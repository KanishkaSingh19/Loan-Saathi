"""Web-call conversation manager: a deterministic state machine around Gemini and our model.

    LLM interprets -> our code validates -> caller confirms -> model scores risk
    -> affordability rule decides the amount.

States (TRANSITIONS lists every allowed move; anything else raises InvalidTransition):

    START -> COLLECTING_PROFILE -> VERIFYING_INFORMATION -> CONFIRMED -> ASSESSING_RISK
          -> CHECKING_AFFORDABILITY -> SHOWING_RESULT -> NEXT_STEPS -> COMPLETED
    plus CORRECTION_REQUIRED (caller changes one answer), ERROR_RECOVERY (we keep failing to
    understand, or the assessment failed), HUMAN_HANDOFF (caller asks for a person) and WHAT_IF
    (reserved for the what-if simulator; nothing enters it yet).

Gemini only ever (1) pulls answers out of what the caller said, each with an exact quote of the
caller's words, (2) phrases the next question, (3) classifies a reply to the summary when our
keyword rules can't, and (4) phrases the final result, whose numbers must match ours. It never
chooses a state, a value it can't quote, an amount or a decision. Every value goes through our
parser and checks:

- each field has a status: "validated" (passed our checks) or "confirmed" (the caller confirmed
  the summary). A value that needs the caller's OK first waits in `pending` ("extracted").
  Assessment refuses to run unless the call is in CONFIRMED and every answer is confirmed
  (`require_confirmed`).
- risky numbers are read back before use: a bare "40" for an income, a range ("40-50 hazaar"),
  two amounts in one answer, an English -teen/-ty word, a value far outside the usual range, or a
  Gemini value that differs from the caller's own words.
- a new value that differs from an earlier one is never silently overwritten: Saathi asks which
  one to use. Corrections from the summary change only that field.
- impossible values (negative, zero income, tenure over 10 years...) are refused with a reason.

If Gemini is unavailable the call carries on with scripted questions and our own parser.
"""
import asyncio
import json
import os
import re
import time
import uuid

import httpx

import llm as llm_mod
import normalize as norm
import pii
import simulate as sim_mod
from scoring import inr

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TIMEOUT = 8.0  # transport limit for one Gemini HTTP call (the router's deadline is shorter)
# One hard deadline per LLM use in a turn (Groq, then Gemini); past it, the deterministic path answers.
LLM_DEADLINE_S = float(os.environ.get("LLM_DEADLINE_S") or 3.0)
PROMPT_VERSION = "2026-11-12"  # part of the LLM cache key: bump when a prompt or schema changes
_LLM_CACHE = llm_mod.ResponseCache()

# ---------- states ----------

START = "START"
COLLECTING = "COLLECTING_PROFILE"
VERIFYING = "VERIFYING_INFORMATION"
CORRECTION = "CORRECTION_REQUIRED"
CONFIRMED = "CONFIRMED"
ASSESSING = "ASSESSING_RISK"
AFFORDABILITY = "CHECKING_AFFORDABILITY"
RESULT = "SHOWING_RESULT"
WHAT_IF = "WHAT_IF"
NEXT_STEPS = "NEXT_STEPS"
COMPLETED = "COMPLETED"
RECOVERY = "ERROR_RECOVERY"
HANDOFF = "HUMAN_HANDOFF"

TRANSITIONS = {
    START: {COLLECTING},
    COLLECTING: {VERIFYING, RECOVERY, HANDOFF},
    VERIFYING: {CONFIRMED, CORRECTION, RECOVERY, HANDOFF},
    CORRECTION: {VERIFYING, COLLECTING, RECOVERY, HANDOFF},
    CONFIRMED: {ASSESSING, RECOVERY},
    ASSESSING: {AFFORDABILITY, RECOVERY},
    AFFORDABILITY: {RESULT, RECOVERY},
    RESULT: {NEXT_STEPS, WHAT_IF, HANDOFF, COMPLETED},
    WHAT_IF: {RESULT, NEXT_STEPS, HANDOFF, COMPLETED},  # "what if I take 2 lakh?"
    NEXT_STEPS: {COMPLETED, HANDOFF, WHAT_IF},
    RECOVERY: {COLLECTING, VERIFYING, HANDOFF, COMPLETED},
    HANDOFF: {COMPLETED},
    COMPLETED: {WHAT_IF},  # back to "what if I take 2 lakh?" after saying goodbye (needs a result)
}
STATES = tuple(TRANSITIONS)

# The product pipeline: LISTEN -> EXTRACT -> VALIDATE -> CONFIRM -> ASSESS -> EXPLAIN -> SIMULATE -> HANDOFF.
# Every state belongs to one stage (stage()); the call screen shows the stage, the state machine stays
# the source of truth.
STAGES = ("LISTEN", "EXTRACT", "VALIDATE", "CONFIRM", "ASSESS", "EXPLAIN", "SIMULATE", "HANDOFF")
STAGE_OF = {
    START: "LISTEN",
    COLLECTING: "EXTRACT",          # VALIDATE while a read-back is waiting (see stage())
    CORRECTION: "VALIDATE",         # a value is being fixed
    VERIFYING: "CONFIRM",
    CONFIRMED: "ASSESS", ASSESSING: "ASSESS", AFFORDABILITY: "ASSESS",
    RESULT: "EXPLAIN",
    WHAT_IF: "SIMULATE",
    NEXT_STEPS: "HANDOFF", HANDOFF: "HANDOFF",
}


def stage(s):
    """Which pipeline stage the call is in."""
    st = s["state"]
    if st == COLLECTING and s.get("pending"):
        return "VALIDATE"   # "I heard ₹40,000, is that right?" / "which amount?"
    if st == RECOVERY:
        return "CONFIRM" if s.get("recover_to") == VERIFYING else "EXTRACT"
    if st == COMPLETED:
        if s.get("advisor_case") or any(t["to"] in (NEXT_STEPS, HANDOFF) for t in s.get("trail", [])):
            return "HANDOFF"
        if any(t["to"] == WHAT_IF for t in s.get("trail", [])):
            return "SIMULATE"
        return "EXPLAIN" if s.get("result") else "EXTRACT"
    return STAGE_OF.get(st, "EXTRACT")
FINISHED = (COMPLETED, HANDOFF)


class InvalidTransition(Exception):
    pass


class NotConfirmed(Exception):
    """Raised when something tries to assess a profile the caller hasn't confirmed."""


def transition(s, to, why=""):
    if to not in TRANSITIONS.get(s["state"], ()):
        raise InvalidTransition(f"{s['state']} -> {to}")
    s["trail"].append({"from": s["state"], "to": to, "why": why, "ts": round(time.time(), 3)})
    s["state"] = to


# ---------- fields ----------

# Question order. `need` = no decision is possible without it, so we never skip it.
FIELDS = [
    {"key": "loan_amount", "need": True,
     "en": "How much loan do you need?", "hi": "Aapko kitne ka loan chahiye?"},
    {"key": "tenure_months",
     "en": "Over how many months or years would you like to repay it? If you're not sure, say 'you decide'.",
     "hi": "Yeh loan aap kitne mahine ya saal mein chukana chahenge? Pakka na ho toh 'aap bataiye' boliye."},
    {"key": "monthly_income", "need": True,
     "en": "Roughly how much do you earn in a month?", "hi": "Aap mahine mein lagbhag kitna kamaate hain?"},
    {"key": "work",
     "en": "What work do you do?", "hi": "Aap kya kaam karte hain?"},
    {"key": "years_in_work",
     "en": "How many years have you been doing this work?", "hi": "Yeh kaam aap kitne saal se kar rahe hain?"},
    {"key": "other_loans_count",
     "en": "Do you have any other loans running right now? If yes, how many?",
     "hi": "Kya abhi koi aur loan chal raha hai? Agar haan, toh kitne?"},
    {"key": "other_loans_emi",
     "en": "What is the total EMI you pay on those loans every month?",
     "hi": "Un sab loans ki total EMI mahine ki kitni jaati hai?"},
    {"key": "other_loans_outstanding",
     "en": "And how much do you still owe in total on those loans?",
     "hi": "Aur un loans par abhi kul kitna chukana baaki hai?"},
    # Repayment history (features.HISTORY_FEATURES, +0.02 AUC). First-time borrowers hear only two of these.
    {"key": "past_loans",
     "en": "Including closed ones, how many loans have you taken in your life? Say 'none' if this is your first.",
     "hi": "Ab tak kul kitne loan liye hain, band ho chuke bhi milakar? Pehli baar hai toh 'koi nahi' boliye."},
    {"key": "ever_refused",
     "en": "Has a loan application of yours ever been refused?",
     "hi": "Kya kabhi aapki koi loan application reject hui hai?"},
    {"key": "ever_late",
     "en": "Have you ever paid an EMI late?", "hi": "Kya aapne kabhi koi EMI der se bhari hai?"},
    {"key": "late_last_year",
     "en": "Was any EMI late in the last 12 months?", "hi": "Kya pichhle 12 mahine mein koi EMI late hui?"},
    {"key": "late_over_month",
     "en": "Were you ever more than a month late on an EMI?",
     "hi": "Kya kabhi koi EMI ek mahine se zyada late hui?"},
    {"key": "overdue_now",
     "en": "Is any EMI overdue right now?", "hi": "Kya abhi koi EMI overdue hai, yaani bharni baaki reh gayi hai?"},
    {"key": "housing",
     "en": "Do you live in your own house, on rent, or with family?",
     "hi": "Aap apne ghar mein rehte hain, kiraye pe, ya parivaar ke saath?"},
    {"key": "age", "en": "And lastly, what is your age?", "hi": "Aur aakhri sawaal, aapki umar kitni hai?"},
]
KEYS = [f["key"] for f in FIELDS]
BY_KEY = {f["key"]: f for f in FIELDS}
EXTRA = ()  # kept for compatibility: every captured field is now asked for
REQUIRED = tuple(f["key"] for f in FIELDS if f.get("need"))
MONEY = ("loan_amount", "monthly_income", "other_loans_emi", "other_loans_outstanding")
NUMERIC = MONEY + ("tenure_months", "other_loans_count", "years_in_work", "age", "past_loans")
HISTORY_FLAGS = ("ever_refused", "ever_late", "late_last_year", "late_over_month", "overdue_now")
HISTORY = ("past_loans",) + HISTORY_FLAGS
FIRST_TIME = re.compile(r"\b(none|never|first|pehli baar|pehla|koi nahi|kabhi nahi|zero)\b|पहली बार|कोई नहीं", re.I)
CRITICAL = MONEY + ("tenure_months", "other_loans_count")  # the financial profile spoken back
CARD = CRITICAL + ("work",)  # shown on the "Here's what I understood" card (income source included)
SUMMABLE = ("monthly_income", "other_loans_emi", "other_loans_outstanding")  # "both" can mean a total
DEFAULT_TENURE = 24
MAX_TRIES = 3
MAX_CORRECTIONS = 6

# (minimum, read back above, refuse above)
LIMITS = {
    "loan_amount": (1_000, 50_00_000, 10_00_00_000),
    "monthly_income": (1_000, 5_00_000, 1_00_00_000),
    "other_loans_emi": (0, None, 1_00_00_000),
    "other_loans_outstanding": (0, None, 1_00_00_00_000),
}

LABELS = {
    "en": {"loan_amount": "requested loan", "tenure_months": "repayment period",
           "monthly_income": "monthly income", "work": "work", "years_in_work": "years in work",
           "other_loans_count": "number of other loans", "other_loans_emi": "existing EMI",
           "other_loans_outstanding": "existing debt", "housing": "housing", "age": "age",
           "past_loans": "number of past loans", "ever_refused": "earlier refusals",
           "ever_late": "late EMIs", "late_last_year": "late EMIs in the last year",
           "late_over_month": "EMIs over a month late", "overdue_now": "overdue EMIs"},
    "hi": {"loan_amount": "loan ki rakam", "tenure_months": "chukane ka samay",
           "monthly_income": "mahine ki kamaai", "work": "kaam", "years_in_work": "kaam ke saal",
           "other_loans_count": "chal rahe loans ki ginti", "other_loans_emi": "maujooda EMI",
           "other_loans_outstanding": "baaki loan", "housing": "ghar", "age": "umar",
           "past_loans": "pichhle loans ki ginti", "ever_refused": "pehle reject hui application",
           "ever_late": "late EMI", "late_last_year": "pichhle saal late EMI",
           "late_over_month": "mahine se zyada late EMI", "overdue_now": "overdue EMI"},
}
TITLES = {"loan_amount": "Requested loan", "tenure_months": "Tenure", "monthly_income": "Monthly income",
          "work": "Work", "years_in_work": "Years in work", "other_loans_count": "Other loans",
          "other_loans_emi": "Existing EMI", "other_loans_outstanding": "Existing debt",
          "housing": "Housing", "age": "Age", "past_loans": "Loans taken before", "ever_refused": "Ever refused",
          "ever_late": "Ever paid an EMI late", "late_last_year": "Late in the last 12 months",
          "late_over_month": "Ever over a month late", "overdue_now": "EMI overdue now"}

HOUSING_WORDS = re.compile(
    r"own|apna|apne|khud|rent|kiray|family|parivaar|parivar|parents|maa|papa|ghar|house|home|flat|"
    r"quarter|government|sarkari|hostel|\bpg\b|घर|किराय|किराए|किराये|कमरा|परिवार|माता|पिता|अपन|क्वार्टर", re.I)
WRONG_WORDS = re.compile(r"\b(galat|wrong|incorrect|mistake|change|badal\w*|sahi nahi|theek nahi)\b|"
                         r"गलत|ग़लत|बदल|सही नहीं", re.I)
FILLER = {"hmm", "hm", "umm", "um", "uh", "haan", "ha", "ji", "acha", "achha", "ok", "okay", "theek", "hai", "yes",
          "no", "nahi", "nahin", "na", "kya", "what", "sorry", "hello", "huh"}
DONT_KNOW = re.compile(
    r"pata nahi|nahi pata|maloom nahi|aap (bataiye|batao|batayein|decide|dekh)|you decide|"
    r"not sure|don'?t know|koi bhi|kuch bhi|jo (theek|sahi) (ho|lage)|whatever|any tenure|पता नहीं", re.I)
YES = re.compile(
    r"\b(haan|haa|han|ha|hanji|ji|jee|yes|yeah|yep|yup|correct|sahi|theek|thik|bilkul|right|confirm|"
    r"confirmed|ok|okay|sure|done|chalega|perfect)\b|हाँ|हां|सही|ठीक|जी", re.I)
NO = re.compile(
    r"\b(nahi|nahin|nai|na|no|nope|galat|wrong|incorrect|change|badlo|badalna|badal|update|mistake|"
    r"not)\b|नहीं|नही|गलत|ग़लत|बदल", re.I)
HUMAN = re.compile(
    r"\b(human|insaan|insan|real person|actual person|customer care|customer support|helpline)\b|"
    r"kisi (insaan|aadmi|vyakti|person) se|(agent|officer|manager|executive|advisor|adviser) se baat|"
    r"(talk|speak) to (a |an |the )?(loan )?(person|human|agent|officer|someone|real|advisor|adviser)|"
    r"\bloan (advisor|adviser)\b|\b(advisor|adviser) (chahiye|se milna|se milwa)|"
    r"इंसान|किसी से बात|एडवाइज़र|एडवाइजर|एडवाइसर|सलाहकार|अधिकारी से बात|एजेंट से बात|व्यक्ति से बात", re.I)
FIRST = re.compile(r"\b(pehla|pehle|pahla|pahle|first|purana|old|earlier)\b|पहल", re.I)
SECOND = re.compile(r"\b(doosra|dusra|doosre|dusre|second|naya|nayi|new|latest|abhi wala|baad wala|later)\b|दूसर|नया", re.I)
BOTH = re.compile(r"\b(total|dono|both|jod|jodo|add|sum|milakar|mila ke)\b|दोनों|कुल", re.I)

# What-if questions after the result ("what if I take 2 lakh?", "agar 5 saal ka loon?").
MAX_EMI = re.compile(r"(max|maximum|zyada se zyada|kitni|kitna|how much).{0,40}\bemi\b|\bemi\b.{0,40}"
                     r"(afford|max|maximum|kitni|kitna|bhar sakta|bhar sakti)|afford", re.I)
