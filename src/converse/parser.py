"""Saathi's own parser: candidate answers from one utterance, without any LLM."""

import re

import normalize as norm

from .fields import BY_KEY, HOUSING_WORDS, MONEY, NO, TENURE_PHRASE, YES
from .validation import mentioned_field, valid


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
