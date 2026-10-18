"""Score one loan applicant from what they said in a voice conversation.

The voice agent (Evon) extracts these fields from the call; any can be missing:

    monthly_income        ₹ per month
    loan_amount           ₹ requested
    emi                   ₹ per month they expect to pay (optional)
    tenure_months         loan length (optional, default 24)
    purchase_price        ₹ price of what the loan is for (optional, default = loan_amount)
    loan_type             "Cash loans" | "Revolving loans"
    age                   years
    years_employed        years at current work; None if not working / pensioner
    income_type, occupation, education, housing   (dataset categories, see allowed_values())
    children, family_members
    owns_car, owns_house  bool
    active_loans          number of other loans running now
    active_loans_outstanding   ₹ still owed on them
    existing_emi          ₹ per month already paid on those loans (optional, else estimated)

Currency: the dataset's amounts are monthly income / loan sizes in an undisclosed
currency (median income 1.47 lakh units/month, so not rupees). We convert with one
scale factor chosen so the dataset's median income equals TYPICAL_MONTHLY_INCOME_RS.
Ratios (EMI/income, loan/income) are unaffected by this choice.
"""
import json
import math
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import EMPLOYED_SENTINEL, VOICE_CATEGORICAL, VOICE_FEATURES, add_debt_ratio, add_derived

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
ANNUAL_RATE = 0.18  # assumed interest when we have to estimate an EMI
DEFAULT_TENURE = 24
TYPICAL_MONTHLY_INCOME_RS = 25_000

FIELD_TO_COLUMN = {
    "loan_type": "NAME_CONTRACT_TYPE",
    "income_type": "NAME_INCOME_TYPE",
    "education": "NAME_EDUCATION_TYPE",
    "housing": "NAME_HOUSING_TYPE",
    "occupation": "OCCUPATION_TYPE",
}

# Plain-English labels for explanations. Evon rephrases these in the caller's language.
LABELS = {
    "AMT_INCOME_TOTAL": "income",
    "AMT_CREDIT": "loan amount",
    "AMT_ANNUITY": "monthly EMI",
    "AMT_GOODS_PRICE": "price of what the loan is for",
    "CNT_CHILDREN": "number of children",
    "CNT_FAM_MEMBERS": "family size",
    "OWNS_CAR": "owning a vehicle",
    "OWNS_REALTY": "owning a house",
    "AGE_YEARS": "age",
    "YEARS_EMPLOYED": "years in current work",
    "NOT_EMPLOYED": "not currently employed",
    "CREDIT_TO_INCOME": "loan size compared to income",
    "ANNUITY_TO_INCOME": "EMI compared to income",
    "CREDIT_TO_GOODS": "loan compared to purchase price",
    "INCOME_PER_PERSON": "income per family member",
    "BUREAU_ACTIVE_LOANS": "other loans running now",
    "BUREAU_ACTIVE_DEBT": "amount owed on other loans",
    "DEBT_TO_INCOME": "existing debt compared to income",
    "NAME_CONTRACT_TYPE": "type of loan",
    "NAME_INCOME_TYPE": "source of income",
    "NAME_EDUCATION_TYPE": "education",
    "NAME_HOUSING_TYPE": "housing situation",
    "OCCUPATION_TYPE": "occupation",
}

# Factors the voice agent may say out loud. Loan size, purchase price and income
# level are left out: in this data they are confounded with the lender's own
# decisions (safer people got bigger loans), so "your big loan lowers your risk"
# would be true of the model but misleading advice. They stay in all_factors.
SPOKEN = {
    "ANNUITY_TO_INCOME", "DEBT_TO_INCOME", "BUREAU_ACTIVE_DEBT", "BUREAU_ACTIVE_LOANS",
    "YEARS_EMPLOYED", "NOT_EMPLOYED", "OWNS_REALTY", "OWNS_CAR", "AGE_YEARS",
    "NAME_INCOME_TYPE", "NAME_HOUSING_TYPE",
}

_model = None
_meta = None
_units_per_rupee = None


