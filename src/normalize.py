"""Turn loosely-typed values from the voice agent into scoring inputs.

Gnani Agent Builder fills request bodies from conversation variables, so values may
arrive as strings ("25 hazaar", "2.5 lakh", "haan"), as numbers, empty, "null", or
as an unfilled "{{placeholder}}". Everything here is forgiving: unknown -> None.
"""
import re

_SCALES = {
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000, "karod": 10_000_000, "karor": 10_000_000,
    "करोड़": 10_000_000, "करोड": 10_000_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "l": 100_000, "लाख": 100_000,
    "hazaar": 1_000, "hazar": 1_000, "hajar": 1_000, "thousand": 1_000, "thousands": 1_000,
    "k": 1_000, "हज़ार": 1_000, "हजार": 1_000,
    "sau": 100, "hundred": 100, "सौ": 100,
}
_WORDS = {
    "ek": 1, "one": 1, "do": 2, "two": 2, "teen": 3, "three": 3, "char": 4, "chaar": 4, "four": 4,
    "paanch": 5, "panch": 5, "five": 5, "chhe": 6, "six": 6, "saat": 7, "seven": 7, "aath": 8,
    "eight": 8, "nau": 9, "nine": 9, "das": 10, "ten": 10, "dedh": 1.5, "dhai": 2.5, "adhai": 2.5,
    "half": 0.5, "aadha": 0.5, "adha": 0.5,
    "gyarah": 11, "eleven": 11, "barah": 12, "baarah": 12, "twelve": 12, "terah": 13, "thirteen": 13,
    "chaudah": 14, "fourteen": 14, "pandrah": 15, "pandra": 15, "fifteen": 15, "solah": 16,
    "sixteen": 16, "satrah": 17, "seventeen": 17, "atharah": 18, "eighteen": 18, "unnees": 19,
    "nineteen": 19, "bees": 20, "twenty": 20,
    "pachees": 25, "pachchis": 25, "pachis": 25, "tees": 30, "tis": 30, "thirty": 30,
    "paintees": 35, "pentis": 35, "chalis": 40, "chaalis": 40, "chalees": 40, "forty": 40,
    "paintalis": 45, "pachaas": 50, "pachas": 50, "fifty": 50, "saath": 60, "sixty": 60,
    "sattar": 70, "seventy": 70, "assi": 80, "eighty": 80, "nabbe": 90, "ninety": 90,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "दस": 10, "बीस": 20, "तीस": 30,
    "चालीस": 40, "पचास": 50, "डेढ़": 1.5, "ढाई": 2.5,
}
# Words that add to the next number: "saadhe teen lakh" = 3.5 lakh, "sawa lakh" = 1.25 lakh.
_PLUS = {"saadhe": 0.5, "sadhe": 0.5, "saade": 0.5, "साढ़े": 0.5, "sawa": 0.25, "sava": 0.25, "savva": 0.25,
         "सवा": 0.25, "paune": -0.25, "pone": -0.25, "पौने": -0.25}
# "do" is 2, but also the verb "give/do": "loan de do", "bata do", "how much do you earn".
_DO_VERB_BEFORE = {"de", "dedo", "kar", "karo", "bata", "batao", "dila", "dilwa", "le", "dikha", "chala", "rakh",
                   "bhej", "samjha", "sun", "bol", "likh", "ho", "kara", "i", "you", "we", "they", "what", "how",
                   "to", "please", "pls", "much", "many"}
_JOIN = {"aur", "and", "or"}          # "1 lakh aur 50 hazaar" is one amount
_RANGE = {"-", "/", "ya", "or", "to", "se", "या"}  # "40-50 hazaar": both get the scale
# English -teen/-ty words are easily misheard ("fourteen" / "forty"), so callers confirm them.
TEEN_TY = re.compile(r"\b(thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
                     r"thirty|forty|fifty|sixty|seventy|eighty|ninety)\b", re.I)
_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _blank(v):
    return v is None or (isinstance(v, str) and (not v.strip() or v.strip().lower() in
                         ("null", "none", "na", "n/a", "unknown", "nil") or "{{" in v))


def _plus_half(word):
    """'one' -> '1.5', '2' -> '2.5'; unknown words are left as they were."""
    try:
        n = float(word)
    except ValueError:
        n = _WORDS.get(word)
    return f"{n + 0.5:g}" if n is not None else f"{word} and a half"


