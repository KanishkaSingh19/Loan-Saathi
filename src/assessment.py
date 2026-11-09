"""The applicant-facing assessment: what the dashboard shows after a pre-screen.

Everything here is deterministic and built from values the system already has: what the caller
said, the affordability rule (scoring.affordable_loan, all EMIs within 50% of income), the debt
rule (server.DEBT_LIMIT_MONTHS) and the risk model's band and what-if reasons. Nothing is
invented, and every important number says where it came from (`source`, see SOURCES), so the UI
never hard-codes provenance.

    safety_pre(applicant, context)   checks that must pass before anything is assessed
    build(...)                       the assessment shown with the result

It is a pre-screening, not a lending decision: labels say "estimated" and "eligible for further
review", never "approved".
"""
import math

import scoring
from scoring import inr

SOURCES = {
    "you": "Provided by you",
    "rules": "Calculated by Loan-Saathi affordability rules",
    "model": "Estimated by Loan-Saathi risk model",
    "calc": "Calculated by Loan-Saathi at an assumed 18% yearly interest",
    "estimate": "Estimated from your existing debt (EMI not given)",
    "default": "Not given: Loan-Saathi's default used",
}
COMFORT_FOIR = 0.4  # lower end of the suggested range: all EMIs within 40% of income
DECISION_LABELS = {
    "pre_approved": "Eligible for further review",
    "needs_review": "Eligible for advisor review",
    "counter_offer": "Smaller amount suggested",
    "not_eligible_now": "Not eligible right now",
    "need_more_info": "More information needed",
}
STATUS_TEXT = {
    "within": "Within your estimated repayment capacity.",
    "pressure": "This loan may put too much pressure on your monthly income.",
    "not_now": "Based on what you shared, this loan isn't a good fit right now.",
    "paused": "Assessment paused: some information is needed first.",
}
WHY_NOTE = ("Risk factors come from patterns in past borrowers' repayment records. They describe "
            "what usually went with repayment trouble, not a judgement about you. Gender and marital "
            "status are never used, and age is never given as a reason.")
UNAVAILABLE = [
    ("Credit bureau report", "Not checked in a pre-screening; loans and repayment history are as you told us."),
    ("Income proof", "Income is self-reported until salary slips or bank statements are checked."),
    ("Employment verification", "Work and years in work are self-reported."),
]


def _num(value, source, kind="money", suffix=""):
    if kind == "money":
        value = int(round(value))
        text = inr(value)
    elif kind == "months":
        value = int(value)
        text = f"{value} months"
    else:
        text = str(value)
    return {"value": value, "text": text + suffix, "source": source, "source_text": SOURCES[source]}


def _check(cid, label, state, detail):
    """state: pass | flag (ran, and shaped the outcome) | fail (pauses the assessment) | na."""
    return {"id": cid, "label": label, "state": state, "detail": detail}


def safety_pre(applicant, context=None):
    """Deterministic checks before any assessment. A "fail" pauses it and says what's needed."""
    ctx = context or {}
    web = ctx.get("channel") == "web"
    a = applicant
    income, loan = a.get("monthly_income"), a.get("loan_amount")
    checks = []

    missing = [label for key, label in (("loan_amount", "the loan amount"), ("monthly_income", "monthly income"))
               if not a.get(key)]
    checks.append(_check("required", "Required information available", "fail" if missing else "pass",
                         f"Needed: {' and '.join(missing)}." if missing
                         else "Loan amount and monthly income are known."))

    bad = []
    if income is not None and not 1_000 <= income <= 1_00_00_000:
        bad.append("monthly income")
    if loan is not None and not 1_000 <= loan <= 10_00_00_000:
        bad.append("loan amount")
    tenure = a.get("tenure_months")
    if tenure is not None and not 3 <= tenure <= 120:
        bad.append("tenure (3 months to 10 years)")
    for key, label in (("existing_emi", "existing EMI"), ("active_loans_outstanding", "existing debt")):
        if a.get(key) is not None and a[key] < 0:
            bad.append(label)
    if a.get("age") is not None and not 18 <= a["age"] <= 100:
        bad.append("age (18 to 100)")
    checks.append(_check("validated", "Financial values validated", "fail" if bad else "pass",
                         f"Please check: {', '.join(bad)}." if bad else "Every value is within a sensible range."))

    if web:
        confirmed = bool(ctx.get("confirmed"))
        checks.append(_check("confirmed", "User confirmation completed", "pass" if confirmed else "fail",
                             "You confirmed the details." if confirmed
                             else "Please confirm the details Saathi read back."))
        open_checks = int(ctx.get("open_checks") or 0)
        checks.append(_check("contradictions", "Contradictions resolved", "fail" if open_checks else "pass",
                             f"{open_checks} answer(s) still need a yes or no." if open_checks
                             else "No conflicting answers remain."))
    else:
        checks.append(_check("confirmed", "User confirmation completed", "na",
                             "Phone calls don't have a read-back step yet; answers are as heard."))
        checks.append(_check("contradictions", "Contradictions resolved", "na",
                             "Checked only on web calls."))

    emi, owed = a.get("existing_emi"), a.get("active_loans_outstanding")
    clash = []
    if emi is not None and income and emi > income:
        clash.append("the existing EMI is more than the monthly income")
    if a.get("active_loans") == 0 and ((emi or 0) > 0 or (owed or 0) > 0):
        clash.append("no other loans were mentioned, but an EMI or debt was")
    checks.append(_check("consistency", "Safety rules passed", "fail" if clash else "pass",
                         f"Please check: {'; '.join(clash)}." if clash else "Answers are consistent with each other."))
    return checks