LOW_EMI = re.compile(r"\bemi\b.{0,25}\b(kam|less|lower|low|chhoti|kum)\b|\b(kam|lower|less|low|chhoti)\b.{0,12}\bemi\b", re.I)
# A number then a time word: "2 saal", "18 mahine". Not "hazaar mahina" or the "000" of "8,000 mahina":
# after an amount, "mahina" means per month, not a tenure.
TENURE_PHRASE = re.compile(r"((?:(?<![\d,.])\d+(?:\.\d+)?|(?<![a-zऀ-ॿ])(?!(?:hazaar|hazar|lakhs?|lacs?|thousand|crores?|k|"
                           r"हज़ार|हजार|लाख|करोड़)(?![a-zऀ-ॿ]))[a-zऀ-ॿ]+)\s*(?:saal|sal|years?|yrs?|varsh|baras|"
                           r"mahine|mahina|months?|साल|महीने)(?![a-zऀ-ॿ]))", re.I)  # not the "sal" in "salary"
BACK = re.compile(r"\b(original|meri request|wapas|back to|reset)\b", re.I)


def whatif_amounts(text):
    """(distinct loan amounts, bare small numbers) in a what-if, outside any tenure phrase. Two amounts
    ("2 ya 3 lakh") or a bare "40" are asked about, never guessed."""
    rest = TENURE_PHRASE.sub(" ", text or "")
    found = norm.amounts(rest)
    loans = sorted({int(a["value"]) for a in found if a["scaled"] or a["value"] >= 1000})
    bare = [int(a["value"]) for a in found if not a["scaled"] and a["value"] < 1000]
    return loans, bare


def whatif_request(text):
    """(loan, tenure_months) the caller is asking about; either may be None."""
    tenure = None
    for m in TENURE_PHRASE.finditer(text or ""):
        n = norm.months(m.group(1))
        if n and norm.number(m.group(1)) is not None:
            tenure = int(n)
    rest = TENURE_PHRASE.sub(" ", text or "")
    loans = [a["value"] for a in norm.amounts(rest) if a["scaled"] or a["value"] >= 1000]
    return (loans[0] if loans else None), tenure


# Words that point at a field, for corrections and for "actually I earn 60k" mid-call.
# Checked in order: specific words first, plain "loan" last.
MENTIONS = [
    ("other_loans_emi", r"\bemi\b|\bkist|\bkisht|किस्त|installment|instalment"),
    ("other_loans_outstanding", r"\bdebt|outstanding|\bbaaki|\bbaki\b|bakaya|\bowe|\bkarz|कर्ज|बकाया|udhaar|udhar"),
    ("other_loans_count", r"kitne loan|how many loans|number of loans|other loans|aur loan|dusre loan|loans? count"),
    ("monthly_income", r"income|kamaai|kamai|kamaata|kamata|kamaati|kamati|kamaate|salary|tankhwah|tankha|"
                       r"\bearn|aamdani|कमाई|कमाता|आय|तनख्वाह|सैलरी"),
    ("years_in_work", r"experience|kitne saal se|years in work|years of work|years working"),
    ("tenure_months", r"tenure|duration|repay|chukana|chukane|chukaun|अवधि|kitne mahine|how long|loan period"),
    ("age", r"\bage\b|\bumar|\bumr|उम्र|years old"),
    ("housing", r"\bhouse|\bghar\b|\brent|kiraya|kiraye|housing|घर|किराय"),
    ("work", r"\bwork\b|\bjob\b|\bkaam\b|naukri|occupation|business|employment|rozgar|profession|काम|नौकरी"),
    ("loan_amount", r"loan amount|requested|request|maanga|manga|naya loan|new loan|\bloan\b|लोन|amount|rakam"),
]
MENTIONS = [(k, re.compile(p, re.I)) for k, p in MENTIONS]

# ---------- what Saathi says ----------

# Data protection (India's DPDP Act): answers are stored only with the caller's yes, and deleted after
# RETENTION_DAYS (server.purge_expired). Without consent the call still works, in memory only.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS") or 30)
PRIVACY = {
    "hi": {True: f"Aapke jawaab sirf is jaanch ke liye {RETENTION_DAYS} din tak save rahenge, phir mita diye "
                 "jaayenge; 'save mat karo' bolkar aap ise rok sakte hain. ",
           False: "Abhi aapke jawaab save nahi ho rahe. "},
    "en": {True: f"Your answers are kept for {RETENTION_DAYS} days for this check, then deleted; say "
                 "'don't save' to stop that. ",
           False: "Your answers aren't being saved. "},
}
NO_SAVE = re.compile(r"save\s*(?:mat|na|nahi)\s*kar|mat\s*save|don'?t\s+save|do\s+not\s+save|"
                     r"delete\s+my\s+(?:data|answers)|data\s+mat\s+rakho|(?:don'?t|do not)\s+store", re.I)
SAVE_OK = re.compile(r"\bsave\s+kar\s*(?:o|do|lo|dijiye|dena)\b|\bsave\s+my\s+answers\b|\byou\s+can\s+save\b", re.I)

GREETING = {
    "hi": "Namaste! Main Loan Saathi hoon. Main kuch aasaan sawaal poochkar bataunga ki aapko kitna loan "
          "mil sakta hai. Yeh sirf ek pre-screening hai, final approval nahi. ",
    "en": "Hello! I'm Loan Saathi. I'll ask a few simple questions and tell you how much loan you could get. "
          "This is only a pre-screening, not a final approval. ",
}
CLARIFY = {
    "other_loans_emi": {
        "hi": "Theek hai, maine loan ki rakam note kar li. Ab bataiye, in sab loans ki mahine ki kist, "
              "yaani EMI, kul milakar kitni jaati hai?",
        "en": "Got it, I've noted the loan amounts. Now, how much do you pay in total each month as EMI "
              "on those loans?",
    },
}
SAY = {
    "sorry": {"hi": "Maaf kijiye, main samajh nahi paaya. ", "en": "Sorry, I couldn't understand that. "},
    "repeat_short": {"hi": "Kya aap apni {label} phir se bata sakte hain? ", "en": "Could you repeat your {label}? "},
    "repeat": {"hi": "Kya aap apni {label} phir se bata sakte hain? Jaise '40 hazaar' ya '₹40,000'.",
               "en": "Could you repeat your {label}? For example, '40 thousand' or '₹40,000'."},
    "glitch": {"hi": "Maaf kijiye, mere taraf kuch gadbad hui. Kya aap apni pichhli baat phir se bata sakte hain?",
               "en": "Sorry, something went wrong on my side. Could you say that again?"},
    "low_emi_noted": {"hi": "Theek hai, EMI kam rakhni hai. Result ke baad main kam EMI wale vikalp dikhaunga. ",
                      "en": "Okay, you'd like a low EMI. After the result I'll show you lower-EMI options. "},
    "again": {"hi": "Theek hai, phir se bataiye. ", "en": "Okay, please tell me again. "},
    "noted": {"hi": "Theek hai. ", "en": "Okay. "},
    "updated": {"hi": "Theek hai, maine {label} {value} kar di. ", "en": "Okay, I've changed your {label} to {value}. "},
    "yes_no": {"hi": " Kripya haan ya nahi boliye.", "en": " Please say yes or no."},
    "confirm_number": {"hi": "{label} ke liye maine {value} suna. Kya yeh sahi hai?",
                       "en": "For your {label} I heard {value}. Is that right?"},
    "confirm_scale": {"hi": "{label} ke liye maine sirf {raw} suna. Kya aapka matlab {value} hai?",
                      "en": "For your {label} I only heard {raw}. Did you mean {value}?"},
    "choose_multiple": {"hi": "{label} ke liye maine do rakam suni: {a} aur {b}. Kaunsi sahi hai?",
                        "en": "I heard two amounts for your {label}: {a} and {b}. Which one should I use?"},
    "choose_total": {"hi": " Agar dono milakar hai, toh 'total' boliye.", "en": " If it's both together, say 'total'."},
    "contradiction": {"hi": "Mere paas {label} ki do alag baatein hain: {a} aur {b}. Kaunsi sahi hai?",
                      "en": "I have two different {label} values: {a} and {b}. Which should I use?"},
    "loans_conflict": {"hi": "Pehle aapne bataya ki koi aur loan nahi hai, par ab aapne {label} {value} bataayi. "
                             "Kya abhi koi loan chal raha hai?",
                       "en": "Earlier you said you have no other loans, but now you mentioned an {label} of "
                             "{value}. Do you have other loans running right now?"},
    "summary_intro": {"hi": "Maine yeh samjha: ", "en": "Here's what I understood: "},
    "summary_ask": {"hi": " Kya yeh sab sahi hai? 'Haan' boliye, ya bataiye kya badalna hai.",
                    "en": " Is this information correct? Say yes, or tell me what to change."},
    "verify_unclear": {"hi": "Agar sab sahi hai toh 'haan' boliye, ya bataiye kaunsi jaankari badalni hai.",
                       "en": "If everything is right, say yes. Otherwise tell me what to change."},
    "which_field": {"hi": "Kaunsi jaankari galat hai? Jaise loan ki rakam, kamaai, EMI, baaki loan ya chukane ka samay.",
                    "en": "Which detail is wrong? For example the loan amount, income, EMI, debt or tenure."},
    "ask_correct": {"hi": "Sahi {label} kya hai?", "en": "What is the correct {label}?"},
    "emi_over_income": {"hi": "Aapki EMI {emi} aapki kamaai {income} se zyada ho rahi hai. ",
                        "en": "Your EMI of {emi} is more than your income of {income}. "},
    "recovery": {"hi": "Maaf kijiye, mujhe theek se samajh nahi aa raha. Aap jawab type bhi kar sakte hain. "
                       "Kya hum phir se koshish karein, ya main aapka summary loan advisor ke liye save kar doon?",
                 "en": "Sorry, I'm having trouble understanding. You can also type your answer. "
                       "Shall we try again, or shall I save your summary for a loan advisor?"},
    "whatif_later": {"hi": "Alag rakam ya samay ka asar main jaanch ke baad dikha sakta hoon. Pehle bataiye, kya "
                           "upar padhi gayi jaankari sahi hai? 'Haan' boliye, ya bataiye kya badalna hai.",
                     "en": "I can show other amounts or tenures right after the assessment. First, is the information "
                           "I read out correct? Say yes, or tell me what to change."},
    "wa_unheard": {"hi": "Maaf kijiye, aapka voice note saaf sunai nahi diya. Kripya 30 second se chhota "
                          "voice note bhejiye, ya likh kar bataiye.",
                    "en": "Sorry, I couldn't hear that voice note clearly. Please send one under 30 seconds, "
                          "or type your answer."},
    "wa_unsupported": {"hi": "Main sirf text aur voice note samajh sakta hoon. Kripya likh kar ya bol kar bataiye.",
                       "en": "I can only read text and voice notes. Please type or record your answer."},
    "whatif_which": {"hi": "Maine do rakam suni: {options}. Kaunsi rakam dekhni hai? Ek hi rakam boliye.",
                     "en": "I heard two amounts: {options}. Which one should I simulate? Please say one amount."},
    "whatif_bare": {"hi": "Maine sirf '{n}' suna. Poori rakam boliye, jaise '{n} hazaar' ya '2 lakh'.",
                    "en": "I only heard '{n}'. Please say the full amount, like '{n} thousand' or '2 lakh'."},
    "whatif_more": {"hi": " Koi aur rakam ya samay try karna hai? Ya 'aage ke steps' boliye.",
                    "en": " Want to try another amount or tenure? Or say 'next steps'."},
    "whatif_unclear": {"hi": "Aap koi rakam ya samay boliye, jaise 'agar 2 lakh loon' ya '5 saal mein'. "
                             "Ya 'aage ke steps' boliye.",
                       "en": "Say an amount or a tenure, like 'what if I take 2 lakh' or 'over 5 years'. "
                             "Or say 'next steps'."},
    "max_emi": {"hi": "Aapki anumanit affordability ke hisaab se naye loan ki EMI lagbhag {cap} mahina tak ho sakti hai, "
                      "taaki saari EMI milakar kamaai ke aadhe se kam rahein. Yeh anumaan hai, lender ka offer nahi.",
                "en": "Your estimated affordable EMI for a new loan is about {cap} a month, so that all your EMIs "
                      "stay within half of your income. This is an estimate, not a lender offer."},
    "no_room": {"hi": "Aapki maujooda EMI pehle se kamaai ka aadha ya usse zyada hai, isliye naye loan ki EMI ki "
                      "gunjaish abhi nahi dikhti.",
                "en": "Your existing EMIs already take half your income or more, so there is no room for a new "
                      "EMI right now."},
    "safety_failed": {"hi": "Jaanch se pehle ek baat saaf karni hai. Kya hum details phir se dekhein?",
                      "en": "Before I can check, one thing needs clearing up. Shall we go over the details again?"},
    "assess_failed": {"hi": "Maaf kijiye, jaanch karte waqt kuch technical dikkat aa gayi. Kya main phir se koshish karoon?",
                      "en": "Sorry, something went wrong while checking. Shall I try again?"},
    "wa_consent": {"hi": f"Kya hum aapke jawaab {RETENTION_DAYS} din ke liye save kar sakte hain, taaki zaroorat "
                          "pade toh loan advisor aage madad kar sake? Dono tarah se baat jaari rahegi.",
                    "en": f"May we keep your answers for {RETENTION_DAYS} days, so a loan advisor can follow up if "
                          "needed? Either way, we can carry on."},
    "handoff_done": {"hi": "Aapka summary pehle hi loan advisor ke liye case {case} mein save hai. Dhanyavaad!",
                     "en": "Your summary is already saved for a loan advisor as case {case}. Thank you!"},
    "lang_switched": {"hi": "Theek hai, ab main Hinglish mein baat karunga.",
                      "en": "Sure, I'll speak in English."},
    "consent_yes": {"hi": f"Theek hai, aapke jawaab {RETENTION_DAYS} din ke liye save rahenge.",
                    "en": f"Okay, your answers will be kept for {RETENTION_DAYS} days."},
    "consent_no": {"hi": "Theek hai, aapke jawaab save nahi honge.",
                   "en": "Okay, your answers won't be saved."},
    "handoff": {"hi": "Theek hai. Maine aapki baatcheet ka summary loan advisor ke liye case {case} mein save kar "
                      "diya hai. Yeh prototype hai, isliye kisi advisor ko apne aap suchna nahi jaati. Dhanyavaad!",
                "en": "Okay. I've saved a summary of our conversation for a loan advisor as case {case}. This is a "
                      "prototype, so no advisor is notified automatically. Thank you!"},
    "handoff_notified": {"hi": "Theek hai. Maine aapki baatcheet ka summary case {case} mein save karke loan advisor ko "
                               "suchna bhej di hai. Yeh sirf pre-screening hai; advisor aapka case dekhenge. Dhanyavaad!",
                         "en": "Okay. I've saved a summary of our conversation as case {case} and notified a loan "
                               "advisor. This is still only a pre-screening; the advisor will review your case. "
                               "Thank you!"},
    "handoff_failed": {"hi": "Maaf kijiye, abhi advisor ke liye summary save nahi ho paaya. Thodi der baad phir "
                             "koshish kijiye.",
                       "en": "Sorry, I couldn't save the summary for an advisor just now. Please try again in a "
                             "moment."},
    "too_many_changes": {"hi": "Lagta hai kaafi baatein badal rahi hain, isliye behtar hoga ki ek loan advisor aapse "
                               "seedhe baat karein. ",
                         "en": "Quite a few details have changed, so it's best if a loan advisor talks to "
                               "you directly. "},
    "bye": {"hi": "Koi baat nahi. Jab chahein dobara baat kijiye. Dhanyavaad!",
            "en": "No problem. Talk to me again any time. Thank you!"},
    "finished": {"hi": "Aapka pre-screening ho gaya hai. Dhanyavaad!", "en": "Your pre-screening is complete. Thank you!"},
    # After the call: what the caller can still do (shown as buttons too), instead of a dead end.
    "after_options_result": {"hi": " Aap chahein toh alag rakam ya samay ka asar dekh sakte hain, loan advisor se baat "
                                   "kar sakte hain, ya naya check shuru kar sakte hain.",
                             "en": " If you like, you can try a different amount or time, talk to a loan advisor, "
                                   "or start a new check."},
    "after_options": {"hi": " Aap chahein toh loan advisor se baat kar sakte hain, ya naya check shuru kar sakte hain.",
                      "en": " If you like, you can talk to a loan advisor or start a new check."},
    "after_options_saved": {"hi": " Aap chahein toh naya check shuru kar sakte hain.",
                            "en": " If you like, you can start a new check."},
    "whatif_prompt": {"hi": "Zaroor. Koi rakam ya samay boliye, jaise 'agar 2 lakh loon' ya '3 saal mein'.",
                      "en": "Sure. Tell me an amount or a time, for example 'what if I take 2 lakh' or 'over 3 years'."},
    "new_call": {"hi": "Theek hai, naya check shuru karte hain.", "en": "Okay, let's start a new check."},
    "more": {"hi": " Aur kuch poochna ho, ya loan advisor se baat karni ho, toh bataiye.",
             "en": " If you have another question or want to talk to a loan advisor, just say so."},
}
PROBLEMS = {
    "negative": {"hi": "{label} minus mein nahi ho sakti. ", "en": "Your {label} can't be negative. "},
    "too_small": {"hi": "{label} kam se kam {min} honi chahiye. ", "en": "The {label} needs to be at least {min}. "},
    "too_large": {"hi": "{value} {label} ke liye bahut zyada lagta hai. ", "en": "{value} looks too large for your {label}. "},
    "tenure_range": {"hi": "Loan 3 mahine se 10 saal ke beech chukaya ja sakta hai. ",
                     "en": "The loan can be repaid over 3 months to 10 years. "},
    "age_range": {"hi": "Is pre-screening ke liye umar 18 se 100 saal ke beech honi chahiye. ",
                  "en": "This pre-screening is for ages 18 to 100. "},
}
RESULT_FOLLOWUP = {
    "pre_approved": {"hi": " Kya main aage ke steps bataun? Aap 'agar 2 lakh loon' jaise sawaal bhi pooch sakte hain.",
                     "en": " Shall I tell you the next steps? You can also ask 'what if' with another amount or tenure."},
    "needs_review": {"hi": " Kya main aage ke steps bataun? Aap 'agar 2 lakh loon' jaise sawaal bhi pooch sakte hain.",
                     "en": " Shall I tell you the next steps? You can also ask 'what if' with another amount or tenure."},
    "not_eligible_now": {"hi": " Kya main bataun ki aage kya kar sakte hain?", "en": " Shall I tell you what could help?"},
    "counter_offer": {"hi": "", "en": ""},  # the result already asks "would that work?"
}