def negative(v):
    """True when the caller states a negative amount ("-5000", "minus 5 hazaar")."""
    s = str(v or "").lower().translate(_DEV_DIGITS)
    return bool(re.search(r"(^|[^\w\d])-\s*\d", s) or re.search(r"\b(minus|negative|ghata|ghaata)\b", s))


def amounts(v):
    """Every amount stated in some text, in order, as dicts {value, scaled, digits}.

    '1 lakh 50 hazaar' -> [150000] (one compound amount), 'saadhe teen lakh' -> [350000],
    '13,00,000 ka 12 lakh ka' -> [1300000, 1200000], '40-50 hazaar' -> [40000, 50000],
    'forty five thousand' -> [45000]. `scaled` means it had lakh/hazaar/k etc.; `digits` that it was
    written in digits (a lone word like "do" is weak: it is also the verb "give")."""
    if _blank(v) or isinstance(v, bool):
        return []
    if isinstance(v, (int, float)):
        return [{"value": float(v), "scaled": False, "digits": True}]
    s = str(v).lower().translate(_DEV_DIGITS).replace("₹", " ")
    s = re.sub(r"(?<=\d),(?=\d)", "", s)
    s = re.sub(r"\b(rs\.?|inr|rupees?|rupaye|rupay|rupaiye|rupiya)\b", " ", s)
    # Halves, as people say them and as speech-to-text writes them: "one and a half lakh",
    # "one and a 1/2 lakh" (Gnani Prisma), "1 1/2 lakh", "dhai" is handled by _WORDS.
    s = re.sub(r"(\d+(?:\.\d+)?|[a-zऀ-ॿ]+)\s+and\s+(?:a\s+)?(?:half|1/2)\b",
               lambda m: _plus_half(m.group(1)), s)
    s = re.sub(r"\b(\d+)\s+1/2\b", lambda m: str(float(m.group(1)) + 0.5), s)
    s = re.sub(r"(?<![\d/])1/2\b", "0.5", s)
    tokens = re.findall(r"\d+(?:\.\d+)?|[a-zऀ-ॿ]+|[-/]", s)

    out = []
    cur = {"total": 0.0, "num": None, "last": float("inf"), "scaled": False, "digits": False,
           "word": False, "plus": 0.0, "range": None}

    def flush():
        total = cur["total"] + (cur["num"] or 0)
        if cur["range"] is not None:
            out.append({"value": cur["range"], "scaled": False, "digits": cur["digits"]})
        if cur["num"] is not None or cur["total"]:
            out.append({"value": total, "scaled": cur["scaled"], "digits": cur["digits"]})
        cur.update(total=0.0, num=None, last=float("inf"), scaled=False, digits=False, word=False,
                   plus=0.0, range=None)

    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        is_digit = tok[0].isdigit()
        val = float(tok) if is_digit else _WORDS.get(tok)
        if tok == "do" and i > 0 and tokens[i - 1] in _DO_VERB_BEFORE and nxt not in _SCALES:
            val = None  # the verb, not the number: "loan de do", "how much do you earn"
        if val is not None:
            val += cur["plus"]
            cur["plus"] = 0.0
            if cur["num"] is not None:
                # "forty five" -> 45; otherwise two numbers in a row are two amounts.
                if (not is_digit and cur["word"] and cur["num"] % 10 == 0 and 20 <= cur["num"] < 100
                        and val < 10):
                    cur["num"] += val
                    continue
                flush()
            cur["num"], cur["word"] = val, not is_digit
            cur["digits"] = cur["digits"] or is_digit
        elif tok in _SCALES:
            scale = _SCALES[tok]
            if cur["num"] is None and cur["plus"]:
                cur["num"], cur["plus"] = 1 + cur["plus"], 0.0  # "sawa lakh" = 1.25 lakh, "paune lakh" = 0.75
            if cur["num"] is None:
                continue
            if scale >= cur["last"]:  # "5 hazaar 3 hazaar": a new amount starts
                carry, digits = cur["num"], cur["digits"]
                cur["num"] = None
                flush()
                cur["num"], cur["digits"] = carry, digits
            if cur["range"] is not None and cur["total"] == 0:
                out.append({"value": cur["range"] * scale, "scaled": True, "digits": cur["digits"]})
                cur["range"] = None
            cur["total"] += cur["num"] * scale
            cur["num"], cur["last"], cur["scaled"], cur["word"] = None, scale, True, False
        elif tok in _PLUS:
            cur["plus"] = _PLUS[tok]
        elif tok in _RANGE and cur["num"] is not None and cur["total"] == 0 and cur["range"] is None and (
                nxt[:1].isdigit() or nxt in _WORDS):
            cur["range"], cur["num"] = cur["num"], None
        elif tok in _JOIN and (cur["total"] or cur["num"] is not None):
            continue
        elif cur["num"] is not None or cur["total"]:
            flush()
    flush()
    return out