HELP = {
    "risk": "How often people with similar answers had trouble repaying, in past records: low, medium or high.",
    "affordability": "The most a new EMI can be while all your EMIs stay within half of your income.",
    "requested_emi": "Your estimated monthly payment for the loan you asked for.",
    "suggested_range": "Loan amounts whose EMIs keep all your EMIs within 40% to 50% of your income.",
}
STATUS_HI = {
    "within": "Aapka maanga hua loan aapki anumanit chukane ki kshamta ke andar hai.",
    "pressure": "Yeh loan aapki mahine ki kamaai par zyada dabaav daal sakta hai.",
    "not_now": "Aapki batayi jaankari ke hisaab se yeh loan abhi theek nahi baithta.",
    "paused": "Jaanch abhi ruki hai, pehle kuch jaankari chahiye.",
}
RISK_HI = {"low": "kam", "medium": "madhyam", "high": "zyada"}


def spoken(status, risk_band=None, affordable=None, requested_emi=None, needed=None):
    """Short, plain sentences for 'Listen to this result' (read by Gnani Timbre)."""
    en = [STATUS_TEXT[status]]
    hi = [STATUS_HI[status]]
    if status == "paused":
        en += needed or []
        hi += needed or []
    else:
        if affordable is not None:
            en.append(f"Your estimated affordable EMI for a new loan is {inr(affordable)} a month.")
            hi.append(f"Naye loan ke liye aapki anumanit affordable EMI {inr(affordable)} mahina hai.")
        if requested_emi is not None:
            en.append(f"The loan you asked for would have an EMI of about {inr(requested_emi)} a month.")
            hi.append(f"Aapke maange hue loan ki EMI lagbhag {inr(requested_emi)} mahina hogi.")
        if risk_band:
            en.append(f"Estimated risk is {risk_band}.")
            hi.append(f"Anumanit risk {RISK_HI[risk_band]} hai.")
    en.append("This is a pre-screening estimate, not a lender's decision.")
    hi.append("Yeh sirf pre-screening ka anumaan hai, lender ka faisla nahi.")
    return {"en": " ".join(en), "hi": " ".join(hi)}


def failed(checks):
    return [c for c in checks if c["state"] == "fail"]


def paused(checks):
    """The assessment shown when a safety check fails: what's needed, nothing else."""
    needed = [c["detail"] for c in failed(checks)]
    return {"status": "paused", "status_text": STATUS_TEXT["paused"],
            "decision_label": DECISION_LABELS["need_more_info"], "needed": needed,
            "safety": {"passed": False, "checks": checks}, "spoken": spoken("paused", needed=needed)}


def safety_post(applicant, afford, owed_limit_months, req_emi):
    income = applicant["monthly_income"]
    owed = applicant.get("active_loans_outstanding") or 0
    months = owed / income if income else 0
    over = owed > owed_limit_months * income
    checks = [_check("debt", "Debt checked", "flag" if over else "pass",
                     (f"{inr(owed)} still owed is {months:.1f} months of income, above the limit of "
                      f"{owed_limit_months}." if over else
                      f"{inr(owed)} still owed is {months:.1f} months of income (limit {owed_limit_months})."))]
    existing = afford["existing_emi"] if afford else 0
    share = (existing + req_emi) / income * 100 if income else 0
    fits = bool(afford and afford["fits_request"])
    checks.append(_check("affordability", "EMI affordability checked", "pass" if fits else "flag",
                         f"All EMIs together would be {share:.0f}% of income (limit "
                         f"{scoring.MAX_FOIR * 100:.0f}%)."))
    return checks


def _sentence(text):
    text = text.strip()
    return text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")