# ---------- Gemini prompts ----------

SYSTEM_PROMPT = """You are Loan Saathi, a warm, patient voice assistant that pre-screens first-time
borrowers in India for small loans. Callers speak Hindi, English or Hinglish, often informally,
and their words come from speech recognition, so expect errors and Devanagari or Latin script.

You get the conversation so far, the answers already collected, the field our system wants
next, and the caller's latest words. Do two things:

1. ANSWERS: list every field the caller states in `caller_said` (this turn only), including a new
   value for a field already collected (the caller may be changing it). Callers often
   give several answers in one sentence: "50 hazaar chahiye, sabzi ki dukaan hai, 25 hazaar kamata
   hoon" gives THREE answers (loan_amount 50000, work, monthly_income 25000). For each answer give
   `quote`: the exact words from `caller_said` that state it, copied character for character.
   If you cannot quote the caller's words for a field, they did not say it: leave it out.
   Never fill a field from guesswork, typical values or the question that was asked.
   Values:
   loan_amount, monthly_income, other_loans_emi, other_loans_outstanding: rupees as plain digits
     ("pachas hazaar" -> "50000", "dhai lakh" -> "250000", "15k" -> "15000"). monthly_income: convert
     a yearly or daily figure to monthly (daily x 26).
   work: a short English description ("vegetable seller", "salaried driver", "retired",
     "student", "unemployed", "government job", "own kirana shop").
   years_in_work, age: plain number of years. "6 saal se dukaan hai" / "for 6 years" -> years_in_work "6"
     (that is how long they have worked, never the repayment period).
   other_loans_count: plain number; "nahi" / "koi loan nahi" -> "0"; "ek bike loan hai" -> "1".
   A single message can answer several fields: return every one the caller states, whatever was asked.
   other_loans_emi is ONLY the monthly installment (kist) they pay on other loans each month.
   other_loans_outstanding is the size of those loans or what is still owed on them ("13 lakh ka
     loan hai"); if they name several, add them up. Never put a loan's size into other_loans_emi:
     if they gave loan sizes but no monthly installment, other_loans_emi is not answered yet.
   housing: one of "own house", "rented", "with family", "government quarters".
   tenure_months: how long they want to repay, in months ("2 saal" -> "24").
   past_loans: how many loans they have EVER taken, closed ones included, as a number ("never" -> "0").
   ever_refused, ever_late, late_last_year, late_over_month, overdue_now: "yes" or "no" only:
     whether a loan application was ever refused; whether they ever paid an EMI late; late in the last
     12 months; ever more than a month late; an EMI overdue right now. If they say they don't know,
     leave the field out.

2. REPLY: one or two short spoken sentences. Briefly acknowledge what they said, then ask
   about the NEXT field: the first one in `still_needed` (it is in order) that the caller has not
   just answered, skipping years_in_work if they are retired, a student or unemployed, and
   skipping other_loans_emi and other_loans_outstanding if they have no other loans. Ask it in
   natural words; the `question_hints` show what each field means. If the caller asked
   something, answer it in one sentence first, but never promise approval, quote interest rates,
   or mention any score or model. If nothing is left to ask, set asking to "done" and do not ask
   anything: just say "Thank you." in their language.
   Reply in `reply_language` (our system decides it from the caller's words). Hinglish means Hindi
   in Latin script. No emojis, no lists.

Set "asking" to exactly the field you asked about in the reply (or "done").
Set "language" to "hi" (Hindi or Hinglish) or "en" (English)."""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": KEYS},
                    "value": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["field", "value", "quote"],
            },
        },
        "reply": {"type": "string"},
        "asking": {"type": "string"},
        "language": {"type": "string", "enum": ["hi", "en"]},
    },
    "required": ["answers", "reply", "asking", "language"],
}

INTENT_PROMPT = """You classify one reply from a loan pre-screening caller in India (Hindi, English or
Hinglish, from speech recognition). Saathi just read back the caller's details and asked if they are
correct. Return:
intent: "confirm" if the caller says everything is correct; "correct" if they say something is
  wrong or want to change something; "unclear" otherwise.
field: the detail they want to change, if they name one, else "none".
Only classify. Do not invent values."""

INTENT_SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": ["confirm", "correct", "unclear"]},
                   "field": {"type": "string", "enum": KEYS + ["none"]}},
    "required": ["intent", "field"],
}

RESULT_PROMPT = """You are Loan Saathi, speaking to a caller on a voice call about a loan pre-screening.
Turn the guidance below into what you say to the caller: 2 to 4 short spoken sentences, warm and clear.
LANGUAGE: {language_rule}
NUMBERS: write every amount, EMI and tenure in DIGITS exactly as in the guidance (for example
"₹50,000" or "50,000 rupaye", "24 mahine"). Never write numbers as words ("fifty thousand",
"pachas hazaar"). Do not add amounts, rates, promises or reasons that are not in the guidance.
Never mention scores, percentages, models or AI. No emojis, no lists."""

LANGUAGE_RULES = {
    "hi": "Hinglish: Hindi written in Latin script (\"Aapka loan ... mumkin lagta hai\"), even though the "
          "guidance is in English. Do not answer in English.",
    "en": "Simple Indian English.",
}

RESULT_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}


def _plain(text):
    """Lowercase, punctuation-free, single-spaced, for comparing a quote with what was said."""
    return " ".join(re.sub(r"[^\wऀ-ॿ₹]+", " ", str(text).lower()).split())


def quoted(quote, said):
    """True if Gemini's quote really is in the caller's words (guards against invented answers)."""
    q = _plain(quote)
    return bool(q) and q in _plain(said)


# ---------- validation ----------

def _none_said(v):
    """'never taken one', 'koi nahi, ek bhi nahi': a negation with no digits means zero, not 'one'."""
    return bool(FIRST_TIME.search(str(v))) and not re.search(r"\d", str(v))


def valid(key, value):
    """The normalised value if our parser understands it and it is in range, else None."""
    if value in (None, ""):
        return None
    v = str(value)
    if key in MONEY and norm.negative(v):
        return None
    if key in ("loan_amount", "monthly_income"):
        n = norm.number(v)
        return n if n and n > 0 else None
    if key in ("other_loans_emi", "other_loans_outstanding"):
        n = norm.number(v)
        if n is None and norm.boolean(v) is False:
            n = 0
        return n if n is not None and n >= 0 else None
    if key == "other_loans_count":
        n = norm.number(v)
        if (n is None and norm.boolean(v) is False) or _none_said(v):
            n = 0
        return int(n) if n is not None and 0 <= n < 50 else None
    if key == "years_in_work":
        n = norm.years(v)
        return n if n is not None and 0 <= n < 70 else None
    if key == "age":
        n = norm.number(v)
        return int(n) if n and 18 <= n <= 100 else None
    if key == "work":
        # Free text, so almost anything is a description; never "hmm", "pata nahi" or a bare yes/no.
        words = re.sub(r"[^\w\s]", " ", str(v).lower()).split()
        if not norm.work_known(v) and (not words or DONT_KNOW.search(str(v)) or all(w in FILLER for w in words)
                                       or (len(words) <= 2 and norm.boolean(v) is not None)):
            return None
        income_type, _ = norm.work(v)
        return v if income_type else None
    if key == "housing":
        return v if HOUSING_WORDS.search(v) else None  # norm.housing() defaults on any input
    if key == "tenure_months":
        n = norm.months(v)
        return n if n and 3 <= n <= 120 else None
    if key == "past_loans":
        n = norm.number(v)
        if (n is None and (FIRST_TIME.search(v) or norm.boolean(v) is False)) or _none_said(v):
            n = 0
        return int(n) if n is not None and 0 <= n <= 50 else None
    if key in HISTORY_FLAGS:
        b = norm.boolean(v)
        return None if b is None else int(b)
    return None


def check(s, key, raw, words):
    """Our verdict on one heard value: ("ok", v), ("confirm", v, how), ("choose", [a, b]) or
    ("invalid", code, v). `raw` is the value (Gemini's or the caller's text); `words` is what the
    caller actually said for it."""
    words = words or str(raw)
    if key in NUMERIC and (norm.negative(words) or norm.negative(raw)):
        return ("invalid", "negative", None)
    if key in MONEY:
        # "6 lakh for 2 years": the tenure's number is not a second amount.
        heard = norm.amounts(TENURE_PHRASE.sub(" ", str(words)))
        if any(a["value"] >= 1000 for a in heard):
            heard = [a for a in heard if a["value"] >= 100 or a["scaled"]]
        strong = [a for a in heard if a["digits"] or a["scaled"]] or heard
        # An over-wide quote ("3 lakh chahiye, 40,000 kamata hoon" for the loan) is not ambiguous when
        # the other amount belongs to another field the caller stated in the same breath.
        others = [v for f, v in (s.get("claims") or {}).items() if f != key and v]
        strong = [a for a in strong if not any(abs(a["value"] - o) <= 0.5 for o in others)] or strong
        distinct = []
        for a in strong:
            if all(abs(a["value"] - d) > 0.5 for d in distinct):
                distinct.append(a["value"])
        if len(distinct) > 1:
            return ("choose", distinct[:2])
        n = valid(key, raw)
        if str(raw) == str(words) and len(distinct) == 1 and n is not None:
            n = distinct[0]  # the caller's own words: use the amount we picked out, not just the first number
        if n is None:
            if norm.number(raw) == 0 and key in ("loan_amount", "monthly_income"):
                return ("invalid", "too_small", 0)
            return ("invalid", "unclear", None)
        lo, soft, hard = LIMITS[key]
        own = strong[0] if strong else None
        if own and abs(own["value"] - n) > 0.5:
            return ("confirm", n, "number")  # Gemini's figure isn't what the caller's words say
        small = 1000 if key in ("loan_amount", "monthly_income") else 100
        if own and not own["scaled"] and 0 < n < small and key not in s["literal_ok"]:
            return ("confirm", n * 1000, "scale")  # "40" for an income almost always means 40 hazaar
        if n < lo:
            return ("invalid", "too_small", n)
        if n > hard:
            return ("invalid", "too_large", n)
        if norm.TEEN_TY.search(words) or (soft and n > soft):
            return ("confirm", n, "number")
        return ("ok", n)
    if key == "tenure_months":
        n = norm.months(raw)
        own = norm.months(words)
        # The caller's own words win when they name the unit ("six months" is 6 even if Gemini sent "6").
        if own is not None and re.search(r"month|mahin|महीन|saal|sal\b|year|varsh|baras|साल", str(words), re.I):
            n = own
        if n is None:
            return ("invalid", "unclear", None)
        if not 3 <= n <= 120:
            return ("invalid", "tenure_range", n)
        if own is not None and own != n:
            return ("confirm", n, "number")
        return ("ok", n)
    if key == "age":
        n = norm.number(raw)
        if n is not None and not 18 <= n <= 100:
            return ("invalid", "age_range", n)
    n = valid(key, raw)
    if n is None:
        return ("invalid", "unclear", None)
    return ("ok", raw if key in ("work", "housing") else n)


def _same(key, a, b):
    if key in ("work", "housing"):
        return True  # free text: the latest description wins
    return abs(float(a) - float(b)) <= max(0.5, 0.01 * abs(float(a)))


def fmt(key, v, lang="en"):
    if key in MONEY:
        return inr(v)
    if key == "tenure_months":
        n = int(v)
        return f"{n} mahine" if lang == "hi" else f"{n} months"
    if key in ("age", "years_in_work"):
        n = round(float(v), 1)
        n = int(n) if n == int(n) else n
        return f"{n} saal" if lang == "hi" else f"{n} years"
    if key in ("other_loans_count", "past_loans"):
        return str(int(v))
    if key in HISTORY_FLAGS:
        return ("Haan" if v else "Nahi") if lang == "hi" else ("Yes" if v else "No")
    if key == "work":
        return norm.work_label(v, lang)  # the category, read back; the caller's own words stay in s["said"]
    return str(v)


def mentioned_field(text):
    for key, pat in MENTIONS:
        if pat.search(text or ""):
            return key
    return None


def _skip(key, answers):
    """Fields that don't apply given earlier answers."""
    if key == "years_in_work" and answers.get("work"):
        income_type, _ = norm.work(answers["work"])
        return income_type in ("Pensioner", "Unemployed", "Student")
    if key in ("other_loans_emi", "other_loans_outstanding"):
        return valid("other_loans_count", answers.get("other_loans_count")) == 0
    # The history funnel: no loans ever -> no lateness questions; "never late" -> no follow-ups;
    # nothing running now -> nothing can be overdue.
    never_borrowed = answers.get("past_loans") == 0 and answers.get("other_loans_count") in (0, None)
    if key == "ever_late":
        return never_borrowed
    if key in ("late_last_year", "late_over_month"):
        return never_borrowed or answers.get("ever_late") == 0
    if key == "overdue_now":
        return answers.get("other_loans_count") == 0 or never_borrowed
    return False


ASK_HISTORY = True  # the repayment-history questions; older test suites switch them off to keep their flows
# Short call: the questions the decision rules read come first (loan, tenure, income, other loans); the ones
# only the risk model reads (work, years, history, housing, age) are asked only while some answer to them
# could still move the risk band. Older scripted suites switch it off to keep the full fixed order.
SHORT_CALL = os.environ.get("SHORT_CALL", "1").strip() != "0"
RULE_FIELDS = ("loan_amount", "tenure_months", "monthly_income", "other_loans_count", "other_loans_emi",
               "other_loans_outstanding")
# Follow-ups and the question they follow ("how many years in this work?" needs the work first).
PARENT = {"years_in_work": "work", "ever_late": "past_loans", "late_last_year": "ever_late",
          "late_over_month": "ever_late", "overdue_now": "past_loans"}