def number(v):
    """'25,000' -> 25000, '2.5 lakh' -> 250000, '25k' -> 25000, 'dedh lakh' -> 150000,
    '1 lakh 50 hazaar' -> 150000. The first amount stated in digits or with a scale wins over a lone
    number word; None if there is no number at all."""
    found = amounts(v)
    if not found:
        return None
    strong = [a for a in found if a["digits"] or a["scaled"]]
    return (strong or found)[0]["value"]


def years(v):
    """'6 mahine' -> 0.5, '2 saal' -> 2, '18 months' -> 1.5, 'since 2019' -> years since 2019."""
    m = re.search(r"\b(19[5-9]\d|20\d\d)\b", str(v or ""))
    if m and re.search(r"since|from|se\b|से", str(v), re.I):
        import datetime
        return float(max(0, datetime.date.today().year - int(m.group(1))))
    n = number(v)
    if n is None:
        return None
    s = str(v).lower()
    if any(k in s for k in ("mahine", "mahina", "month", "mah")):
        return n / 12
    return n


def months(v):
    """Loan tenure: '2 saal' -> 24, '18 mahine' -> 18, bare 2 -> 24 (small numbers are years)."""
    n = number(v)
    if n is None:
        return None
    s = str(v).lower()
    if any(k in s for k in ("saal", "sal", "year", "varsh", "baras")):
        return n * 12
    if any(k in s for k in ("month", "mahin", "महीन")):  # "six months" is 6, not 6 years
        return n
    if n <= 10:
        return n * 12
    return n


def boolean(v):
    if _blank(v):
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "haan", "han", "ha", "haa", "ji", "ji haan", "hai", "hoon", "own", "owned"):
        return True
    if s in ("0", "false", "no", "n", "nahi", "nahin", "na", "nai", "illa", "none", "नहीं", "नही", "ना"):
        return False
    if any(w in s for w in ("nahi", "nahin", "no ", "not", "don't", "dont", "never", "नहीं", "नही", "कभी नहीं")):
        return False
    if any(w in s for w in ("haan", "yes", "hai", "own", "हाँ", "हां", "है")):
        return True
    return None