def build(applicant, risk, afford, decision, policy, model_up, model_down, checks, debt_limit_months):
    """The assessment for a completed pre-screen.

    model_up / model_down: plain-English model reasons already filtered by server.decide
    (spoken-safe factors only, direction-checked), as [(feature, text)].
    """
    a = applicant
    income, requested = a["monthly_income"], a["loan_amount"]
    tenure = int(afford["tenure_months"]) if afford else int(a.get("tenure_months") or scoring.DEFAULT_TENURE)
    existing_emi = afford["existing_emi"] if afford else (a.get("existing_emi") or 0)
    no_loans = a.get("active_loans") == 0
    emi_source = "you" if a.get("existing_emi") is not None or no_loans else "estimate"
    owed = a.get("active_loans_outstanding")

    max_new_emi = max(0.0, scoring.MAX_FOIR * income - existing_emi)
    req_emi = scoring.estimate_emi(requested, tenure)
    remaining = income - existing_emi - req_emi
    checks = checks + safety_post(a, afford, debt_limit_months, req_emi)

    if decision == "not_eligible_now":
        status = "pressure" if (policy or not (afford and afford["loan_amount"] > 0)
                                or req_emi > max_new_emi) else "not_now"
        suggested = None
    else:
        status = "within" if afford and afford["fits_request"] else "pressure"
        high = afford["loan_amount"]
        comfort = scoring.loan_for_emi(COMFORT_FOIR * income - existing_emi, tenure)
        low = math.floor(min(high, comfort) / 1000) * 1000
        if low >= high:
            text = inr(high)
        elif low <= 0:
            text = f"Up to {inr(high)}"
        else:
            text = f"{inr(low)} – {inr(high)}"
        suggested = {"low": int(max(low, 0)), "high": int(high), "text": text, "source": "rules",
                     "source_text": SOURCES["rules"] + " (all EMIs within 40% to 50% of income)",
                     "help": HELP["suggested_range"]}

    helping, against = [], []
    if req_emi <= max_new_emi:
        helping.append({"text": f"Requested EMI of {inr(req_emi)}/month is within your estimated affordability "
                                f"of {inr(max_new_emi)}/month.", "source": "rules"})
    else:
        against.append({"text": f"Requested EMI of {inr(req_emi)}/month is above your estimated affordability "
                                f"of {inr(max_new_emi)}/month.", "source": "rules"})
    if policy == "debt_over_limit":
        against.append({"text": f"Existing loans add up to more than {debt_limit_months // 12} years of income.",
                        "source": "rules"})
    # The EMI share is already covered by the rule above, in rupees.
    against += [{"text": _sentence(t), "source": "model"} for f, t in model_up
                if f != "ANNUITY_TO_INCOME" and not (policy and f in ("DEBT_TO_INCOME", "BUREAU_ACTIVE_DEBT"))]
    helping += [{"text": _sentence(t), "source": "model"} for f, t in model_down if f != "ANNUITY_TO_INCOME"]

    known = [
        ("Monthly income", True, ""),
        ("Requested loan", True, ""),
        ("Existing EMI", a.get("existing_emi") is not None or no_loans,
         "" if a.get("existing_emi") is not None or no_loans else "Estimated from your debt"),
        ("Existing debt", owed is not None or no_loans, "" if owed is not None or no_loans else "Not given"),
        ("Tenure", a.get("tenure_months") is not None,
         "" if a.get("tenure_months") is not None else f"Not given: {tenure} months used"),
        ("Work type", bool(a.get("income_type")), "" if a.get("income_type") else "Not given"),
        ("Years in work", "years_employed" in a, "" if "years_employed" in a else "Not given"),
        ("Housing", bool(a.get("housing")), "" if a.get("housing") else "Not given"),
        ("Age", a.get("age") is not None, "" if a.get("age") is not None else "Not given"),
        ("Repayment history", a.get("past_loans") is not None or a.get("ever_late") is not None,
         "As you told us" if a.get("past_loans") is not None or a.get("ever_late") is not None
         else "Not given"),
    ]
    completeness = [{"label": label, "state": "known" if ok else "missing", "note": note}
                    for label, ok, note in known]
    completeness += [{"label": f"{label} unavailable", "state": "unavailable", "note": note}
                     for label, note in UNAVAILABLE]

    share = (lambda x: round(max(0.0, x) / income * 100, 1)) if income else (lambda x: 0)
    return {
        "status": status,
        "status_text": STATUS_TEXT[status],
        "decision_label": DECISION_LABELS[decision],
        "risk": {"band": risk["band"], "text": risk["band"].upper(), "source": "model",
                 "source_text": SOURCES["model"], "help": HELP["risk"]},
        "affordability": {**_num(max_new_emi, "rules", suffix="/month"), "help": HELP["affordability"]},
        "requested_loan": _num(requested, "you"),
        "requested_emi": {**_num(req_emi, "calc", suffix="/month"), "help": HELP["requested_emi"]},
        "spoken": spoken(status, risk["band"], max_new_emi, req_emi),
        "suggested_range": suggested,
        "picture": {
            "monthly_income": _num(income, "you"),
            "existing_emi": _num(existing_emi, emi_source),
            "existing_debt": _num(owed or 0, "you") if owed is not None or no_loans else None,
            "new_emi": _num(req_emi, "calc"),
            "remaining_income": _num(remaining, "rules"),
            "requested_loan": _num(requested, "you"),
            "tenure": _num(tenure, "you" if a.get("tenure_months") is not None else "default", kind="months"),
            "emi_limit": _num(scoring.MAX_FOIR * income, "rules"),
            "shares": {"existing_emi": share(existing_emi), "new_emi": share(req_emi),
                       "remaining": share(remaining), "limit": scoring.MAX_FOIR * 100},
        },
        "why": {"helping": helping, "risk": against, "note": WHY_NOTE},
        "completeness": completeness,
        "safety": {"passed": not failed(checks), "checks": checks},
    }