# Answers a caller could give to each model-only question, spanning its range (and "not answered").
PROBES = {
    "work": ["kirana dukaan", "private naukri", "driver", "kheti", "dihadi mazdoori", "sarkari naukri", "retired",
             "berozgar", "student", "teacher", "security guard"],
    "years_in_work": ["3 mahine", "1 saal", "3 saal", "8 saal", "20 saal"],
    "past_loans": ["0", "1", "3", "8"],
    "ever_refused": ["haan", "nahi"], "ever_late": ["haan", "nahi"], "late_last_year": ["haan", "nahi"],
    "late_over_month": ["haan", "nahi"], "overdue_now": ["haan", "nahi"],
    "housing": ["apna ghar", "kiraye pe", "parivaar ke saath", "sarkari quarter"],
    "age": ["18", "21", "26", "35", "45", "58", "70"],
}


def _open(s, k):
    return not ((not ASK_HISTORY and k in HISTORY) or k in s["answers"] or k in s["skipped"]
                or _skip(k, s["answers"]))


def next_field(s):
    if not SHORT_CALL:
        return next((f["key"] for f in FIELDS if _open(s, f["key"])), "done")
    rule = [k for k in KEYS if k in RULE_FIELDS and _open(s, k)]
    if rule:
        return rule[0]
    return _model_question(s)


def _band_probe(s, remaining):
    """For each remaining model-only field: the risk band with each possible answer (others unanswered), and
    the band with every field at its riskiest and at its safest answer together. None if we can't score."""
    build = s.get("_build_applicant")
    if build is None:
        return None
    import scoring
    base = {k: str(v) for k, v in s["answers"].items()}
    tries = [(k, v) for k in remaining for v in PROBES.get(k, [])]
    # Two model calls in all: every single answer (and none) in one batch, then all-riskiest / all-safest.
    ps = scoring.risk_probabilities([build(base)] + [build({**base, k: v}) for k, v in tries])
    p_now, by_field = ps[0], {}
    for (k, v), pk in zip(tries, ps[1:]):
        by_field.setdefault(k, []).append((pk, v))
    worst = {k: max(t)[1] for k, t in by_field.items()}
    best = {k: min(t)[1] for k, t in by_field.items()}
    swing = {k: {scoring.band_of(x) for x, _ in t} | {scoring.band_of(p_now)} for k, t in by_field.items()}
    p_worst, p_best = scoring.risk_probabilities([build({**base, **worst}), build({**base, **best})])
    bands = {scoring.band_of(p_now), scoring.band_of(p_worst), scoring.band_of(p_best)}.union(*swing.values())
    return {"bands": bands, "swing": swing}


def _model_question(s):
    """The next model-only question, or "done" once no answer to any of them could move the risk band. Those
    are then recorded as not needed (skipped, so the model reads them as not answered)."""
    remaining = [k for k in KEYS if k not in RULE_FIELDS and _open(s, k)]
    if not remaining:
        return "done"
    key = json.dumps([sorted((k, str(v)) for k, v in s["answers"].items()), remaining])
    cached = s.get("_probe_cache")
    if cached and cached[0] == key:
        probe = cached[1]
    else:
        try:
            probe = _band_probe(s, remaining)
        except Exception as e:  # noqa: BLE001  (never lose a call over the shortcut: ask everything)
            print(f"[converse {s['id']}] band probe failed, asking every question: {type(e).__name__}: {e}",
                  flush=True)
            probe = None
        s["_probe_cache"] = (key, probe)
    if probe is None:
        return remaining[0]
    if len(probe["bands"]) == 1:
        s.setdefault("not_needed", [])
        for k in remaining:
            s["skipped"].add(k)
            if k not in s["not_needed"]:
                s["not_needed"].append(k)
        return "done"
    # The first remaining question (in the usual order) whose answer alone can move the band; if none can
    # alone but together they could, the first one left. A follow-up never comes before its question.
    k = next((k for k in remaining if len(probe["swing"].get(k, ())) > 1), remaining[0])
    while PARENT.get(k) in remaining:
        k = PARENT[k]
    return k


# ---------- Gemini ----------

async def _gemini(api_key, model, system, user, schema):
    """One structured call. Returns the parsed JSON object, or raises."""
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "temperature": 0.2,
            "maxOutputTokens": 400,
            "thinkingConfig": {"thinkingLevel": "MINIMAL"},
        },
    }
    async def post():
        async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT) as client:
            return await client.post(GEMINI_URL.format(model=model), json=body, headers={"x-goog-api-key": api_key})

    # httpx's timeout is per phase (connect, each read...), so a slow, trickling reply could hold a voice
    # turn for minutes (seen in a dry run). This is a hard deadline for the whole call.
    try:
        r = await asyncio.wait_for(post(), timeout=GEMINI_TIMEOUT)
    except asyncio.TimeoutError:
        raise TimeoutError(f"Gemini took longer than {GEMINI_TIMEOUT:.0f}s") from None
    if not r.is_success:
        raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]
                       if not p.get("thought"))
    except (KeyError, IndexError, TypeError):
        raise llm_mod.InvalidJSON("Gemini reply had no content") from None
    return llm_mod.parse_json(text)


# ---------- the LLM, through the provider router ----------

def _router(env):
    """The server's router (Groq then Gemini), or, for direct callers and tests, Gemini alone."""
    if env.get("llm") is not None:
        return env["llm"]
    if env.get("api_key"):
        return llm_mod.Router([llm_mod.GeminiProvider(env["api_key"], env.get("model", ""),
                                                      send=lambda *a: _gemini(*a))],
                              deadline_s=LLM_DEADLINE_S, cache=_LLM_CACHE)
    return None


def _llm_available(env):
    r = _router(env)
    return bool(r and r.available)


async def _llm(s, env, purpose, system, user, schema):
    """One LLM use: (JSON object, provider). At most one per turn, so a turn never waits on two LLM calls
    in a row; a second use raises LLMUnavailable and the caller takes the deterministic path. Every
    attempt is written to the call's llm_trail (provider, purpose, milliseconds)."""
    if s.get("_llm_used_this_turn"):
        s.setdefault("llm_trail", []).append({"ts": round(time.time(), 3), "state": s["state"], "purpose": purpose,
                                              "provider": "fallback", "ms": 0, "why": "one LLM call per turn"})
        raise llm_mod.LLMUnavailable("one LLM call per turn")
    s["_llm_used_this_turn"] = True
    t0 = time.monotonic()
    entry = {"ts": round(time.time(), 3), "state": s["state"], "purpose": purpose}
    # Personal identifiers (phone, Aadhaar, PAN, account numbers, a name...) never reach a provider; the trail
    # records how many of each kind were masked, never the values.
    system, _ = pii.mask(system)
    user, masked = pii.mask(user)
    if masked:
        entry["masked"] = masked
    try:
        out, provider = await _router(env).call(system, user, schema, prompt_version=PROMPT_VERSION)
    except Exception as e:
        s.setdefault("llm_trail", []).append({**entry, "provider": "fallback",
                                              "ms": round((time.monotonic() - t0) * 1000),
                                              "why": f"{type(e).__name__}: {str(e)[:160]}"})
        raise
    s.setdefault("llm_trail", []).append({**entry, "provider": provider,
                                          "ms": round((time.monotonic() - t0) * 1000)})
    return out, provider


def _transcript(s, limit=10):
    return "\n".join(f"{who}: {text}" for who, text in s["history"][-limit:])


# ---------- sessions ----------

SESSIONS = {}
SESSION_TTL = 30 * 60
MAX_SESSIONS = 500


def _prune():
    now = time.time()
    for sid in [k for k, s in SESSIONS.items() if now - s["updated"] > SESSION_TTL]:
        SESSIONS.pop(sid, None)
    while len(SESSIONS) >= MAX_SESSIONS:
        SESSIONS.pop(min(SESSIONS, key=lambda k: SESSIONS[k]["updated"]))


_SET_KEYS = ("skipped", "literal_ok", "changed")


def export_session(s):
    """A JSON-safe copy of a call for storage (sets become lists; callbacks are dropped)."""
    out = {k: v for k, v in s.items() if not k.startswith("_")}
    for k in _SET_KEYS:
        out[k] = sorted(out.get(k) or [])
    out["history"] = [list(h) for h in s.get("history", [])]
    return out


def restore_session(d):
    """Back into memory from storage: the call continues exactly where it was."""
    s = dict(d)
    for k in _SET_KEYS:
        s[k] = set(s.get(k) or [])
    s["history"] = [tuple(h) for h in s.get("history", [])]
    s["updated"] = time.time()
    _prune()
    SESSIONS[s["id"]] = s
    return s


def start(language="hi", study=None, consent=True):
    _prune()
    lang = "en" if language == "en" else "hi"
    s = {"id": uuid.uuid4().hex[:12], "lang": lang, "state": START, "trail": [],
         "answers": {}, "status": {}, "said": {}, "skipped": set(), "tries": {}, "history": [],
         "pending": [], "correcting": None, "corrections": 0, "recoveries": 0, "recover_to": None,
         "literal_ok": set(), "changed": set(), "clarify": None, "just_corrected": None,
         "problem_value": None, "result": None, "prescreen_id": None, "llm": [], "updated": time.time(),
         "study": study, "first_values": {}, "readbacks": 0, "consent": bool(consent)}
    SESSIONS[s["id"]] = s
    transition(s, COLLECTING, "greeting")
    first = next_field(s)
    reply = GREETING[lang] + PRIVACY[lang][bool(consent)] + BY_KEY[first][lang]
    s["history"].append(("Saathi", reply))
    s["asking"] = first
    return s, reply


def summary(s):
    """The profile as Saathi understood it, for the 'Here's what I understood' card."""
    rows = []
    for k in KEYS:
        if k in s["answers"]:
            row = {"field": k, "label": TITLES[k], "value": s["answers"][k],
                   "text": fmt(k, s["answers"][k]), "status": s["status"].get(k, "validated"),
                   "critical": k in CRITICAL, "card": k in CARD}
            if k == "work":  # the category, with the caller's own words kept beside it as evidence
                row["category"] = norm.work_category(s["answers"][k])
                row["said"] = s["said"].get(k) or s["answers"][k]
                if row["category"] != "other":
                    row["text"] = f'{row["text"]} \u2014 "{s["answers"][k]}"'
            rows.append(row)
        elif k == "tenure_months" and "loan_amount" in s["answers"] and k in s["skipped"]:
            rows.append({"field": k, "label": TITLES[k], "value": None, "critical": True, "card": True,
                         "text": f"not given ({DEFAULT_TENURE} months will be used)", "status": "default"})
    return rows


def _spoken_summary(s):
    """A short read-back of the financial profile (the screen shows every field)."""
    a, lang = s["answers"], s["lang"]
    hi = lang == "hi"
    parts = []
    if "loan_amount" in a:
        t = ""
        if "tenure_months" in a:
            t = f", {fmt('tenure_months', a['tenure_months'], lang)} mein" if hi else \
                f" over {fmt('tenure_months', a['tenure_months'], lang)}"
        parts.append(f"loan {inr(a['loan_amount'])}{t}" if hi else f"a loan of {inr(a['loan_amount'])}{t}")
    if "monthly_income" in a:
        source = f" ({norm.work_label(a['work'], lang)})" if a.get("work") else ""
        parts.append(f"kamaai {inr(a['monthly_income'])} mahina{source}" if hi
                     else f"monthly income {inr(a['monthly_income'])}{source}")
    n = a.get("other_loans_count")
    if n == 0:
        parts.append("koi aur loan nahi" if hi else "no other loans")
    elif n:
        bit = f"{n} aur loan" if hi else f"{n} other loan{'s' if n != 1 else ''}"
        if "other_loans_emi" in a:
            bit += f", EMI {inr(a['other_loans_emi'])}"
        if "other_loans_outstanding" in a:
            bit += (f", baaki {inr(a['other_loans_outstanding'])}" if hi
                    else f", {inr(a['other_loans_outstanding'])} still owed")
        parts.append(bit)
    return SAY["summary_intro"][lang] + "; ".join(parts) + "." + SAY["summary_ask"][lang]


# ---------- recovery: spell out the options, then offer to skip or an advisor ----------

RECOVER_AFTER = 2  # failed answers to one question before we offer to skip it (or, if required, an advisor)


def _b(label_en, label_hi, say_en, say_hi):
    return {"en": (label_en, say_en), "hi": (label_hi, say_hi)}


YES_NO = [_b("Yes", "Haan", "Yes", "Haan"), _b("No", "Nahi", "No", "Nahi")]
# After one failed answer: the question again with its options or examples spelled out, and (for fields
# with a short list of answers) buttons on the web call. Buttons only send words the caller could have
# said; the answer still goes through the same parser, quote and read-back as speech.
HELP = {
    "loan_amount": {"en": "For example '50 thousand', '1 lakh' or '2.5 lakh'.",
                    "hi": "Jaise '50 hazaar', '1 lakh' ya 'dhai lakh'."},
    "tenure_months": {"en": "For example '12 months', '2 years' or '3 years'. If you're not sure, say 'you decide'.",
                      "hi": "Jaise '12 mahine', '2 saal' ya '3 saal'. Pakka na ho toh 'aap bataiye' boliye.",
                      "buttons": [_b("1 year", "1 saal", "1 year", "1 saal"), _b("2 years", "2 saal", "2 years", "2 saal"),
                                  _b("3 years", "3 saal", "3 years", "3 saal"),
                                  _b("You decide", "Aap bataiye", "You decide", "Aap bataiye")]},
    "monthly_income": {"en": "A rough figure is fine, for example '15 thousand' or '40 thousand'.",
                       "hi": "Andaaza bhi chalega, jaise '15 hazaar' ya '40 hazaar'."},
    "work": {"en": "For example: your own shop or business, a salaried job, driving, farming, or daily-wage work.",
             "hi": "Jaise: apni dukaan ya business, salary wali naukri, driver, kheti, ya dihadi mazdoori.",
             "buttons": [_b("Own shop / business", "Dukaan / business", "I run my own shop", "Apni dukaan hai"),
                         _b("Salaried job", "Naukri", "I have a salaried job", "Naukri karta hoon"),
                         _b("Driver", "Driver", "I am a driver", "Driver hoon"),
                         _b("Farming", "Kheti", "I am a farmer", "Kheti karta hoon"),
                         _b("Daily-wage work", "Mazdoori", "Daily-wage labour", "Dihadi mazdoori")]},
    "years_in_work": {"en": "For example '6 months', '2 years' or '10 years'.",
                      "hi": "Jaise '6 mahine', '2 saal' ya '10 saal'."},
    "other_loans_count": {"en": "Say 'no' if you have none, or how many: one, two, three.",
                          "hi": "Koi nahi hai toh 'nahi' boliye, warna kitne: ek, do, teen.",
                          "buttons": [_b("None", "Koi nahi", "No other loans", "Koi loan nahi"),
                                      _b("1", "1", "One loan", "Ek loan hai"), _b("2", "2", "Two loans", "Do loan hain"),
                                      _b("3", "3", "Three loans", "Teen loan hain")]},
    "other_loans_emi": {"en": "The monthly total, for example '3,000' or '5 thousand'.",
                        "hi": "Mahine ki kul kist, jaise '3000' ya '5 hazaar'."},
    "other_loans_outstanding": {"en": "A rough figure is fine, for example '50 thousand' or '1 lakh'.",
                                "hi": "Andaaza bhi chalega, jaise '50 hazaar' ya '1 lakh'."},
    "past_loans": {"en": "Say 'none' if this is your first loan, or how many: one, two, three.",
                   "hi": "Pehli baar hai toh 'koi nahi' boliye, warna kitne: ek, do, teen.",
                   "buttons": [_b("First loan", "Pehli baar", "None, this is my first", "Koi nahi, pehli baar"),
                               _b("1", "1", "One", "Ek"), _b("2", "2", "Two", "Do"), _b("3", "3", "Three", "Teen")]},
    "ever_refused": {"en": "Please say yes or no.", "hi": "Kripya haan ya nahi boliye.", "buttons": YES_NO},
    "ever_late": {"en": "Please say yes or no.", "hi": "Kripya haan ya nahi boliye.", "buttons": YES_NO},
    "late_last_year": {"en": "Please say yes or no.", "hi": "Kripya haan ya nahi boliye.", "buttons": YES_NO},
    "late_over_month": {"en": "Please say yes or no.", "hi": "Kripya haan ya nahi boliye.", "buttons": YES_NO},
    "overdue_now": {"en": "Please say yes or no.", "hi": "Kripya haan ya nahi boliye.", "buttons": YES_NO},
    "housing": {"en": "Please choose one: your own house, a rented place, or living with family.",
                "hi": "Inmein se ek boliye: apna ghar, kiraye ka ghar, ya parivaar ke saath.",
                "buttons": [_b("Own house", "Apna ghar", "My own house", "Apna ghar"),
                            _b("Rented", "Kiraye pe", "On rent", "Kiraye pe"),
                            _b("With family", "Parivaar ke saath", "With my family", "Parivaar ke saath")]},
    "age": {"en": "Please tell me your age in years, for example '32'.",
            "hi": "Apni umar saal mein bataiye, jaise '32'."},
}
SKIP_WORDS = re.compile(r"\b(skip|chhodo|chhod do|chod do|chodo|aage badho|aage chalo|next|pass|rehne do)\b|"
                        r"छोड़|आगे बढ़|रहने दो", re.I)
