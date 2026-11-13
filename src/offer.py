"""Check an offer the borrower got: effective yearly rate, total cost, and whether the EMI fits.

Deterministic, like every number Loan-Saathi gives (no LLM reads or writes any of it):

    the offer (amount, tenure, EMI, optional processing fee, optional quoted rate)
    -> the yearly rate that EMI really implies on what reaches the borrower (amount minus fee)
    -> total paid and total cost
    -> the EMI against their income (all EMIs within the policy's FOIR limit, if the income is known)
    -> flags: a rate far above config/policy.yaml's offer_check.flag_rate_above, the 50% rule broken,
       numbers that can't be a loan, a quoted rate well below the real one.

Results are labelled as estimates and never name a lender, whatever the borrower said.
"""
import math
import re

import normalize as norm
from policy import POLICY
from scoring import inr

LABEL = "Estimate only: Loan-Saathi's own calculation from the numbers you gave, not advice about any lender."
LABEL_HI = "Sirf andaaza: aapke diye numbers par Loan-Saathi ka apna hisaab, kisi lender ke baare mein salah nahi."

OFFER_WORDS = re.compile(r"\boffer\b|\bofer\b|offer\s*(?:mila|aaya|aya|diya|hai)|ऑफ़र|ऑफर|"
                         r"\b(?:bank|lender|company|nbfc)\s+(?:ne|wale)\b.*\b(?:diya|de\s+raha|de\s+rahe)\b", re.I)
EMI_WORDS = re.compile(r"\b(?:emi|e\s*m\s*i|kist|kisht|qist|installment|instalment|monthly\s+payment|"
                       r"har\s+mahine|per\s+month|a\s+month|mahina|mahine\s+ka|mahine\s+ki)\b|किस्त|ई\s*एम\s*आई", re.I)
FEE_WORDS = re.compile(r"\b(?:fee|fees|charge|charges|processing|file\s+charge)\b|शुल्क|फीस", re.I)
RATE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per\s*cent|pratishat|टक्के|प्रतिशत)", re.I)
TENURE = re.compile(r"((?:(?<![\d,.])\d+(?:\.\d+)?|(?<![a-zऀ-ॿ])(?!(?:hazaar|hazar|lakhs?|lacs?|thousand|crores?|k)"
                    r"(?![a-zऀ-ॿ]))[a-zऀ-ॿ]+)\s*(?:saal|sal|years?|yrs?|varsh|baras|mahine|mahina|months?|साल|महीने)"
                    r"(?![a-zऀ-ॿ]))", re.I)
SPLIT = re.compile(r"(?<!\d),|,(?!\d)|[;।]|\b(?:and|aur|with|plus|par|but)\b|और|(?=\b(?:emi|kist|qist|fee|processing)\b)", re.I)
LENDER_NAMES = re.compile(r"\b(HDFC|ICICI|SBI|State Bank|Axis|Kotak|Bajaj|Muthoot|Paytm|IDFC|Yes Bank|Bandhan|"
                          r"Manappuram|Tata Capital|Fullerton|Home Credit|KreditBee|MoneyView|Navi)\b", re.I)


class OfferError(ValueError):
    """The offer can't be checked as given; the message says what to fix."""


def mentions_offer(text):
    return bool(text) and bool(OFFER_WORDS.search(text))


def _money(clause):
    found = [a for a in norm.amounts(clause) if a["digits"] or a["scaled"]]
    return max((a["value"] for a in found), default=None)


