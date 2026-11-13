"""Everything Saathi says (Hinglish and English), the LLM prompts, and the recovery help."""

import os
import re

import policy
from policy import POLICY

from .fields import AGE_RANGE, KEYS, LABELS, TENURE_RANGE


# ---------- what Saathi says ----------

# Data protection (India's DPDP Act): answers are stored only with the caller's yes, and deleted after
# RETENTION_DAYS (server.purge_expired). Without consent the call still works, in memory only.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS") or POLICY["retention_days"])
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
    "tenure_range": {"hi": f"Loan {policy.months_text(TENURE_RANGE[0], 'hi')} se "
                           f"{policy.months_text(TENURE_RANGE[1], 'hi')} ke beech chukaya ja sakta hai. ",
                     "en": f"The loan can be repaid over {policy.months_text(TENURE_RANGE[0])} to "
                           f"{policy.months_text(TENURE_RANGE[1])}. "},
    "age_range": {"hi": f"Is pre-screening ke liye umar {AGE_RANGE[0]} se {AGE_RANGE[1]} saal ke beech honi chahiye. ",
                  "en": f"This pre-screening is for ages {AGE_RANGE[0]} to {AGE_RANGE[1]}. "},
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