SAY.update({
    "offer_skip": {"en": "No problem, this question is optional. Shall we skip it? "
                         "Or, if you prefer, I can pass your answers to a loan advisor.",
                   "hi": "Koi baat nahi, yeh sawaal zaroori nahi hai. Kya ise chhod dein? "
                         "Ya chahein toh main aapki jaankari loan advisor ko de sakta hoon."},
    "skipped_ok": {"en": "Okay, let's skip that. ", "hi": "Theek hai, ise chhod dete hain. "},
    "retry_question": {"en": "Okay, let's try once more. ", "hi": "Theek hai, ek baar aur koshish karte hain. "},
})


def _repeat_money(k, lang):
    """'Could you repeat your monthly income? For example '15 thousand' or '40 thousand'.'"""
    return SAY["repeat_short"][lang].format(label=LABELS[lang][k]) + HELP[k][lang]


def _help_buttons(k, lang):
    return [{"label": b[lang][0], "action": "answer", "text": b[lang][1]} for b in HELP.get(k, {}).get("buttons", [])]


def buttons(s):
    """Tap targets for the call screen. Each sends `action` (and `text`, for the transcript)."""
    hi = s["lang"] == "hi"
    if s["state"] in FINISHED:
        out = []
        if s["state"] == COMPLETED and s.get("result") and s.get("applicant") and not s.get("advisor_case"):
            out.append({"label": "Try a different amount" if not hi else "Alag rakam dekhein", "action": "whatif",
                        "text": "Alag rakam ya samay dekhna hai" if hi else "I'd like to try a different amount"})
        if not s.get("advisor_case"):
            out.append({"label": "Talk to a loan advisor", "action": "handoff",
                        "text": "Loan advisor se baat karni hai" if hi else "I'd like to talk to a loan advisor"})
        out.append({"label": "Start a new check" if not hi else "Naya check", "action": "new_call",
                    "text": "Naya check shuru karna hai" if hi else "Start a new check"})
        return out
    if s["pending"]:
        p = s["pending"][0]
        if p["kind"] == "choose":
            out = [{"label": fmt(p["field"], o), "action": f"choose:{i}", "text": fmt(p["field"], o, s["lang"])}
                   for i, o in enumerate(p["options"])]
            if p.get("why") == "multiple" and p["field"] in SUMMABLE:
                out.append({"label": f"Total {fmt(p['field'], sum(p['options']))}", "action": "choose:total",
                            "text": "Dono milakar" if hi else "Both together"})
            return out
        return [{"label": "Yes", "action": "yes", "text": "Haan" if hi else "Yes"},
                {"label": "No", "action": "no", "text": "Nahi" if hi else "No"}]
    if s["state"] == COLLECTING and s.get("skip_offer") and s.get("skip_offer") == s.get("asking"):
        return [{"label": "Skip it" if not hi else "Chhod dein", "action": "skip",
                 "text": "Chhod dijiye" if hi else "Skip it"},
                {"label": "Try again" if not hi else "Phir se", "action": "retry",
                 "text": "Phir se poochiye" if hi else "Ask me again"},
                {"label": "Talk to a loan advisor", "action": "handoff",
                 "text": "Loan advisor se baat karni hai" if hi else "I'd like to talk to a loan advisor"}]
    if s["state"] == COLLECTING and s.get("help_for") and s.get("help_for") == s.get("asking"):
        return _help_buttons(s["asking"], s["lang"])
    if s["state"] == RECOVERY:
        return [{"label": "Try again" if not hi else "Phir se", "action": "yes",
                 "text": "Haan, phir se koshish karte hain" if hi else "Yes, let's try again"},
                {"label": "Talk to a loan advisor", "action": "handoff",
                 "text": "Loan advisor se baat karni hai" if hi else "I'd like to talk to a loan advisor"}]
    if s["state"] == VERIFYING:
        return [{"label": "Confirm", "action": "confirm", "text": "Haan, sab sahi hai" if hi else "Yes, that's correct"},
                {"label": "Change", "action": "change", "text": "Kuch badalna hai" if hi else "I need to change something"}]
    if s["state"] == CORRECTION and not s["correcting"]:
        return [{"label": TITLES[k], "action": f"correct:{k}",
                 "text": f"{LABELS['hi'][k]} badalni hai" if hi else f"Change my {LABELS['en'][k]}"}
                for k in KEYS if k in s["answers"] or k == "tenure_months"]
    if s["state"] == RESULT:
        return [{"label": "Yes", "action": "yes", "text": "Haan" if hi else "Yes"},
                {"label": "No, thanks", "action": "no", "text": "Nahi, shukriya" if hi else "No, thanks"},
                {"label": "Talk to a loan advisor", "action": "handoff",
                 "text": "Loan advisor se baat karni hai" if hi else "I'd like to talk to a loan advisor"}]
    return []


def public(s):
    """What the call screen sees: no internal bookkeeping."""
    pending = None
    if s["pending"]:
        p = s["pending"][0]
        pending = {"field": p["field"], "kind": p["kind"], "value": p.get("value"), "options": p.get("options")}
    return {"session_id": s["id"], "state": s["state"], "asking": s.get("asking", "done"),
            "language": s["lang"], "answers": dict(s["answers"]),
            "fields": {k: {"value": v, "status": s["status"].get(k, "validated"), "said": s["said"].get(k, "")}
                       for k, v in s["answers"].items()},
            "summary": summary(s), "pending": pending, "correcting": s["correcting"],
            "buttons": buttons(s), "skipped": sorted(s["skipped"]), "not_needed": list(s.get("not_needed", [])),
            "done": s["state"] in FINISHED, "result": s["result"], "prescreen_id": s["prescreen_id"],
            "simulation": s.get("simulation"), "advisor_case": s.get("advisor_case"),
            "new_call": bool(s.get("new_call")),
            "consent": s.get("consent", True),
            "stage": stage(s),
            "trail": [f"{t['from']} -> {t['to']}" for t in s["trail"][-20:]],
            "llm": s["llm"][-1] if s["llm"] else ""}


# ---------- setting values ----------

def _set(s, k, v, said, status="validated"):
    s.setdefault("first_values", {}).setdefault(k, v)  # what we first understood (study: first-pass accuracy)
    old = s["answers"].get(k)
    if old is not None and not _same(k, old, v):
        s.setdefault("change_log", []).append({
            "field": k, "before": old, "after": v, "said": said, "ts": round(time.time(), 3),
            "how": "correction" if s["state"] == CORRECTION else "clarified"})
    s["answers"][k] = v
    s["status"][k] = status
    s["said"][k] = said
    s["skipped"].discard(k)
    s["changed"].add(k)
    if k == "work":
        s["work_category"] = norm.work_category(v)
    if k in RULE_FIELDS and s.get("not_needed"):
        s["skipped"].difference_update(s["not_needed"])  # asked again only if they could now move the band
        s["not_needed"] = []
    if k == "other_loans_count" and v == 0:  # no loans: their EMI and debt are 0 by definition
        for d in ("other_loans_emi", "other_loans_outstanding"):
            s["answers"].pop(d, None)
            s["status"].pop(d, None)


def _flag(s, kind, field, values):
    s.setdefault("flag_log", []).append({"kind": kind, "field": field, "values": values,
                                         "ts": round(time.time(), 3)})


def _unset(s, k):
    s["answers"].pop(k, None)
    s["status"].pop(k, None)


def _queue(s, item):
    if not any(p["field"] == item["field"] for p in s["pending"]):
        s["pending"].append({"tries": 0, **item})
        s["readbacks"] = s.get("readbacks", 0) + 1


def _corrected(s, k):
    if k == s["correcting"]:
        s["correcting"] = None
        s["corrections"] += 1
        s["just_corrected"] = k


def _commit(s, k, v, said, mode):
    """Store a checked value, unless it contradicts what we already have."""
    old = s["answers"].get(k)
    if mode == "collect" and old is not None and not _same(k, old, v):
        _queue(s, {"kind": "choose", "why": "contradiction", "field": k, "options": [old, v], "said": said,
                   "mode": mode})
        _flag(s, "contradiction", k, [old, v])
        return
    if k in ("other_loans_emi", "other_loans_outstanding") and v > 0 and s["answers"].get("other_loans_count") == 0:
        _queue(s, {"kind": "loans_conflict", "field": k, "value": v, "said": said, "mode": mode})
        _flag(s, "loans_conflict", k, [0, v])
        return
    _set(s, k, v, said)
    if mode == "correct":
        _corrected(s, k)


def offer(s, k, raw, words, mode="collect"):
    """Run every check on one heard value and store it, queue a read-back, or return the problem
    code ("negative", "too_small", "too_large", "tenure_range", "age_range", "unclear")."""
    verdict = check(s, k, raw, words)
    kind = verdict[0]
    if kind == "invalid":
        s["problem_value"] = verdict[2]
        return verdict[1]
    if kind == "choose":
        _queue(s, {"kind": "choose", "why": "multiple", "field": k, "options": verdict[1], "said": words,
                   "mode": mode})
        return None
    v = verdict[1]
    # An EMI above the whole monthly income is almost always a loan's size heard as its installment
    # ("13 lakh ka loan"): keep it as the debt and ask for the EMI again, clearly.
    income = s["answers"].get("monthly_income")
    if k == "other_loans_emi" and income and v > income:
        print(f"[converse {s['id']}] EMI {v} is above income {income}: keeping it as debt", flush=True)
        if "other_loans_outstanding" not in s["answers"]:
            _set(s, "other_loans_outstanding", v, words)
        s["clarify"] = "other_loans_emi"
        return None
    if kind == "confirm":
        _queue(s, {"kind": "confirm", "how": verdict[2], "field": k, "value": v, "said": words,
                   "raw": norm.number(words) if verdict[2] == "scale" else None, "mode": mode})
        return None
    _commit(s, k, v, words, mode)
    return None


def _yes(text, action):
    return action == "yes" or (not action and bool(YES.search(text)) and not NO.search(text))


def _no(text, action):
    return action == "no" or (not action and bool(NO.search(text)))


def _resolve_pending(s, text, action):
    """The caller's answer to a read-back. Returns (prefix to say, problem code)."""
    p = s["pending"][0]
    k, lang, mode = p["field"], s["lang"], p.get("mode", "collect")
    # Only real amounts count as a new value: in "the second one" / "pehla wala, ek" the word "one" is not ₹1.
    heard = [a["value"] for a in norm.amounts(text) if a["digits"] or a["scaled"]] if not action else []

    if p["kind"] == "choose":
        pick = None
        if action.startswith("choose:"):
            arg = action.split(":", 1)[1]
            if arg == "total":
                pick = sum(p["options"])
            elif arg.isdigit() and int(arg) < len(p["options"]):
                pick = p["options"][int(arg)]
        elif heard and k in NUMERIC:
            match = [o for o in p["options"] if any(_same(k, o, h) for h in heard)]
            if match:
                pick = match[0]
            else:  # a third value: check it like a fresh answer, replacing the old one
                s["pending"].pop(0)
                if p.get("why") == "contradiction":
                    _unset(s, k)
                return "", offer(s, k, text, text, mode)
        elif BOTH.search(text) and p.get("why") == "multiple" and k in SUMMABLE:
            pick = sum(p["options"])
        elif FIRST.search(text):
            pick = p["options"][0]
        elif SECOND.search(text):
            pick = p["options"][1]
        if pick is None:
            p["tries"] += 1
            if p["tries"] >= MAX_TRIES:
                s["pending"].pop(0)
                _unset(s, k)
                return SAY["again"][lang], None
            return SAY["sorry"][lang], None
        s["pending"].pop(0)
        _set(s, k, pick, p["said"])
        if mode == "correct":
            _corrected(s, k)
        return SAY["noted"][lang], None

    if p["kind"] == "loans_conflict":
        if _yes(text, action):
            s["pending"].pop(0)
            _unset(s, "other_loans_count")  # ask how many again
            _set(s, k, p["value"], p["said"])
            return SAY["noted"][lang], None
        if _no(text, action):
            s["pending"].pop(0)
            return SAY["noted"][lang], None
        p["tries"] += 1
        if p["tries"] >= MAX_TRIES:
            s["pending"].pop(0)
        return SAY["sorry"][lang], None

    # confirm: "I heard X. Is that right?"
    same = any(_same(k, p["value"], h) for h in heard) or (
        p["how"] == "scale" and any(_same(k, p["raw"] or 0, h) for h in heard))
    if heard and not same:
        s["pending"].pop(0)  # they said a different number: check that one instead
        return "", offer(s, k, text, text, mode)
    if _yes(text, action):
        s["pending"].pop(0)
        _commit(s, k, p["value"], p["said"], mode)
        return SAY["noted"][lang], None
    if _no(text, action):
        s["pending"].pop(0)
        if p["how"] == "scale":
            s["literal_ok"].add(k)  # next time, take a bare number as said
        if mode == "correct":
            s["correcting"] = k
        return SAY["again"][lang], None
    p["tries"] += 1
    if p["tries"] >= MAX_TRIES:
        s["pending"].pop(0)
        return SAY["again"][lang], None
    return "", None


def _pending_prompt(s):
    p, lang = s["pending"][0], s["lang"]
    k = p["field"]
    label = LABELS[lang][k]
    if p["kind"] == "confirm":
        if p["how"] == "scale":
            raw = p["raw"]
            raw = int(raw) if raw is not None and raw == int(raw) else raw
            text = SAY["confirm_scale"][lang].format(label=label, raw=raw, value=fmt(k, p["value"], lang))
        else:
            text = SAY["confirm_number"][lang].format(label=label, value=fmt(k, p["value"], lang))
        return text + (SAY["yes_no"][lang] if p["tries"] else "")
    if p["kind"] == "choose":
        a, b = (fmt(k, o, lang) for o in p["options"][:2])
        if p.get("why") == "contradiction":
            return SAY["contradiction"][lang].format(label=label, a=a, b=b)
        text = SAY["choose_multiple"][lang].format(label=label, a=a, b=b)
        return text + (SAY["choose_total"][lang] if k in SUMMABLE else "")
    return SAY["loans_conflict"][lang].format(label=label, value=fmt(k, p["value"], lang))


def _problem_text(s, k, code):
    lang = s["lang"]
    if code == "unclear" or code not in PROBLEMS:
        return SAY["sorry"][lang]
    lo = LIMITS.get(k, (0,))[0]
    v = s.get("problem_value")
    return PROBLEMS[code][lang].format(label=LABELS[lang][k], min=inr(lo) if k in MONEY else lo,
                                       value=fmt(k, v, lang) if v is not None else "")


def _cross_checks(s):
    """Checks between answers. Returns (field to ask again, message) or None."""
    a, lang = s["answers"], s["lang"]
    emi, income = a.get("other_loans_emi"), a.get("monthly_income")
    if emi is not None and income and emi > income and "other_loans_emi" not in s["changed"]:
        # The income changed to below an EMI we already had: ask for the income again.
        msg = SAY["emi_over_income"][lang].format(emi=inr(emi), income=inr(income))
        _unset(s, "monthly_income")
        return "monthly_income", msg
    return None


