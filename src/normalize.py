"""Turn loosely-typed values from the voice agent into scoring inputs.

Gnani Agent Builder fills request bodies from conversation variables, so values may
arrive as strings ("25 hazaar", "2.5 lakh", "haan"), as numbers, empty, "null", or
as an unfilled "{{placeholder}}". Everything here is forgiving: unknown -> None.
"""
import re

_UNITS = [
    (r"(crore|cr)\b", 10_000_000),
    (r"(lakh|lac|lakhs|lacs|l)\b", 100_000),
    (r"(hazaar|hazar|hajar|thousand|k)\b", 1_000),
]
_WORDS = {
    "ek": 1, "one": 1, "do": 2, "two": 2, "teen": 3, "three": 3, "char": 4, "chaar": 4, "four": 4,
    "paanch": 5, "panch": 5, "five": 5, "chhe": 6, "six": 6, "saat": 7, "seven": 7, "aath": 8,
    "eight": 8, "nau": 9, "nine": 9, "das": 10, "ten": 10, "dedh": 1.5, "dhai": 2.5, "adhai": 2.5,
    "bees": 20, "twenty": 20, "pachaas": 50, "pachas": 50, "fifty": 50,
}


def _blank(v):
    return v is None or (isinstance(v, str) and (not v.strip() or v.strip().lower() in
                         ("null", "none", "na", "n/a", "unknown", "nil") or "{{" in v))


def number(v):
    """'25,000' -> 25000, '2.5 lakh' -> 250000, '25k' -> 25000, 'dedh lakh' -> 150000."""
    if _blank(v):
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).lower().replace(",", "").replace("₹", "").replace("rs.", "").replace("rs", "").strip()
    s = s.replace("rupees", "").replace("rupaye", "").replace("rupay", "").strip()
    m = re.search(r"\d+(\.\d+)?", s)
    base = float(m.group()) if m else None
    if base is None:
        for w, n in _WORDS.items():
            if re.search(rf"\b{w}\b", s):
                base = n
                break
    if base is None:
        return None
    for pat, mult in _UNITS:
        if re.search(r"(\d|\b)" + pat, s):
            return base * mult
    return base


def years(v):
    """'6 mahine' -> 0.5, '2 saal' -> 2, '18 months' -> 1.5."""
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
    if any(k in s for k in ("saal", "sal", "year", "varsh", "baras")) or n <= 10:
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
    if s in ("0", "false", "no", "n", "nahi", "nahin", "na", "nai", "illa", "none"):
        return False
    if any(w in s for w in ("nahi", "nahin", "no ", "not", "don't", "dont")):
        return False
    if any(w in s for w in ("haan", "yes", "hai", "own")):
        return True
    return None


# (keywords, income_type, occupation). First match wins, so order matters.
_WORK = [
    (("retired", "pension", "retire"), "Pensioner", None),
    (("student", "padhai kar", "college me", "studying"), "Student", None),
    (("unemployed", "berozgar", "no job", "naukri nahi", "kaam nahi"), "Unemployed", None),
    (("government", "govt", "sarkari", "railway", "police", "army", "psu"), "State servant", None),
    (("kirana", "dukaan", "dukan", "shop owner", "own shop", "my shop", "business", "vyapar", "vyapaar",
      "self employed", "self-employed", "trader", "merchant", "khud ka"), "Commercial associate", None),
    (("driver", "auto", "taxi", "truck", "cab", "rickshaw", "delivery"), "Working", "Drivers"),
    (("teacher", "tuition", "school", "professor"), "Working", "Core staff"),
    (("nurse", "doctor", "hospital", "medical", "pharma", "asha", "anm"), "Working", "Medicine staff"),
    (("security", "guard", "watchman", "chowkidar"), "Working", "Security staff"),
    (("cook", "chef", "kitchen", "khana banana"), "Working", "Cooking staff"),
    (("clean", "safai", "maid", "housekeeping", "sweeper"), "Working", "Cleaning staff"),
    (("waiter", "hotel", "restaurant", "dhaba", "bar"), "Working", "Waiters/barmen staff"),
    (("salesman", "sales", "shop assistant", "counter", "marketing"), "Working", "Sales staff"),
    (("accountant", "accounts", "ca ", "bookkeeping"), "Working", "Accountants"),
    (("manager", "supervisor", "team lead"), "Working", "Managers"),
    (("software", "it ", "computer", "developer", "programmer"), "Working", "IT staff"),
    (("engineer", "technician", "electrician", "mechanic", "plumber"), "Working", "High skill tech staff"),
    (("property", "real estate", "broker", "dealer"), "Working", "Realty agents"),
    (("clerk", "office", "data entry", "receptionist", "secretary"), "Working", "Secretaries"),
    (("beauty", "salon", "parlour", "tailor", "darzi", "barber"), "Working", "Private service staff"),
    (("helper", "peon", "chaprasi"), "Working", "Low-skill Laborers"),
    (("farmer", "kisan", "kheti", "farm", "agri", "labour", "labor", "mazdoor", "majdoor",
      "construction", "factory", "worker", "mill"), "Working", "Laborers"),
    (("job", "naukri", "salary", "private", "company", "salaried"), "Working", None),
]


def work(v):
    """Free-text job description -> (income_type, occupation)."""
    if _blank(v):
        return None, None
    s = f" {str(v).lower()} "
    for keys, income_type, occupation in _WORK:
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
    if any(k in s for k in ("parent", "maa", "papa", "mata", "pita", "family ke saath", "with family", "joint")):
        return "With parents", None
    if any(k in s for k in ("rent", "kiraya", "kiraye", "kirae", "pg", "hostel")):
        return "Rented apartment", False
    if any(k in s for k in ("quarter", "company", "staff")):
        return "Office apartment", False
    if any(k in s for k in ("own", "apna", "khud", "mera ghar", "my house", "self")):
        return "House / apartment", True
    return "House / apartment", None