def _load():
    global _model, _meta, _units_per_rupee
    if _model is None:
        _model = lgb.Booster(model_file=str(MODEL_DIR / "voice_model.txt"))
        _meta = json.loads((MODEL_DIR / "voice_model_meta.json").read_text())
        _units_per_rupee = _meta["medians"]["AMT_INCOME_TOTAL"] / TYPICAL_MONTHLY_INCOME_RS
    return _model, _meta


def allowed_values():
    """Category values the extractor must map the caller's words onto."""
    _, meta = _load()
    return {field: meta["categorical"][col] for field, col in FIELD_TO_COLUMN.items()}


def estimate_emi(amount, months, annual_rate=ANNUAL_RATE):
    r = annual_rate / 12
    return amount * r / (1 - (1 + r) ** -months)


def _to_row(a):
    """Applicant dict -> one-row DataFrame in the model's feature space."""
    _, meta = _load()
    warnings = []
    loan = a.get("loan_amount")
    emi = a.get("emi")
    if loan and not emi:
        emi = estimate_emi(loan, a.get("tenure_months") or DEFAULT_TENURE)
    income = a.get("monthly_income")
    age = a.get("age")
    yrs = a.get("years_employed")
    k = _units_per_rupee
    goods = a.get("purchase_price") or loan

    raw = {
        "AMT_INCOME_TOTAL": income * k if income else np.nan,
        "AMT_CREDIT": loan * k if loan else np.nan,
        "AMT_ANNUITY": emi * k if emi else np.nan,
        "AMT_GOODS_PRICE": goods * k if goods else np.nan,
        "CNT_CHILDREN": a.get("children", np.nan),
        "CNT_FAM_MEMBERS": a.get("family_members", np.nan),
        "FLAG_OWN_CAR": "Y" if a.get("owns_car") else "N",
        "FLAG_OWN_REALTY": "Y" if a.get("owns_house") else "N",
        "DAYS_BIRTH": -age * 365.25 if age else np.nan,
        "DAYS_EMPLOYED": EMPLOYED_SENTINEL if yrs is None and "years_employed" in a else (-yrs * 365.25 if yrs is not None else np.nan),
        "BUREAU_ACTIVE_LOANS": a.get("active_loans", 0),
        "BUREAU_ACTIVE_DEBT": (a.get("active_loans_outstanding") or 0) * k,
    }
    df = add_debt_ratio(add_derived(pd.DataFrame([raw])))
    for field, col in FIELD_TO_COLUMN.items():
        cats = meta["categorical"][col]
        val = a.get(field, "Cash loans" if field == "loan_type" else None)
        if val is not None and val not in cats:
            warnings.append(f"{field}={val!r} is not a known value; treated as unknown")
            val = None
        df[col] = pd.Categorical([val], categories=cats)
    for c in VOICE_FEATURES:
        if c not in VOICE_CATEGORICAL:
            df[c] = df[c].astype("float64")
    return df[VOICE_FEATURES], warnings


def band_of(p):
    _, meta = _load()
    b = meta["bands"]
    return "low" if p < b["low"] else "medium" if p < b["medium"] else "high"


def inr(x):
    """Indian digit grouping: 210000 -> '₹2,10,000'."""
    n = int(round(x))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + "₹" + s