# ---------- the turn ----------

async def turn(s, text, decide, build_applicant, save, api_key="", model="gemini-3.5-flash-lite",
               action="", assess=None, safety=None, simulator=None, advisor=None, llm=None):
    """Handles one caller utterance (or button press) and returns the reply to speak. `llm` is the server's
    provider router (Groq, then Gemini); without it `api_key` means Gemini alone."""
    s["_llm_used_this_turn"] = False
    text = (text or "").strip()[:1000]
    action = (action or "").strip()
    s["updated"] = time.time()
    s.update(changed=set(), clarify=None, just_corrected=None, problem_value=None, help_for=None)
    if s["state"] in FINISHED:
        wants_person = action == "handoff" or (not action and text and HUMAN.search(text))
        if wants_person and advisor and not s.get("advisor_case"):
            return _late_handoff(s, advisor, text)
        if wants_person and s.get("advisor_case"):
            return SAY["handoff_done"][s["lang"]].format(case=s["advisor_case"]) + _after_options(s)
        if s["state"] == HANDOFF:
            transition(s, COMPLETED, "call over")
        if action == "new_call" or (not action and text and NEW_CALL.search(text)):
            s["history"].append(("Caller", text or "[new_call]"))
            s["new_call"] = True  # the call screen starts a fresh call (new session, nothing carried over)
            return SAY["new_call"][s["lang"]]
        if _can_reopen_what_if(s, simulator) and (action == "whatif" or (not action and text and
                                                                         _looks_like_what_if(text))):
            s["history"].append(("Caller", text or "[whatif]"))
            transition(s, WHAT_IF, "caller came back for a what-if")
            s["asking"] = "whatif"
            if action == "whatif" or not text:
                reply = SAY["whatif_prompt"][s["lang"]]
            else:
                env = {"simulator": simulator}
                reply = _try_what_if(s, env, text, "") or SAY["whatif_prompt"][s["lang"]]
            s["history"].append(("Saathi", reply))
            return reply
        return SAY["finished"][s["lang"]] + _after_options(s)
    s["history"].append(("Caller", text or f"[{action}]"))
    wanted = language_request(text, action)
    if wanted:  # "talk in English please": switch, say so, and ask the same thing again
        s["lang"] = wanted
        reply = SAY["lang_switched"][wanted] + _repeat_question(s)
        s["history"].append(("Saathi", reply))
        return reply
    if text and not action:
        s["lang"] = _language(text, None, s["lang"])
    env = {"decide": decide, "build_applicant": build_applicant, "save": save, "assess": assess,
           "safety": safety, "simulator": simulator, "advisor": advisor, "api_key": api_key, "model": model,
           "llm": llm}
    s["_advisor"] = advisor  # used by _handoff, which some paths reach without env
    s["_build_applicant"] = build_applicant  # used by the short call's band probe (next_field)

    # Consent can change at any point: "save mat karo" stops storage, "save karo" allows it.
    choice = (action == "consent:no" or (not action and NO_SAVE.search(text or ""))) and "no" or \
        (action == "consent:yes" or (not action and SAVE_OK.search(text or ""))) and "yes" or None
    if choice:
        s["consent"] = choice == "yes"
        s["consent_changed"] = True
        reply = SAY["consent_" + choice][s["lang"]]
        if s["state"] == COLLECTING and s.get("asking") in BY_KEY and not s["pending"]:
            reply += " " + BY_KEY[s["asking"]][s["lang"]]
        s["history"].append(("Saathi", reply))
        return reply

    if action == "handoff" or (not action and text and HUMAN.search(text)):
        if HANDOFF in TRANSITIONS[s["state"]]:
            if not s.get("consent"):  # asking for an advisor is asking us to keep the summary
                s["consent"] = True
                s["consent_via"] = "advisor request"
            reply = _handoff(s, "caller asked for a loan advisor", question=text)
            s["history"].append(("Saathi", reply))
            return reply

    handler = {COLLECTING: _collecting, VERIFYING: _verifying, CORRECTION: _correcting,
               RESULT: _after_result, NEXT_STEPS: _after_next_steps, RECOVERY: _recovering,
               WHAT_IF: _what_if}[s["state"]]
    reply = await handler(s, env, text, action)
    s["history"].append(("Saathi", reply))
    return reply


NEW_CALL = re.compile(r"\b(new (call|check)|start (again|over)|naya (check|call)|nayi (call|baat)|dobara shuru|"
                      r"phir se shuru|shuru se)\b|नया|दोबारा शुरू", re.I)


def _can_reopen_what_if(s, simulator):
    return s["state"] == COMPLETED and bool(s.get("result")) and bool(s.get("applicant")) and bool(simulator)


def _looks_like_what_if(text):
    loan, tenure = whatif_request(text)
    return loan is not None or tenure is not None or bool(WHAT_IF_WORDS.search(text) or MAX_EMI.search(text)
                                                          or LOW_EMI.search(text))


def _after_options(s):
    """What the caller can still do once the call is over."""
    lang = s["lang"]
    if s.get("advisor_case"):
        return SAY["after_options_saved"][lang]
    return SAY["after_options_result" if s.get("result") else "after_options"][lang]


def _late_handoff(s, advisor, text):
    """An advisor request after the call has ended: save the case without reopening the call."""
    lang = s["lang"]
    s["history"].append(("Caller", text or "[handoff]"))
    s["consent"] = True              # asking for an advisor is asking us to keep the summary
    s.setdefault("consent_via", "advisor request")
    try:
        case = advisor(s, text)
    except Exception as e:  # noqa: BLE001
        print(f"[converse {s['id']}] advisor case failed: {type(e).__name__}: {e}", flush=True)
        reply = SAY["handoff_failed"][lang]
    else:
        s["advisor_case"] = case["id"]
        s.setdefault("trail", []).append({"from": s["state"], "to": s["state"], "why": "advisor requested after the call",
                                          "ts": round(time.time(), 3)})
        key = "handoff_notified" if (case.get("notification") or {}).get("delivered") else "handoff"
        reply = SAY[key][lang].format(case=case["id"])
    s["history"].append(("Saathi", reply))
    return reply


def _handoff(s, why, question=""):
    """Save an advisor case (when the server provides one) and tell the caller honestly."""
    lang = s["lang"]
    case_id, notified = "", None
    if s.get("_advisor"):
        try:
            case = s["_advisor"](s, question)
            case_id = case["id"]
            notified = (case.get("notification") or {}).get("delivered")
        except Exception as e:  # noqa: BLE001
            print(f"[converse {s['id']}] advisor case failed: {type(e).__name__}: {e}", flush=True)
            return SAY["handoff_failed"][lang]
    transition(s, HANDOFF, why)
    s["asking"] = "done"
    s["advisor_case"] = case_id or s.get("advisor_case")
    print(f"[converse {s['id']}] advisor handoff: {why} case={case_id}", flush=True)
    if not case_id:
        return ("Theek hai, aap loan advisor se baat karna chahte hain. Dhanyavaad!" if lang == "hi"
                else "Okay, you'd like to talk to a loan advisor. Thank you!")
    if notified:  # only when the advisor channel confirmed delivery
        return SAY["handoff_notified"][lang].format(case=case_id)
    return SAY["handoff"][lang].format(case=case_id)


async def _extract(s, env, text, asked):
    """Candidate answers [(field, value, caller's words)] and Gemini's reply, if any."""
    out = []
    g = {"reply": "", "asking": "", "used": "fallback"}
    # Identifiers are masked for every reader of this text: the LLM, our parser (a phone number is not an
    # amount) and the quote kept as evidence.
    text, _ = pii.mask(text)
    if _llm_available(env) and text:
        try:
            res, provider = await _llm(s, env, "extract", SYSTEM_PROMPT, json.dumps({
                "conversation": _transcript(s),
                "collected": s["answers"],
                "field_just_asked": asked,
                "still_needed": [k for k in KEYS if k not in s["answers"] and k not in s["skipped"]],
                "question_hints": {k: BY_KEY[k]["en"] for k in KEYS
                                   if k not in s["answers"] and k not in s["skipped"]},
                "caller_said": text,
                "reply_language": "English" if s["lang"] == "en" else "Hinglish (Hindi in Latin script)",
            }, ensure_ascii=False), EXTRACT_SCHEMA)
            for a in res.get("answers") or []:
                if not isinstance(a, dict) or a.get("field") not in BY_KEY:
                    continue
                if quoted(a.get("quote", ""), text) and not pii.has_token(a.get("quote")):
                    out.append((a["field"], a.get("value"), a.get("quote")))
                else:
                    print(f"[converse {s['id']}] dropped unquoted answer {a.get('field')}={a.get('value')!r}", flush=True)
            g = {"reply": str(res.get("reply") or "").strip(), "asking": str(res.get("asking") or ""),
                 "used": provider}
        except Exception as e:  # noqa: BLE001
            print(f"[converse {s['id']}] LLM unavailable, using our parser: {type(e).__name__}: {e}", flush=True)
    if g["used"] == "fallback" and text:
        out = fallback_candidates(text, asked)
    elif text:
        # Belt and braces: Gemini sometimes skips a field the caller clearly stated ("my salary is 40,000"
        # in a dry run). Our parser fills only money/tenure fields Gemini didn't return; every value still
        # goes through check() and the read-back.
        have = {k for k, _, _ in out}
        ours = fallback_candidates(text, asked)
        for k, raw, words in ours:
            if k not in have and k not in s["answers"] and (k in MONEY or k in ("tenure_months", "years_in_work",
                                                                                 "other_loans_count")):
                out.append((k, raw, words))
        # Gemini may quote one end of a range ("35 hazaar" out of "30-35 hazaar"). When our parser's span
        # for the same money field holds more amounts than the quote, check the wider span, so a range or
        # two amounts are always asked about instead of one being stored.
        span = {k: w for k, _, w in ours if k in MONEY}

        def count(w):
            return len(norm.amounts(TENURE_PHRASE.sub(" ", str(w or ""))))
        out = [(k, raw, span[k] if k in span and count(span[k]) > count(words) else words) for k, raw, words in out]
    # One spoken amount answers one question: when the same words are given to two fields (Gemini read
    # "sixty thousand", said to the income question, as the loan), keep the field we asked about.
    by_words = {}
    for cand in out:
        by_words.setdefault(_plain(cand[2]), []).append(cand)
    out = [next((c for c in group if c[0] == asked), group[0]) for group in by_words.values()]
    return out, g


# Clause breaks for the scripted parser: punctuation, "and"/"aur"/"but" (not "one and a half"), and
# where a new "I ..." / "main ..." / "mujhe ..." statement starts (speech-to-text drops punctuation).
CLAUSES = re.compile(r"(?<!\d)[,.]|[,.](?!\d)|[;!?।]|\b(?:and|aur|but|par)\b(?!\s+(?:a\s+)?(?:half|1/2))|"
                     r"(?<!\S)और(?!\S)|(?=\b(?:i|main|mujhe|meri|mera|mere|my)\b)|"
                     r"(?=(?<!\S)(?:मुझे|मैं|मेरी|मेरा|मेरे)(?!\S))", re.I)  # not inside "40,000" or "1.5"
_COUNT_WORD = "|".join(sorted((w for w, v in norm._WORDS.items() if v == int(v) and v < 50), key=len, reverse=True))
LOANS_COUNT = re.compile(
    rf"(?<![\d,.\w])(\d{{1,2}}|{_COUNT_WORD})\s+"
    r"(?:(?!(?:lakhs?|lacs?|hazaar|hazar|thousand|crores?|k|ka|ki|ke|rupaye|rupees|rs|लाख|हज़ार|हजार|करोड़|का|की|के)"
    r"(?:\s|$))[a-zऀ-ॿ]+\s+){0,2}?(?:other\s+|aur\s+)?(?:loans?|लोन)(?![a-zऀ-ॿ])", re.I)
NO_LOANS = re.compile(r"koi\s+(?:aur\s+|dusra\s+|bhi\s+)?loan\s+nahi|no\s+(?:other\s+)?loans?\b|"
                      r"(?:don'?t|do not)\s+have\s+(?:any\s+)?(?:other\s+)?loans?|कोई\s+(?:और\s+)?लोन\s+नहीं", re.I)
LOANISH = re.compile(r"\b(loans?|emi|kist|kisht|repay|chukana|chukaunga|chuka|installment)\b|लोन|किस्त", re.I)
WANTS_LOAN = re.compile(r"chahiye|chaahiye|chahta|chahti|chahte|need|want|lena hai|leni hai|चाहिए|चाहता|चाहती", re.I)
# Years in work: "6 saal se", "das saal ho gaye", "for 5 years", "since 2019", "6 साल से". "Se" (since) is
# never a repayment period.
YEARS_SINCE = re.compile(
    r"((?:\d+(?:\.\d+)?|[a-zऀ-ॿ]+)\s*(?:saal|sal|years?|yrs?|varsh|baras|साल|वर्ष))\s*"
    r"(?:se\b|से|ho\s*gaye|ho\s*gaya|hogaye|from|ka\s+experience|experience|का\s+अनुभव)|"
    r"\b(?:for|since)\s+((?:\d+(?:\.\d+)?|[a-z]+)\s*(?:years?|yrs?)|(?:19[5-9]\d|20\d\d))", re.I)
_SCALE = r"(?:lakhs?|lacs?|k|hazaar|hazar|thousand|crores?|लाख|हज़ार|हजार|करोड़)(?![a-zऀ-ॿ])"
AMOUNT_SPAN = re.compile(r"₹?\s*(?:\d[\d,]*(?:\.\d+)?(?:\s*" + _SCALE + r")?|[a-zऀ-ॿ]+\s+" + _SCALE + r")", re.I)


def _segments(clause):
    """Split a clause with several amounts so each piece holds one amount and the words right after it
    ("मुझे 50,000 का लोन चाहिए ... महीने में 30,000 कमाता हूँ" -> loan piece, income piece). Speech-to-text
    often drops every comma, so clause breaks alone can't separate them."""
    spans = [m for m in AMOUNT_SPAN.finditer(clause) if norm.number(m.group()) and norm.number(m.group()) >= 1000]
    if len(spans) < 2:
        return [clause]
    out, start = [], 0
    for i, m in enumerate(spans):
        stop = spans[i + 1].start() if i + 1 < len(spans) else len(clause)
        after = clause[m.end():stop].split()
        end = m.end() + len(" ".join(after[:3])) + (1 if after else 0) if i + 1 < len(spans) else len(clause)
        end = min(end, stop)
        out.append(clause[start:end])
        start = end
    return [p for p in out if p.strip()]