def parse(text):
    """{"loan_amount", "tenure_months", "emi", "processing_fee", "quoted_rate"} from one sentence; missing
    pieces are None. "mujhe ek offer mila hai 2 lakh ka, 24 mahine, EMI 11,000, processing fee 2000"."""
    out = {"loan_amount": None, "tenure_months": None, "emi": None, "processing_fee": None, "quoted_rate": None}
    if not text:
        return out
    t = LENDER_NAMES.sub(" ", str(text))
    m = RATE.search(t)
    if m:
        out["quoted_rate"] = float(m.group(1)) / 100
        t = t[:m.start()] + " " + t[m.end():]
    tm = next((x for x in TENURE.finditer(t) if norm.months(x.group(1)) is not None), None)
    if tm:
        out["tenure_months"] = norm.months(tm.group(1))
        t = t[:tm.start()] + " , " + t[tm.end():]
    for clause in SPLIT.split(t):
        if not clause or not clause.strip():
            continue
        v = _money(clause)
        if v is None:
            continue
        if FEE_WORDS.search(clause):
            out["processing_fee"] = out["processing_fee"] or v
        elif EMI_WORDS.search(clause):
            out["emi"] = out["emi"] or v
        elif out["loan_amount"] is None or v > out["loan_amount"]:
            out["loan_amount"] = v
    # Fragments ("EMI" said in one clause, its number in the next) can't be resolved; the caller is asked.
    return out


def _payment(principal, monthly_rate, n):
    if monthly_rate <= 0:
        return principal / n
    return principal * monthly_rate / (1 - (1 + monthly_rate) ** -n)


def implied_yearly_rate(principal, emi, n):
    """The reducing-balance yearly rate (12 x monthly) at which `emi` for `n` months repays `principal`.
    None when the EMI can't repay it (too low) or the numbers are impossible."""
    if principal <= 0 or emi <= 0 or n <= 0 or emi * n < principal:
        return None
    lo, hi = 0.0, 1.0  # monthly rate; 100% a month is far beyond any real loan
    if _payment(principal, hi, n) < emi:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if _payment(principal, mid, n) < emi:
            lo = mid
        else:
            hi = mid
    return 12 * (lo + hi) / 2


def check(loan_amount, tenure_months, emi, processing_fee=None, quoted_rate=None, monthly_income=None,
          existing_emi=None):
    """The checked offer. Raises OfferError when a required number is missing or impossible."""
    missing = [k for k, v in (("loan amount", loan_amount), ("tenure", tenure_months), ("EMI", emi)) if not v]
    if missing:
        raise OfferError("To check an offer I need its " + ", ".join(missing) + ".")
    amount, n, emi = float(loan_amount), int(round(float(tenure_months))), float(emi)
    fee = float(processing_fee or 0)
    if not math.isfinite(amount) or amount < 1000:
        raise OfferError(f"The loan amount must be at least {inr(1000)}.")
    if not POLICY["tenure_min"] <= n <= POLICY["tenure_max"]:
        raise OfferError(f"The tenure must be between {POLICY['tenure_min']} and {POLICY['tenure_max']} months.")
    if emi <= 0 or emi > amount:
        raise OfferError("The EMI should be a monthly amount smaller than the loan.")
    if fee < 0 or fee >= amount:
        raise OfferError("The processing fee should be less than the loan amount.")

    total_paid = emi * n
    received = amount - fee
    rate = implied_yearly_rate(received, emi, n)              # what the borrower really pays, fee included
    rate_on_paper = implied_yearly_rate(amount, emi, n)       # ignoring the fee
    flags = []
    limit = POLICY["offer_flag_rate_above"]
    if rate is None:
        flags.append({"code": "numbers_dont_add_up",
                      "text": f"An EMI of {inr(emi)} for {n} months adds up to {inr(total_paid)}, which doesn't repay "
                              f"{inr(received)}. Please check the numbers on the offer."})
    elif rate > limit:
        flags.append({"code": "rate_far_above",
                      "text": f"The real cost works out to about {rate * 100:.0f}% a year, far above "
                              f"{limit * 100:.0f}%. Ask for the interest rate and all charges in writing, and compare "
                              "other offers before you sign."})
    if quoted_rate is not None and rate is not None and rate > quoted_rate + 0.03:
        flags.append({"code": "quoted_rate_lower",
                      "text": f"The offer says {quoted_rate * 100:g}%, but this EMI works out to about {rate * 100:.0f}% "
                              "a year. A low 'flat' rate often hides a higher real one."})
    afford = None
    if monthly_income:
        income, existing = float(monthly_income), float(existing_emi or 0)
        share = (existing + emi) / income
        fits = share <= POLICY["max_foir"] + 1e-9
        afford = {"known": True, "share": round(share, 4), "limit": POLICY["max_foir"], "fits": fits,
                  "max_emi": round(max(0.0, POLICY["max_foir"] * income - existing))}
        if not fits:
            flags.append({"code": "breaks_50_rule",
                          "text": f"With this EMI, all your EMIs would take {share * 100:.0f}% of your income, above the "
                                  f"{POLICY['max_foir'] * 100:.0f}% limit. An EMI up to {inr(afford['max_emi'])} fits."})
    else:
        afford = {"known": False}
    verdict = ("numbers_dont_add_up" if rate is None else
               "check_carefully" if flags else "looks_reasonable")
    out = {
        "label": LABEL, "label_hi": LABEL_HI,
        "offer": {"loan_amount": amount, "tenure_months": n, "emi": emi, "processing_fee": fee,
                  "quoted_rate": quoted_rate},
        "effective_rate": None if rate is None else round(rate, 4),
        "rate_without_fee": None if rate_on_paper is None else round(rate_on_paper, 4),
        "total_paid": round(total_paid), "money_received": round(received),
        "total_cost": round(total_paid - received), "affordability": afford, "flags": flags, "verdict": verdict,
    }
    out["summary"] = {"en": spoken(out, "en"), "hi": spoken(out, "hi")}
    return out


