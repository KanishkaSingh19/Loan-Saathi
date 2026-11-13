"""The questions, their limits and labels, and the patterns that recognise answers."""

import re

import normalize as norm
from policy import POLICY


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
DEFAULT_TENURE = POLICY["default_tenure"]
MAX_TRIES = 3
MAX_CORRECTIONS = 6

# (minimum, read back above, refuse above), from config/policy.yaml
LIMITS = dict(POLICY["limits"])
TENURE_RANGE = (POLICY["tenure_min"], POLICY["tenure_max"])
AGE_RANGE = (POLICY["age_min"], POLICY["age_max"])

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