def fallback_candidates(text, asked):
    """Without Gemini: [(field, value text, caller's words)] from one utterance, deterministically.

    "I need 1 lakh for 2 years, I earn 40,000, I have 4 loans with 9,000 EMI and 50 lakh still owed"
    gives loan, tenure, income, loan count, EMI and debt. Each clause goes to the field its words name
    (earn -> income, EMI -> EMI, owed/debt -> debt, loan -> loan); an amount with no field word answers
    the question we asked. Every value still goes through check() and the read-back like any other.
    """
    found, leftover, prev_clause = {}, [], ""
    for clause in (piece for c0 in CLAUSES.split(text) for piece in _segments(c0 or "")):
        c = (clause or "").strip()
        if not c:
            continue
        money = [a for a in norm.amounts(TENURE_PHRASE.sub(" ", c)) if a["scaled"] or a["value"] >= 1000]
        field = mentioned_field(c)
        used = False
        if money and field in MONEY:
            target = field
        elif money and field in (None, "loan_amount") and asked in MONEY:
            target = asked if field is None else "loan_amount"
        else:
            target = None
        if target:  # two clauses for one amount ("salary 20 hazaar aur dukaan se 10 hazaar") stay together
            found[target] = f"{found[target]} aur {c}" if target in found else c
            used = True
        m = LOANS_COUNT.search(c)
        if m and WANTS_LOAN.search(c):
            m = None  # "ek loan chahiye": the loan they want, not one they already have
        if m and norm.number(m.group(1)) is not None and norm.number(m.group(1)) < 50:
            # "do loan liye hain pehle" answers the past-loans question when that is what we asked.
            found.setdefault("past_loans" if asked == "past_loans" else "other_loans_count", m.group(0))
            used = True
        y = YEARS_SINCE.search(c)
        # "6 saal se dukaan hai" is years in work; "1 lakh for 2 years" or "2 saal se loan chal raha hai" is not.
        if y and (money or LOANISH.search(c) or LOANISH.search(prev_clause)) and not norm.work_known(c):
            y = None  # also "ek loan hai, 2 saal se chal raha hai": the "since" belongs to the loan
        if y and asked != "tenure_months":
            span = y.group(1) or y.group(2)
            if norm.years(span) is not None:
                found.setdefault("years_in_work", span)  # the clause still answers work, if that was asked
            c_for_tenure = YEARS_SINCE.sub(" ", c)
        else:
            c_for_tenure = c
        tenure = next((t.group(1) for t in TENURE_PHRASE.finditer(c_for_tenure)
                       if norm.number(t.group(1)) is not None), None)
        if tenure and (money or re.search(r"\b(loan|repay|chukana|chukane|for|mein|ke liye)\b", c, re.I)) \
                and asked != "years_in_work" and "kaam" not in c.lower() and "work" not in c.lower():
            found.setdefault("tenure_months", tenure)
            used = True
        if NO_LOANS.search(c) and not norm.amounts(c):
            found.setdefault("other_loans_count", "0")  # "koi loan nahi hai", whatever was asked
            used = True
        # (Never a housing answer: "sarkari quarter" is where they live, not a government job.)
        if not used and asked != "work" and not money and norm.work_known(c) and not WANTS_LOAN.search(c)                 and not (asked == "housing" and HOUSING_WORDS.search(c)):
            found.setdefault("work", c)  # "...and I work as a driver": work named while answering something else
            used = True
        if not used:
            leftover.append(c)
        prev_clause = c
    if asked in BY_KEY and asked not in found:
        if not found:
            found[asked] = text  # a plain answer to the question we asked ("kirana shop", "35", "nahi")
        elif asked in ("work", "housing") and leftover:
            rest = " ".join(leftover)
            # Asked about work, the words left after the other answers are the work ("sabzi bechta hoon, 10
            # saal se"), unless they're only a yes/no or filler.
            plain = re.sub(r"[^a-zऀ-ॿ ]", " ", rest.lower()).split()
            if valid(asked, rest) and (asked == "housing" or norm.work_known(rest)
                                       or (plain and not all(YES.fullmatch(w) or NO.fullmatch(w) for w in plain))):
                found[asked] = rest  # "I run a kirana shop and earn 30,000": the shop is the work
    return [(k, v, v) for k, v in found.items()]


async def _collecting(s, env, text, action):
    asked = s.get("asking") if s.get("asking") in BY_KEY else next_field(s)
    if action == "answer":  # a help button: exactly the words on it, treated like typed text
        action = ""
    if s.get("skip_offer") and s["skip_offer"] != asked:
        s["skip_offer"] = None
    if s.get("skip_offer") == asked and not s["pending"]:
        if action == "skip" or (not action and (SKIP_WORDS.search(text) or (_yes(text, "") and not
                                                                            valid(asked, text)))):
            s["skip_offer"] = None
            s["skipped"].add(asked)
            return _advance(s, env, SAY["skipped_ok"][s["lang"]])
        if action == "retry" or (not action and _no(text, "") and not valid(asked, text)):
            s["skip_offer"] = None
            s["asking"] = asked
            s["help_for"] = asked
            return SAY["retry_question"][s["lang"]] + BY_KEY[asked][s["lang"]] + " " + HELP[asked][s["lang"]]
        s["skip_offer"] = None  # anything else: they may simply be answering it
    if s["pending"]:
        k = s["pending"][0]["field"]
        prefix, problem = _resolve_pending(s, text, action)
        return _advance(s, env, prefix, (k, problem) if problem else None)

    if asked == "tenure_months" and DONT_KNOW.search(text) and not norm.amounts(text):
        s["skipped"].add("tenure_months")  # the affordability rule picks the tenure
        return _advance(s, env, SAY["noted"][s["lang"]])
    if asked in HISTORY and DONT_KNOW.search(text):
        s["skipped"].add(asked)  # unknown stays unknown: the model has a learned "not answered" branch
        return _advance(s, env, SAY["noted"][s["lang"]])
    if asked == "tenure_months" and LOW_EMI.search(text) and not norm.amounts(text):
        s["skipped"].add("tenure_months")  # "EMI kam rakhni hai": options come after the result
        s["prefers_low_emi"] = True
        return _advance(s, env, SAY["low_emi_noted"][s["lang"]])

    # "My income is wrong" before the summary: forget that answer and ask for it again.
    wrong = mentioned_field(text)
    # Only an explicit "wrong / galat / change": "koi loan nahi" (no other loans) mentions "loan" and
    # "nahi" but is an answer, not a correction (a synthetic dry run caught it erasing the loan amount).
    if wrong in s["answers"] and WRONG_WORDS.search(text) and not norm.amounts(text) and not action:
        _unset(s, wrong)
        s["tries"].pop(wrong, None)
        if next_field(s) == wrong:
            s["asking"] = wrong
            return SAY["noted"][s["lang"]] + SAY["ask_correct"][s["lang"]].format(label=LABELS[s["lang"]][wrong])
        return _advance(s, env, SAY["noted"][s["lang"]])  # asked again when its turn comes

    s["asked_before"] = asked
    candidates, g = await _extract(s, env, text, asked)
    s["claims"] = {k: valid(k, raw) for k, raw, _ in candidates if k in MONEY}
    problem = None
    for k, raw, words in candidates:
        if k != asked and k in ("work", "housing") and k in s["answers"]:
            continue  # a side mention never replaces a free-text answer already given (corrections do)
        p = offer(s, k, raw, words, "collect")
        if p and k == asked:
            problem = (k, p)
    # Gemini may miss a short direct answer ("do", "nahi"); try our parser on it too.
    if g["used"] != "fallback" and asked in BY_KEY and asked not in s["answers"] and not s["pending"] \
            and not s["clarify"] and text:
        p = offer(s, asked, text, text, "collect")
        if p and p != "unclear":
            problem = (asked, p)
    s["llm"].append(g["used"])

    if asked in BY_KEY and asked not in s["answers"] and not s["pending"] and not s["clarify"]:
        s["tries"][asked] = s["tries"].get(asked, 0) + 1
        if s["tries"][asked] >= MAX_TRIES and not BY_KEY[asked].get("need"):
            s["skipped"].add(asked)
    return _advance(s, env, "", problem, g)


def _advance(s, env, prefix="", problem=None, g=None):
    """Decide what to say next from the state of the profile. Never calls Gemini."""
    lang = s["lang"]
    cross = _cross_checks(s)
    if s["pending"]:
        s["asking"] = s["pending"][0]["field"]
        lead = _problem_text(s, problem[0], problem[1]) if problem else prefix
        return lead + _pending_prompt(s)

    if s["state"] == CORRECTION:
        if s["corrections"] > MAX_CORRECTIONS:
            return SAY["too_many_changes"][lang] + _handoff(s, "too many corrections")
        k = s["correcting"]
        if k:
            s["asking"] = k
            lead = _problem_text(s, k, problem[1]) if problem else (prefix or SAY["noted"][lang])
            return lead + SAY["ask_correct"][lang].format(label=LABELS[lang][k])
        if s.get("just_corrected"):
            k = s["just_corrected"]
            if k in s["answers"]:
                prefix = SAY["updated"][lang].format(label=LABELS[lang][k], value=fmt(k, s["answers"][k], lang))
        elif not cross and next_field(s) == "done":
            s["asking"] = "correction"
            return prefix + SAY["which_field"][lang]

    nxt = next_field(s)
    if nxt != "done":
        if s["state"] == CORRECTION:
            transition(s, COLLECTING, "correction needs new answers")
        specific = problem and problem[0] == nxt and problem[1] in PROBLEMS and problem[1] != "unclear"
        # A precise refusal ("can't be negative") tells the caller what to fix: allow one more go.
        if BY_KEY[nxt].get("need") and s["tries"].get(nxt, 0) >= (MAX_TRIES if specific else RECOVER_AFTER):
            return _enter_recovery(s, COLLECTING, f"could not understand {nxt}")
        s["asking"] = nxt
        fails = s["tries"].get(nxt, 0) if nxt == s.get("asked_before") and not s.get("clarify") else 0
        if cross and cross[0] == nxt:
            return prefix + cross[1] + BY_KEY[nxt][lang]
        if s.get("clarify") == nxt:
            return prefix + CLARIFY[nxt][lang]
        if fails >= RECOVER_AFTER and nxt not in s.setdefault("skip_offered", []) and not specific:
            s["skip_offered"].append(nxt)
            s["skip_offer"] = nxt
            return prefix + SAY["sorry"][lang] + SAY["offer_skip"][lang]
        if fails == 1 and nxt in HELP and nxt not in MONEY and not specific:
            s["help_for"] = nxt
            return prefix + SAY["sorry"][lang] + BY_KEY[nxt][lang] + " " + HELP[nxt][lang]
        if specific and nxt in HELP and HELP[nxt].get("buttons"):
            s["help_for"] = nxt
        if problem and problem[0] == nxt:
            if problem[1] == "unclear" and nxt in MONEY:
                return prefix + SAY["sorry"][lang] + _repeat_money(nxt, lang)
            return prefix + _problem_text(s, nxt, problem[1]) + BY_KEY[nxt][lang]
        if g and g["used"] != "fallback" and g["asking"] == nxt and g["reply"] and not prefix \
                and _question_ok(g["reply"], s):
            return g["reply"]
        if nxt == s.get("asked_before") and not prefix and nxt in MONEY:
            return SAY["sorry"][lang] + _repeat_money(nxt, lang)
        return prefix + BY_KEY[nxt][lang]

    if s["state"] in (COLLECTING, CORRECTION):
        transition(s, VERIFYING, "profile complete")
    s["asking"] = "confirm"
    s["tries"].pop("confirm", None)
    return prefix + _spoken_summary(s)


def _enter_recovery(s, back_to, why):
    s["recoveries"] += 1
    if s["recoveries"] > 2 and HANDOFF in TRANSITIONS[s["state"]]:
        return _handoff(s, f"recovery failed: {why}")
    transition(s, RECOVERY, why)
    s["recover_to"] = back_to
    s["asking"] = "recovery"
    return SAY["recovery"][s["lang"]]


async def _recovering(s, env, text, action):
    back = s["recover_to"] or COLLECTING
    if not action and re.search(r"\b(advisor|adviser|summary|save)\b|save kar", text or "", re.I):
        return _handoff(s, "caller chose the advisor in recovery", question=text)
    if _no(text, action) and not _yes(text, action):
        transition(s, COMPLETED, "caller stopped")
        s["asking"] = "done"
        return SAY["bye"][s["lang"]] + _after_options(s)
    if back == COLLECTING:
        transition(s, COLLECTING, "retry")
        k = next_field(s)
        s["tries"].pop(k, None)
        s["asked_before"] = None  # a fresh start: ask plainly, not "sorry, I didn't understand"
        if _yes(text, action) or not text:
            return _advance(s, env)
        s["asking"] = k
        return await _collecting(s, env, text, action)  # they may have just answered
    transition(s, VERIFYING, "retry")
    return _advance(s, env)


async def _intent(s, env, text, action):
    """("confirm" | "correct" | None, field or None) for a reply to the summary."""
    if action == "confirm":
        return "confirm", None
    if action == "change":
        return "correct", None
    if action.startswith("correct:"):
        k = action.split(":", 1)[1]
        return "correct", k if k in BY_KEY else None
    field = mentioned_field(text)
    no, yes = NO.search(text), YES.search(text)
    if no and (not yes or field or re.search(r"\b(sahi|theek|thik|correct|right)\s+(nahi|nahin|not)\b|"
                                             r"\bnot\s+(correct|right)\b", text, re.I)):
        return "correct", field
    if field and norm.amounts(text):
        return "correct", field
    if yes and not no:
        return "confirm", None
    if _llm_available(env) and text:
        try:
            out, provider = await _llm(s, env, "intent", INTENT_PROMPT,
                                       json.dumps({"caller_said": text}, ensure_ascii=False), INTENT_SCHEMA)
            s["llm"].append(provider)
            intent, k = out.get("intent"), out.get("field")
            if intent in ("confirm", "correct"):
                return intent, k if k in BY_KEY else None
        except Exception as e:  # noqa: BLE001
            print(f"[converse {s['id']}] LLM intent unavailable: {type(e).__name__}: {e}", flush=True)
    return None, field


def _apply_correction_value(s, k, text):
    """In the same breath as naming the field ("income 60 hazaar hai"), take the value too."""
    if k in NUMERIC and norm.amounts(text):
        return offer(s, k, text, text, "correct")
    return None


WHAT_IF_WORDS = re.compile(r"\bwhat if\b|\bagar\b|\bsuppose\b|\bkya hoga\b", re.I)


async def _verifying(s, env, text, action):
    if not action and WHAT_IF_WORDS.search(text or ""):
        # A what-if before the assessment would silently change the request: answer it after.
        return SAY["whatif_later"][s["lang"]]
    intent, field = await _intent(s, env, text, action)
    if intent == "confirm":
        transition(s, CONFIRMED, "caller confirmed")
        for k in s["answers"]:
            s["status"][k] = "confirmed"
        return await _assess(s, env)
    if intent == "correct":
        transition(s, CORRECTION, f"caller wants to change {field or 'something'}")
        s["correcting"] = field
        problem = _apply_correction_value(s, field, text) if field and not action else None
        return _advance(s, env, "", (field, problem) if problem else None)
    s["tries"]["confirm"] = s["tries"].get("confirm", 0) + 1
    if s["tries"]["confirm"] >= MAX_TRIES:
        return _enter_recovery(s, VERIFYING, "no clear yes or no to the summary")
    return SAY["verify_unclear"][s["lang"]]


async def _correcting(s, env, text, action):
    if s["pending"]:
        k = s["pending"][0]["field"]
        prefix, problem = _resolve_pending(s, text, action)
        return _advance(s, env, prefix, (k, problem) if problem else None)

    k = s["correcting"]
    if action.startswith("correct:"):
        k = action.split(":", 1)[1]
        s["correcting"] = k if k in BY_KEY else None
        return _advance(s, env)
    if action == "confirm":
        transition(s, VERIFYING, "nothing to change")
        return _advance(s, env)
    mentioned = mentioned_field(text)

    if k is None:
        if mentioned:
            s["correcting"] = mentioned
            problem = _apply_correction_value(s, mentioned, text)
            return _advance(s, env, "", (mentioned, problem) if problem else None)
        if YES.search(text) and not NO.search(text):
            transition(s, VERIFYING, "nothing to change")
            return _advance(s, env)
        _, field = await _intent(s, env, text, "")
        if field:
            s["correcting"] = field
            return _advance(s, env)
        s["tries"]["correction"] = s["tries"].get("correction", 0) + 1
        if s["tries"]["correction"] >= MAX_TRIES:
            transition(s, VERIFYING, "could not tell which field")
            return _advance(s, env)
        return SAY["which_field"][s["lang"]]

    # Free-text fields would swallow "no, the income is wrong": switch field first.
    if mentioned and mentioned not in (k, "loan_amount") and (k not in NUMERIC or not norm.amounts(text)):
        s["correcting"] = k = mentioned
        problem = _apply_correction_value(s, k, text)
        return _advance(s, env, "", (k, problem) if problem else None)
    problem = offer(s, k, text, text, "correct")
    if problem == "unclear" and _llm_available(env) and text:
        candidates, _ = await _extract(s, env, text, k)
        for f, raw, words in candidates:
            if f == k:
                problem = offer(s, k, raw, words, "correct")
    return _advance(s, env, "", (k, problem) if problem else None)


def require_confirmed(s):
    """The gate before any assessment: confirmed state, required fields, every answer confirmed."""
    if s["state"] != CONFIRMED:
        raise NotConfirmed(f"assessment needs state {CONFIRMED}, call is in {s['state']}")
    missing = [k for k in REQUIRED if k not in s["answers"]]
    unconfirmed = [k for k in s["answers"] if s["status"].get(k) != "confirmed"]
    if missing or unconfirmed or s["pending"]:
        raise NotConfirmed(f"missing {missing}, unconfirmed {unconfirmed}, pending {len(s['pending'])}")