# (keywords, income_type, occupation). First match wins, so order matters.
# (keywords, income_type, occupation, category). First match wins, so order matters. income_type and
# occupation are the model's own categories (models/voice_model_meta.json); `category` is what the caller
# hears back. Rows and model values are unchanged from before categories existed; new keywords (Devanagari,
# street vendors) only add mappings where the default ("Working", no occupation) applied.
_WORK = [
    (("retired", "pension", "retire", "रिटायर", "पेंशन"), "Pensioner", None, "retired"),
    (("student", "padhai kar", "college me", "studying", "छात्र", "पढ़ाई"), "Student", None, "student"),
    (("unemployed", "berozgar", "no job", "naukri nahi", "kaam nahi", "बेरोज़गार", "बेरोजगार", "काम नहीं"),
     "Unemployed", None, "unemployed"),
    (("government", "govt", "sarkari", "railway", "police", "army", "psu", "सरकारी", "रेलवे", "पुलिस"),
     "State servant", None, "government"),
    (("kirana", "dukaan", "dukan", "shop owner", "own shop", "my shop", "business", "vyapar", "vyapaar",
      "self employed", "self-employed", "trader", "merchant", "khud ka", "sabzi", "sabji", "thela", "rehri",
      "vendor", "phal bech", "किराना", "दुकान", "व्यापार", "सब्ज़ी", "सब्जी", "ठेला"),
     "Commercial associate", None, "self_employed"),
    (("driver", "auto", "taxi", "truck", "cab", "rickshaw", "delivery", "ड्राइवर", "ऑटो", "टैक्सी", "रिक्शा"),
     "Working", "Drivers", "driver"),
    (("teacher", "tuition", "school", "professor", "शिक्षक", "टीचर"), "Working", "Core staff", "salaried"),
    (("nurse", "doctor", "hospital", "medical", "pharma", "asha", "anm"), "Working", "Medicine staff", "salaried"),
    (("security", "guard", "watchman", "chowkidar", "चौकीदार"), "Working", "Security staff", "salaried"),
    (("cook", "chef", "kitchen", "khana banana"), "Working", "Cooking staff", "salaried"),
    (("clean", "safai", "maid", "housekeeping", "sweeper"), "Working", "Cleaning staff", "daily_wage"),
    (("waiter", "hotel", "restaurant", "dhaba", "bar"), "Working", "Waiters/barmen staff", "salaried"),
    (("salesman", "sales", "shop assistant", "counter", "marketing"), "Working", "Sales staff", "salaried"),
    (("accountant", "accounts", "ca ", "bookkeeping"), "Working", "Accountants", "salaried"),
    (("manager", "supervisor", "team lead"), "Working", "Managers", "salaried"),
    (("software", "it ", "computer", "developer", "programmer"), "Working", "IT staff", "salaried"),
    (("engineer", "technician", "electrician", "mechanic", "plumber", "मिस्त्री"), "Working", "High skill tech staff",
     "skilled"),
    (("property", "real estate", "broker", "dealer"), "Working", "Realty agents", "self_employed"),
    (("clerk", "office", "data entry", "receptionist", "secretary"), "Working", "Secretaries", "salaried"),
    (("beauty", "salon", "parlour", "tailor", "darzi", "barber", "दर्जी"), "Working", "Private service staff",
     "skilled"),
    (("helper", "peon", "chaprasi"), "Working", "Low-skill Laborers", "daily_wage"),
    (("farmer", "kisan", "kheti", "farm", "agri", "किसान", "खेती"), "Working", "Laborers", "farmer"),
    (("labour", "labor", "mazdoor", "majdoor", "construction", "factory", "worker", "mill", "मज़दूर", "मजदूर",
      "दिहाड़ी", "dihadi"), "Working", "Laborers", "daily_wage"),
    (("job", "naukri", "salary", "private", "company", "salaried", "नौकरी", "सैलरी"), "Working", None, "salaried"),
]
# What the caller hears back for each category: (English, Hinglish).
WORK_CATEGORIES = {
    "self_employed": ("Shop owner / self-employed", "Apna kaam / dukaan"),
    "salaried": ("Salaried job", "Naukri (salary)"),
    "government": ("Government job", "Sarkari naukri"),
    "driver": ("Driver", "Driver"),
    "farmer": ("Farmer", "Kisan"),
    "daily_wage": ("Daily-wage worker", "Dihadi mazdoori"),
    "skilled": ("Skilled worker", "Hunar ka kaam"),
    "retired": ("Retired", "Retired"),
    "student": ("Student", "Padhai"),
    "unemployed": ("Not working right now", "Abhi kaam nahi"),
    "other": ("Other work", "Doosra kaam"),
}


def work_category(v):
    """Free-text job description -> category key (see WORK_CATEGORIES); None for a blank answer."""
    if _blank(v):
        return None
    s = f" {str(v).lower()} "
    for keys, _, _, category in _WORK:
        if any(k in s for k in keys):
            return category
    return "other"


def work_label(v, lang="en"):
    """The category the caller hears back: 'Shop owner / self-employed', or 'Other work (...)' with their words."""
    key = work_category(v)
    if key is None:
        return ""
    en, hi = WORK_CATEGORIES[key]
    label = hi if lang == "hi" else en
    if key == "other":
        words = " ".join(str(v).split()[:6])
        return f"{label} ({words})"
    return label


def work_known(v):
    """True when the text names a kind of work we recognise (not just any words)."""
    s = f" {str(v or '').lower()} "
    return any(k in s for keys, _, _, _ in _WORK for k in keys)


def work(v):
    """Free-text job description -> (income_type, occupation)."""
    if _blank(v):
        return None, None
    s = f" {str(v).lower()} "
    for keys, income_type, occupation, _ in _WORK:
        if any(k in s for k in keys):
            return income_type, occupation
    return "Working", None


def education(v):
    if _blank(v):
        return None
    s = str(v).lower()
    if any(k in s for k in ("dropout", "drop out", "incomplete", "adhuri", "chhod", "left college")):
        return "Incomplete higher"
    if any(k in s for k in ("phd", "doctorate", "post grad", "postgrad", "masters", "m.a", "msc", "m.sc",
                            "mba", "m.com", "mcom", "graduate", "graduation", "degree", "b.a", "bsc",
                            "b.sc", "bcom", "b.com", "btech", "b.tech", "engineering", "college", "ba ")):
        return "Higher education"
    if any(k in s for k in ("12", "10", "twelfth", "tenth", "high school", "matric", "inter", "puc",
                            "diploma", "iti", "secondary", "hsc", "ssc")):
        return "Secondary / secondary special"
    if any(k in s for k in ("8", "5", "primary", "none", "illiterate", "anpadh", "nahi padha",
                            "no school", "padhai nahi", "lower")):
        return "Lower secondary"
    return None