def spoken(r, lang="en"):
    """One plain paragraph for the caller. Never a lender name; always 'about' and 'estimate'."""
    o = r["offer"]
    hi = lang == "hi"
    if r["effective_rate"] is None:
        return ("Yeh numbers ek loan jaise nahi jud rahe: itni EMI se itna loan chuk nahi sakta. Offer ke kaagaz "
                "par rakam, samay aur EMI dobara dekhiye." if hi else
                "These numbers don't add up to a loan: that EMI can't repay that amount. Please check the amount, "
                "tenure and EMI on the offer again.")
    rate = f"{r['effective_rate'] * 100:.0f}"
    if hi:
        parts = [f"Andaaza: {inr(o['loan_amount'])} ke liye {o['tenure_months']} mahine tak {inr(o['emi'])} ki EMI "
                 f"ka matlab hai lagbhag {rate}% saalana byaaj. Aap kul {inr(r['total_paid'])} chukayenge, yaani "
                 f"{inr(r['total_cost'])} byaaj aur charges."]
        if r["affordability"].get("known"):
            parts.append("Yeh EMI aapki kamaai ke hisaab se theek baithti hai." if r["affordability"]["fits"] else
                         f"Yeh EMI aapki kamaai ke hisaab se zyada hai: {inr(r['affordability']['max_emi'])} tak ki EMI "
                         "theek rahegi.")
        if any(f["code"] == "rate_far_above" for f in r["flags"]):
            parts.append("Yeh byaaj kaafi zyada hai. Sign karne se pehle byaaj aur saare charges likhit mein maangiye "
                         "aur doosre offer bhi dekhiye.")
        if any(f["code"] == "quoted_rate_lower" for f in r["flags"]):
            parts.append("Offer mein likha rate asli rate se kam dikh raha hai; asli rate poochiye.")
        return " ".join(parts)
    parts = [f"Estimate: an EMI of {inr(o['emi'])} for {o['tenure_months']} months on {inr(o['loan_amount'])} "
             f"works out to about {rate}% a year. You would pay {inr(r['total_paid'])} in all, so "
             f"{inr(r['total_cost'])} in interest and charges."]
    if r["affordability"].get("known"):
        parts.append("This EMI fits your income." if r["affordability"]["fits"] else
                     f"This EMI is more than your income allows: an EMI up to {inr(r['affordability']['max_emi'])} fits.")
    if any(f["code"] == "rate_far_above" for f in r["flags"]):
        parts.append("That rate is very high. Ask for the interest rate and all charges in writing and compare other "
                     "offers before you sign.")
    if any(f["code"] == "quoted_rate_lower" for f in r["flags"]):
        parts.append("The rate written on the offer looks lower than the real one; ask what it really is.")
    return " ".join(parts)