async def _assess(s, env):
    """CONFIRMED -> ASSESSING_RISK (model) -> CHECKING_AFFORDABILITY (rules) -> SHOWING_RESULT."""
    require_confirmed(s)
    s["asking"] = "done"
    body = {k: str(v) for k, v in s["answers"].items()}
    context = {"channel": "web", "confirmed": True, "open_checks": len(s["pending"])}
    try:
        applicant = env["build_applicant"](body)
        if env.get("safety"):
            # Deterministic safety check before the model runs: a failure pauses with what's needed.
            failed = [c for c in env["safety"](applicant, context) if c["state"] == "fail"]
            if failed:
                print(f"[converse {s['id']}] safety check failed: {[c['id'] for c in failed]}", flush=True)
                transition(s, RECOVERY, "safety check failed")
                s["recover_to"] = VERIFYING
                s["asking"] = "recovery"
                s["safety_failed"] = failed
                return SAY["safety_failed"][s["lang"]] + " " + " ".join(c["detail"] for c in failed)
        transition(s, ASSESSING, "profile confirmed")
        risk = env["assess"](applicant) if env["assess"] else None
        transition(s, AFFORDABILITY, "risk scored")
        result, details = env["decide"](applicant, risk, context)
        if result.get("decision") == "need_more_info":
            raise ValueError(f"decision needs more info: {result.get('missing_fields')}")
    except Exception as e:  # noqa: BLE001
        print(f"[converse {s['id']}] assessment failed: {type(e).__name__}: {e}", flush=True)
        reply = _enter_recovery(s, VERIFYING, "assessment failed")
        return SAY["assess_failed"][s["lang"]] if s["state"] == RECOVERY else reply
    pid = uuid.uuid4().hex[:12]
    result["prescreen_id"] = pid
    env["save"](pid, {**body, "source": "web-call", "session_id": s["id"]}, applicant, result, details)
    s.update(result=result, prescreen_id=pid, applicant=applicant, simulation=None)
    transition(s, RESULT, result["decision"])

    reply, provider = "", "fallback"
    if _llm_available(env):
        try:
            out, provider = await _llm(s, env, "phrase_result",
                                       RESULT_PROMPT.format(language_rule=LANGUAGE_RULES[s["lang"]]),
                                       f"Guidance:\n{result['reply_hint']}", RESULT_SCHEMA)
            reply = str(out.get("reply") or "").strip()
            if reply and not _amounts_ok(reply, result):
                print(f"[converse {s['id']}] Gemini result reply changed an amount; using fallback", flush=True)
                reply = ""
        except Exception as e:  # noqa: BLE001
            print(f"[converse {s['id']}] LLM result phrasing unavailable: {type(e).__name__}: {e}", flush=True)
    s["llm"].append(provider if reply else "fallback")
    if not reply:
        reply = _fallback_result(result, s["lang"])
    return reply + RESULT_FOLLOWUP.get(result["decision"], {}).get(s["lang"], "")


def _try_what_if(s, env, text, action):
    """Answers a what-if question with the deterministic simulator, or returns None."""
    if action or not text or not env.get("simulator") or not s.get("applicant"):
        return None
    lang, a = s["lang"], s["applicant"]
    if MAX_EMI.search(text):
        cap = ((s["result"] or {}).get("assessment") or {}).get("affordability") or {}
        if cap.get("value", 0) <= 0:
            reply = SAY["no_room"][lang]
        else:
            reply = SAY["max_emi"][lang].format(cap=inr(cap["value"]))
        if s["state"] != WHAT_IF:
            transition(s, WHAT_IF, "caller asked for the maximum EMI")
        return reply + SAY["whatif_more"][lang]
    current = s.get("simulation") or {}
    loan, tenure = whatif_request(text)
    loans, bare = whatif_amounts(text)
    if len(loans) > 1 or (loan is None and tenure is None and bare and WHAT_IF_WORDS.search(text)):
        if s["state"] != WHAT_IF:
            transition(s, WHAT_IF, "caller asked an unclear what-if")
        if len(loans) > 1:
            return SAY["whatif_which"][lang].format(options=" / ".join(inr(v) for v in loans))
        return SAY["whatif_bare"][lang].format(n=bare[0])
    if LOW_EMI.search(text) and loan is None and tenure is None:
        options = env["simulator"](a, scen=True).get("scenarios") or []
        safer = next((o for o in options if o.get("kind") == "safer" and not o.get("unavailable")), None)
        if safer:
            loan, tenure = safer["loan_amount"]["value"], safer["tenure"]["value"]
        else:  # no lower amount fits: show the same loan over a longer time
            base = int(a.get("tenure_months") or 24)
            loan, tenure = a["loan_amount"], min(120, base + 12)
    if BACK.search(text) and loan is None and tenure is None:
        loan, tenure = a["loan_amount"], int(a.get("tenure_months") or 24)
    if loan is None and tenure is None:
        return None
    sim = (current.get("simulation") or {})
    loan = loan if loan is not None else (sim.get("loan_amount") or {}).get("value")
    tenure = tenure if tenure is not None else (sim.get("tenure") or {}).get("value")
    try:
        out = env["simulator"](a, loan, tenure)
    except sim_mod.InvalidSimulation as e:
        return str(e) + SAY["whatif_more"][lang]
    s["simulation"] = out
    sm = out["simulation"]
    if sm.get("status") != "paused":
        s.setdefault("sim_log", []).append(f"{sm['loan_amount']['text']} over {sm['tenure']['value']} months "
                                           f"(EMI {sm['emi']['text']})")
        s.setdefault("sim_events", []).append({"ts": round(time.time(), 3), "text": s["sim_log"][-1]})
    if s["state"] != WHAT_IF:
        transition(s, WHAT_IF, "caller asked a what-if")
    return sim_mod.spoken(out["simulation"], lang) + SAY["whatif_more"][lang]


async def _what_if(s, env, text, action):
    lang = s["lang"]
    reply = _try_what_if(s, env, text, action)
    if reply:
        return reply
    if _no(text, action) and not _yes(text, action):
        transition(s, COMPLETED, "caller finished")
        s["asking"] = "done"
        return SAY["bye"][lang] + _after_options(s)
    if _yes(text, action) or re.search(r"next|aage|steps", text or "", re.I):
        transition(s, NEXT_STEPS, "caller wants next steps")
        return _next_steps(s["result"], lang) + SAY["more"][lang]
    return SAY["whatif_unclear"][lang]


async def _after_result(s, env, text, action):
    lang = s["lang"]
    reply = _try_what_if(s, env, text, action)
    if reply:
        return reply
    if _no(text, action) and not _yes(text, action):
        transition(s, COMPLETED, "caller declined next steps")
        s["asking"] = "done"
        return SAY["bye"][lang] + _after_options(s)
    transition(s, NEXT_STEPS, "caller wants next steps")
    return _next_steps(s["result"], lang) + SAY["more"][lang]


async def _after_next_steps(s, env, text, action):
    reply = _try_what_if(s, env, text, action)
    if reply:
        return reply
    transition(s, COMPLETED, "call finished")
    s["asking"] = "done"
    return SAY["finished"][s["lang"]] + _after_options(s)


def _next_steps(r, lang):
    """Deterministic next steps for each decision."""
    d = r["decision"]
    tips = r.get("tips_hi" if lang == "hi" else "tips", "") or r.get("tips", "")
    if lang == "hi":
        if d in ("pre_approved", "needs_review", "counter_offer"):
            return ("Aage ke steps: Aadhaar card, PAN card aur pichhle 3 mahine ka bank statement taiyaar rakhiye. "
                    "Lender documents ki jaanch ke baad hi koi faisla karega. Agar aap chahein, toh 'loan advisor "
                    "se baat' boliye, main aapka summary advisor ke liye save kar dunga.")
        return ((f"Isme madad karega: {tips}. " if tips else "")
                + "3 se 6 mahine baad, jab EMI ya baaki loan kam ho jaaye, dobara check kijiye.")
    if d in ("pre_approved", "needs_review", "counter_offer"):
        return ("Next steps: keep your Aadhaar card, PAN card and the last 3 months of bank statements ready. "
                "A lender decides only after checking those documents. If you like, say 'talk to a loan advisor' "
                "and I'll save your summary for one.")
    return ((f"What would help: {tips}. " if tips else "")
            + "Check again in 3 to 6 months, once your EMIs or existing loans have come down.")


def _digits(text):
    return {int(m.replace(",", "")) for m in re.findall(r"\d[\d,]*", text) if m.replace(",", "").isdigit()}


# Words that spell out an amount. Small numbers ("ek", "do", "two working days") are everyday
# words and are allowed; amounts always need a scale word or a tens word.
NUMBER_WORDS = re.compile(
    r"\b(hundreds?|thousands?|lakhs?|lacs?|crores?|hazaar|hazar|hajar|sau|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|bees|pachees|tees|chalis|chaalis|pachas|pachaas|"
    r"sattar|assi|nabbe)\b",  # not "saath" (60): it also means "with"; "saath hazaar" is caught by hazaar
    re.I)


SPELLED_NUMBERS = re.compile(
    r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|bees|pachees|tees|chalis|chaalis|"
    r"pachas|pachaas|sattar|assi|nabbe|sau)\b", re.I)


def _question_ok(reply, s):
    """Gemini's next question may echo the caller's own numbers but never bring new ones: in a live
    test it told a caller "most people borrow between twenty thousand and one lakh", an invented
    anchor. Spelled-out numbers are refused (they can't be checked), and every figure must be one
    the caller said or one of their collected answers."""
    if SPELLED_NUMBERS.search(reply):
        return False
    said = set()
    for _, text in (h for h in s["history"] if h[0] == "Caller"):
        said |= {int(a["value"]) for a in norm.amounts(text)}  # "ek lakh" -> 100000, "2 saal" -> 2
    said |= {int(v) for v in s["answers"].values() if isinstance(v, (int, float))}
    # Only figures written in digits or with lakh/hazaar count: a lone word like "do" is English "do".
    return {int(a["value"]) for a in norm.amounts(reply) if a["digits"] or a["scaled"]} <= said


def _amounts_ok(reply, result):
    """Every number Gemini says must appear in the guidance, written in digits, and the offer
    amount itself must be in the reply. Number words are refused because they can't be checked."""
    if NUMBER_WORDS.search(reply):
        return False
    allowed = _digits(result["reply_hint"]) | {result.get("tenure_months", 0)}
    said = _digits(reply)
    if result.get("offer_amount") and int(result["offer_amount"]) not in said:
        return False
    return said <= allowed


HI_WORDS = re.compile(r"\b(hai|hain|hoon|hu|mujhe|mera|meri|mere|chahiye|kamaata|kamata|kamaati|kamati|nahi|nahin|"
                      r"haan|kya|aap|main|ka|ki|ke|ko|se|mein|saal|mahine|mahina|hazaar|hazar|wala|wali|kar|raha|"
                      r"rahi|karta|karti|ghar|kiraye|dukaan|apna|apni|toh|bhi|aur|kitna|kitne|baaki|kist)\b", re.I)
EN_WORDS = re.compile(r"\b(i|am|my|is|the|a|an|want|need|earn|have|for|and|of|to|do|it|yes|per|month|months|"
                      r"years|what|how|much|own|live|work|loan|loans|rent|with)\b", re.I)


# "Can you talk in English please" / "इंग्लिश में बात करो" / "Hindi mein boliye": an explicit request to switch.
# Speech-to-text set to Hindi writes English words in Devanagari, so both scripts count.
_EN_NAME = r"(?<![a-z])(?:english|inglish|angrezi|angreji)|इंग्लिश|इंगलिश|अंग्रेज़ी|अंग्रेजी"
_HI_NAME = r"hindi|hinglish|हिंदी|हिन्दी|हिंग्लिश"
_VERB = r"talk|speak|reply|answer|continue|switch|change|baat|bol\w*|टॉक|स्पीक|बात|बोल\w*"
_AFTER = r"please|pls|mein|me|में|प्लीज|bolo|boliye|baat|बात|बोलो|बोलिए|mai"


def _switch(name):
    """A request to change language: a talking verb near the name, the name followed by mein/please/bolo,
    or a sentence ending "in <name>". Not "I work in an English school"."""
    return re.compile(rf"(?:{_VERB})\W+(?:\w+\W+){{0,3}}?(?:in\s+|इन\s+)?(?:{name})|"
                      rf"(?:{name})\s*(?:{_AFTER})(?:\W|$)|\b(?:in|इन)\s+(?:{name})\s*[.!?।]*\s*$", re.I)


SWITCH_TO = {"en": _switch(_EN_NAME), "hi": _switch(_HI_NAME)}


def language_request(text, action=""):
    """'en' or 'hi' when the caller asks to change language (button or words), else None."""
    if action in ("lang:en", "lang:hi"):
        return action[5:]
    if action or not text or norm.amounts(text):
        return None
    for lang, pat in SWITCH_TO.items():
        if pat.search(text):
            return lang
    return None


def _repeat_question(s):
    """What Saathi was waiting for, in the call's (new) language."""
    lang = s["lang"]
    if s["pending"]:
        return ""
    if s["state"] == COLLECTING and s.get("asking") in BY_KEY:
        return " " + BY_KEY[s["asking"]][lang]
    if s["state"] == VERIFYING:
        return " " + _spoken_summary(s)
    return ""


def _language(text, gemini_says, current):
    """Decided by our code from the caller's own words, never by Gemini's guess: Devanagari is Hindi;
    otherwise a clear majority of Hindi or English words switches; anything else (\"3 lakh\", \"35\")
    keeps the call's language. `gemini_says` is ignored and kept only for compatibility."""
    if re.search(r"[ऀ-ॿ]", text or ""):
        return "hi"
    hi, en = len(HI_WORDS.findall(text or "")), len(EN_WORDS.findall(text or ""))
    if hi >= 2 and hi > en:
        return "hi"
    if en >= 2 and en > hi:
        return "en"
    return current


def _fallback_result(r, lang):
    d = r["decision"]
    amt, emi, n = r.get("offer_amount_text", ""), r.get("offer_emi_text", ""), r.get("tenure_months", "")
    tips = r.get("tips_hi" if lang == "hi" else "tips", "") or r.get("tips", "")
    if lang == "hi":
        if d == "pre_approved":
            return (f"Achhi khabar! Is pre-screening ke hisaab se aap {amt} ke loan ki aage ki jaanch ke liye "
                    f"eligible lagte hain, {n} mahine ke liye, EMI lagbhag {emi} mahina. "
                    "Yeh sirf pre-screening hai, final approval nahi.")
        if d == "needs_review":
            return (f"Aapki {amt} ki request, {n} mahine mein, EMI lagbhag {emi}, mumkin lagti hai. "
                    "Ise aage ek loan advisor ko dekhna hoga.")
        if d == "counter_offer":
            return (f"Aapki maangi hui rakam se EMI aapki kamaai ke aadhe se zyada ho jaati. "
                    f"{amt}, {n} mahine ke liye, EMI lagbhag {emi} mahina, aapki kamaai mein fit ho sakta hai. "
                    "Kya yeh theek rahega?")
        return ("Abhi aapki batayi jaankari ke hisaab se hum yeh loan nahi de paayenge. "
                + (f"Isme madad karega: {tips}. " if tips else "") + "Kuch samay baad dobara try zaroor kijiye.")
    if d == "pre_approved":
        return (f"Good news! Based on this pre-screening you look eligible for further review for {amt} over "
                f"{n} months, with an EMI of about {emi} a month. This is a pre-screening, not a final approval.")
    if d == "needs_review":
        return (f"Your request for {amt} over {n} months, EMI about {emi}, looks possible. "
                "A loan advisor would need to review it further.")
    if d == "counter_offer":
        return (f"The amount you asked for would take more than half your income in EMIs. "
                f"A smaller loan of {amt} over {n} months, EMI about {emi} a month, would fit your estimated "
                "capacity. Would that work?")
    return ("Based on what you shared, we can't offer this loan right now. "
            + (f"What would help: {tips}. " if tips else "") + "Please do try again later.")