def housing(v):
    """-> (housing category, owns_house)."""
    if _blank(v):
        return None, None
    s = str(v).lower()
    # Gnani Prisma returns Hindi in Devanagari ("किराए पे"), so both scripts are listed.
    if any(k in s for k in ("parent", "maa", "papa", "mata", "pita", "family ke saath", "with family", "joint",
                            "parivaar", "parivar", "ghar walon", "gharwalon",
                            "परिवार", "माता", "पिता", "माँ")):
        return "With parents", None
    if any(k in s for k in ("rent", "kiraya", "kiraye", "kirae", "pg", "hostel", "किराय", "किराए", "किराये",
                            "किरा")):
        return "Rented apartment", False
    if any(k in s for k in ("quarter", "company", "staff", "क्वार्टर", "सरकारी")):
        return "Office apartment", False
    if any(k in s for k in ("own", "apna", "khud", "mera ghar", "my house", "self", "अपना", "अपने", "खुद")):
        return "House / apartment", True
    return "House / apartment", None


# What the loan is for (step 1 of a lender's process). Not a model input and not used by any rule: it only
# shapes the tenure hint and the documents the caller is told to keep ready. First match wins.
_PURPOSE = [
    ("education", ("padhai", "padhaai", "education", "college", "school fee", "fees", "fee bharni", "course",
                   "coaching", "study", "studies", "admission", "पढ़ाई", "कॉलेज", "फीस")),
    ("vehicle", ("gaadi", "gadi", "bike", "scooty", "scooter", "motorcycle", "car", "auto", "rickshaw", "e-rickshaw",
                 "truck", "tempo", "tractor", "vehicle", "two wheeler", "two-wheeler", "गाड़ी", "बाइक", "स्कूटी",
                 "कार", "ऑटो", "ट्रैक्टर")),
    ("business", ("business", "dukaan", "dukan", "shop", "vyapar", "vyapaar", "stock", "maal", "machine", "msme",
                  "kaam badhana", "dhandha", "dhanda", "inventory", "दुकान", "व्यापार", "धंधा")),
    ("home", ("ghar banana", "ghar ki marammat", "ghar ke liye", "makaan", "makan", "house", "home", "flat", "plot",
              "marammat", "repair", "renovation", "construction", "naya ghar", "घर", "मकान", "मरम्मत")),
    ("personal", ("personal", "shaadi", "shadi", "wedding", "marriage", "ilaaj", "ilaj", "medical", "hospital",
                  "emergency", "treatment", "ghar ka kharch", "kharcha", "laptop", "phone", "mobile", "travel",
                  "safar", "karz", "karza", "शादी", "इलाज", "अस्पताल")),
]
# Whole words for Latin script ("car" is not "career", "auto" is not "automatic"); Devanagari as written.
_PURPOSE_RE = [(key, re.compile("|".join(rf"(?<![a-z]){re.escape(w)}(?![a-z])" if w.isascii() else re.escape(w)
                                         for w in words))) for key, words in _PURPOSE]
LOAN_PURPOSES = {
    "personal": ("Personal need", "Personal zaroorat"),
    "vehicle": ("Vehicle", "Gaadi"),
    "education": ("Education", "Padhai"),
    "business": ("Business / shop", "Business / dukaan"),
    "home": ("Home (build, buy or repair)", "Ghar (banana, kharidna ya marammat)"),
    "other": ("Other", "Doosra kaam"),
}


def loan_purpose(v):
    """'bike ke liye' -> 'vehicle', 'beti ki padhai' -> 'education'; 'other' for any other description; None
    for a blank answer."""
    if _blank(v):
        return None
    s = str(v).lower()
    for key, pattern in _PURPOSE_RE:
        if pattern.search(s):
            return key
    return "other"


def loan_purpose_known(v):
    """True when the text names a purpose we recognise (not just any words)."""
    return loan_purpose(v) not in (None, "other")


def loan_purpose_label(v, lang="en"):
    key = loan_purpose(v)
    if key is None:
        return ""
    en, hi = LOAN_PURPOSES[key]
    label = hi if lang == "hi" else en
    if key == "other":
        return f"{label} ({' '.join(str(v).split()[:6])})"
    return label