def _fmt(col, v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "not given"
    if col in ("AMT_INCOME_TOTAL", "INCOME_PER_PERSON"):
        return f"{inr(v / _units_per_rupee)}/month"
    if col.startswith("AMT_") or col == "BUREAU_ACTIVE_DEBT":
        return inr(v / _units_per_rupee)
    if col == "ANNUITY_TO_INCOME":
        return f"{v:.0%} of monthly income"
    if col in ("CREDIT_TO_INCOME", "DEBT_TO_INCOME"):
        return f"{v:.1f} months of income"
    if col in ("AGE_YEARS", "YEARS_EMPLOYED"):
        return f"{v:.0f} years"
    if col in ("OWNS_CAR", "OWNS_REALTY", "NOT_EMPLOYED"):
        return "yes" if v else "no"
    return str(v) if not isinstance(v, float) else f"{v:g}"


def score(applicant, n_reasons=3, min_impact=0.003):
    """Return risk probability, band, and the main reasons each way.

    Reasons are what-if effects: for each feature we swap in a typical applicant's
    value and see how the risk moves. impact = this applicant's risk minus that,
    in probability points, so "+0.02" means this factor adds 2 points of risk.
    """
    model, meta = _load()
    X, warnings = _to_row(applicant)
    p = float(model.predict(X)[0])

    typical = {**meta["medians"], **meta["modes"]}
    variants = pd.concat([X] * len(VOICE_FEATURES), ignore_index=True)
    for i, col in enumerate(VOICE_FEATURES):
        if col in VOICE_CATEGORICAL:
            variants[col] = variants[col].astype(object)
        variants.loc[i, col] = typical[col]
    for col in VOICE_CATEGORICAL:
        variants[col] = pd.Categorical(variants[col], categories=meta["categorical"][col])
    p_typical = model.predict(variants[VOICE_FEATURES])

    row = X.iloc[0]
    items = []
    for col, pt in zip(VOICE_FEATURES, p_typical):
        v = row[col]
        if pd.isna(v):
            continue  # nothing said about it, so nothing to explain
        items.append({
            "feature": col,
            "label": LABELS.get(col, col),
            "value": _fmt(col, v if isinstance(v, str) else float(v)),
            "typical": _fmt(col, typical[col] if isinstance(typical[col], str) else float(typical[col])),
            "impact": round(p - float(pt), 4),
        })
    items.sort(key=lambda d: d["impact"])
    spoken = [i for i in items if i["feature"] in SPOKEN]
    return {
        "default_probability": round(p, 4),
        "band": band_of(p),
        "raises_risk": [i for i in reversed(spoken) if i["impact"] > min_impact][:n_reasons],
        "lowers_risk": [i for i in spoken if i["impact"] < -min_impact][:n_reasons],
        "all_factors": list(reversed(items)),
        "warnings": warnings,
    }


MAX_FOIR = 0.5  # all EMIs together at most half of monthly income (common Indian lending rule)


def affordable_loan(applicant):
    """Largest loan whose EMI keeps total EMIs within MAX_FOIR of income.

    This is a lending rule, not a model output: in this data, loan size and risk are
    confounded (riskier people were given smaller loans), so the model can't be used
    to pick an amount.
    """
    income = applicant.get("monthly_income")
    if not income:
        return None
    tenure = applicant.get("tenure_months") or DEFAULT_TENURE
    existing_emi = applicant.get("existing_emi")
    if existing_emi is None:
        existing_emi = estimate_emi(applicant.get("active_loans_outstanding", 0) or 0, DEFAULT_TENURE)
    room = MAX_FOIR * income - existing_emi
    if room <= 0:
        return {"loan_amount": 0, "emi": 0, "tenure_months": tenure, "existing_emi": round(existing_emi),
                "fits_request": False}
    r = ANNUAL_RATE / 12
    max_loan = room * (1 - (1 + r) ** -tenure) / r
    requested = applicant.get("loan_amount") or max_loan
    amount = min(requested, max_loan)
    amount = math.floor(amount / 1000) * 1000
    return {
        "loan_amount": amount,
        "emi": round(estimate_emi(amount, tenure)) if amount else 0,
        "tenure_months": tenure,
        "existing_emi": round(existing_emi),
        "fits_request": requested <= max_loan,
    }


if __name__ == "__main__":
    demo = {
        "monthly_income": 25000, "loan_amount": 300000, "tenure_months": 24,
        "age": 34, "years_employed": 6, "income_type": "Working", "occupation": "Sales staff",
        "education": "Secondary / secondary special",
        "housing": "House / apartment", "children": 2, "family_members": 4,
        "owns_car": False, "owns_house": True, "active_loans": 1, "active_loans_outstanding": 40000,
    }
    print(json.dumps(score(demo), indent=2, ensure_ascii=False))
    print("Affordable:", affordable_loan(demo))
