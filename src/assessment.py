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
from policy import POLICY
from scoring import inr

SOURCES = {
    "you": "You told us",
    "rules": "Based on affordability rules",
    "model": "Estimated by model",
    "calc": f"Calculated by Loan-Saathi at an assumed {scoring.ANNUAL_RATE * 100:g}% yearly interest",
    "calc_range": f"Calculated by Loan-Saathi across {scoring.rate_range_text()} yearly interest "
                  f"(the rules use {scoring.ANNUAL_RATE * 100:g}%)",
    "math": "Calculated by Loan-Saathi",
    "estimate": "Estimated from your existing debt (EMI not given)",
    "default": "Not given: Loan-Saathi's default used",
}
COMFORT_FOIR = POLICY["comfort_foir"]  # lower end of the suggested range (config/policy.yaml)
DECISION_LABELS = {
    "pre_approved": "Eligible for further review",
    "needs_review": "Eligible for advisor review",
    "counter_offer": "Smaller amount suggested",
    "not_eligible_now": "Not eligible right now",
    "need_more_info": "More information needed",
}
# What the borrower sees as the estimated pre-screening result. Plain and careful: there is no
# lender behind this, so nothing here claims an approval or a guarantee.
OUTCOME_TITLES = {
    "pre_approved": "Further review recommended",
    "needs_review": "Further review recommended",
    "counter_offer": "Lower-burden option recommended",
    "not_eligible_now": "Not right now",
    "need_more_info": "More information needed",
}
OUTCOME_TONE = {"pre_approved": "good", "needs_review": "good", "counter_offer": "warn",
                "not_eligible_now": "bad", "need_more_info": "info"}
BURDEN_TEXT = {
    "comfortable": "Within the comfortable range (up to {comfort}% of income).",
    "stretched": "Within the {limit}% limit, but above the comfortable {comfort}%.",
    "over": "Above the {limit}% limit the affordability rules allow.",
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
    if a.get("age") is not None and not POLICY["age_min"] <= a["age"] <= POLICY["age_max"]:
        bad.append(f"age ({POLICY['age_min']} to {POLICY['age_max']})")
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


def spoken(status, risk_band=None, affordable=None, requested_emi=None, needed=None, emi_range=None):
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
        if emi_range is not None:
            lo, hi_ = (inr(x) for x in emi_range)
            en.append(f"The loan you asked for would have an EMI of about {lo} to {hi_} a month, depending on the "
                      "lender's interest rate.")
            hi.append(f"Aapke maange hue loan ki EMI lagbhag {lo} se {hi_} mahina hogi, lender ke byaaj ke hisaab se.")
        elif requested_emi is not None:
            en.append(f"The loan you asked for would have an EMI of about {inr(requested_emi)} a month.")
            hi.append(f"Aapke maange hue loan ki EMI lagbhag {inr(requested_emi)} mahina hogi.")
        if risk_band:
            en.append(f"Estimated risk is {risk_band}.")
            hi.append(f"Anumanit risk {RISK_HI[risk_band]} hai.")
    en.append("This is a pre-screening estimate, not a lender's decision.")
    hi.append("Yeh sirf pre-screening ka anumaan hai, lender ka faisla nahi.")
    return {"en": " ".join(en), "hi": " ".join(hi)}


def _emi_figure(amount, tenure, emi):
    """An EMI as shown: the range across the policy's interest range; `value` is the assumed-rate EMI the
    rules use."""
    lo, hi = scoring.emi_range(amount, tenure)
    return {"value": int(round(emi)), "low": int(round(lo)), "high": int(round(hi)),
            "text": scoring.emi_range_text(amount, tenure) + "/month", "source": "calc_range",
            "source_text": SOURCES["calc_range"]}


def failed(checks):
    return [c for c in checks if c["state"] == "fail"]


def paused(checks):
    """The assessment shown when a safety check fails: what's needed, nothing else."""
    needed = [c["detail"] for c in failed(checks)]
    return {"status": "paused", "status_text": STATUS_TEXT["paused"],
            "outcome": {"tone": "info", "title": OUTCOME_TITLES["need_more_info"],
                        "detail": "Saathi needs a few details cleared up before it can estimate anything."},
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


def outcome(decision, band, afford, policy, debt_limit_months):
    """The borrower-facing result: the existing decision, said carefully. Only explains causes the
    decision logic (server.decide) actually used: the risk band, the affordability rule, the debt rule."""
    if decision in ("pre_approved", "needs_review"):
        detail = "The loan you asked for fits within the affordability rules"
        detail += (", and the model's estimated risk is low." if band == "low" else
                   ". The model's estimated risk is medium, so a loan advisor should look at it with you.")
        detail += " A lender would still check your documents before any decision."
    elif decision == "counter_offer":
        detail = ("The full amount would put too much pressure on your monthly income. A smaller loan "
                  "keeps all your EMIs within the affordability rules.")
    elif policy == "debt_over_limit":
        detail = (f"Your existing loans add up to more than {debt_limit_months // 12} years of income. "
                  "Paying some of them down first would help.")
    elif not (afford and afford["loan_amount"] > 0):
        detail = ("With your current income and EMIs, there isn't room for a new EMI within the "
                  "affordability rules.")
    else:
        detail = "Based on what you shared, the model's estimated risk is high, so a new loan isn't advised now."
    return {"tone": OUTCOME_TONE[decision], "title": OUTCOME_TITLES[decision], "detail": detail}


def path_to_yes(applicant, decision, band, afford, policy, debt_limit_months):
    """What would change the answer, from the deterministic rules only. Each item names a concrete figure
    the affordability or debt rule computes; nothing comes from the model, so a high risk estimate is said
    to be the reason without promising an amount that would change it."""
    if decision in ("pre_approved", "needs_review"):
        return []
    a = applicant
    income, requested = a["monthly_income"], a["loan_amount"]
    tenure = int(afford["tenure_months"]) if afford else int(a.get("tenure_months") or scoring.DEFAULT_TENURE)
    existing = afford["existing_emi"] if afford else (a.get("existing_emi") or 0)
    owed = a.get("active_loans_outstanding") or 0
    room = max(0.0, scoring.MAX_FOIR * income - existing)
    req_emi = scoring.estimate_emi(requested, tenure)
    out = []

    def item(en, hi):
        out.append({"text": en, "text_hi": hi, "source": "rules"})

    if policy == "debt_over_limit":
        limit = debt_limit_months * income
        item(f"If what you still owe on other loans drops below {inr(limit)} (now {inr(owed)}), the debt rule "
             "would no longer stop a new loan.",
             f"Agar dusre loans ka baaki {inr(limit)} se kam ho jaaye (abhi {inr(owed)}), toh yeh rule naye loan "
             "ko nahi rokega.")
    if req_emi > room + 0.5:
        allowed_existing = scoring.MAX_FOIR * income - req_emi
        if allowed_existing >= 0 and existing > allowed_existing:
            item(f"If your other EMIs drop from {inr(existing)} to {inr(allowed_existing)} a month or less, the "
                 f"{inr(requested)} you asked for would fit the affordability rule.",
                 f"Agar aapki dusri EMI {inr(existing)} se ghatkar {inr(allowed_existing)} mahina ya kam ho jaaye, "
                 f"toh maanga hua {inr(requested)} affordability rule mein aa jaayega.")
        longer = next((m for m in range(tenure + 1, 121) if scoring.estimate_emi(requested, m) <= room), None)
        if longer and room > 0:
            item(f"Over {longer} months instead of {tenure}, the EMI would be about "
                 f"{inr(scoring.estimate_emi(requested, longer))} a month, within the rule.",
                 f"{tenure} ki jagah {longer} mahine mein EMI lagbhag {inr(scoring.estimate_emi(requested, longer))} "
                 "mahina hogi, jo rule ke andar hai.")
        fits = math.floor(scoring.loan_for_emi(room, tenure) / 1000) * 1000 if room > 0 else 0
        if fits >= 1000:
            item(f"With your current EMIs, a loan of up to {inr(fits)} over {tenure} months fits now.",
                 f"Abhi ki EMI ke saath, {tenure} mahine mein {inr(fits)} tak ka loan rule mein aata hai.")
        elif room <= 0:
            item(f"Your existing EMIs already use half of your income ({inr(existing)} of {inr(income)}), so "
                 "paying some of them down comes first.",
                 f"Aapki maujooda EMI pehle se aadhi kamaai le rahi hai ({inr(income)} mein se {inr(existing)}), "
                 "isliye pehle unhe kam karna hoga.")
    if band == "high" and decision == "not_eligible_now":
        item("The estimated risk is high, and that comes from the model, so we can't name a figure that would "
             "change it. A loan advisor can look at your situation.",
             "Anumanit risk zyada hai, aur yeh model se aata hai, isliye hum koi rakam nahi bata sakte jo ise "
             "badle. Loan advisor aapki sthiti dekh sakte hain.")
        out[-1]["source"] = "model"
    return out


def burden(existing_emi, new_emi, income):
    """All EMIs as a share of income against the same 40% / 50% lines the suggested range uses."""
    if not income:
        return None
    pct = (existing_emi + new_emi) / income * 100
    limit, comfort = round(scoring.MAX_FOIR * 100), round(COMFORT_FOIR * 100)
    level = "comfortable" if pct <= comfort else "stretched" if pct <= limit else "over"
    return {"percent": round(pct), "limit": limit, "comfort": comfort, "level": level,
            "text": f"{round(pct)}% of your monthly income would go to EMIs",
            "detail": BURDEN_TEXT[level].format(limit=limit, comfort=comfort),
            "source": "rules", "source_text": SOURCES["rules"]}


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
        "outcome": outcome(decision, risk["band"], afford, policy, debt_limit_months),
        "path_to_yes": path_to_yes(a, decision, risk["band"], afford, policy, debt_limit_months),
        "risk": {"band": risk["band"], "text": risk["band"].upper(), "source": "model",
                 "source_text": SOURCES["model"], "help": HELP["risk"]},
        "affordability": {**_num(max_new_emi, "rules", suffix="/month"), "help": HELP["affordability"]},
        "requested_loan": _num(requested, "you"),
        "requested_emi": {**_emi_figure(requested, tenure, req_emi), "help": HELP["requested_emi"]},
        "spoken": spoken(status, risk["band"], max_new_emi, req_emi, emi_range=scoring.emi_range(requested, tenure)),
        "suggested_range": suggested,
        "picture": {
            "monthly_income": _num(income, "you"),
            "existing_emi": _num(existing_emi, emi_source),
            "existing_debt": _num(owed or 0, "you") if owed is not None or no_loans else None,
            "new_emi": _num(req_emi, "calc"),
            "total_obligations": _num(existing_emi + req_emi, "math"),
            "remaining_income": _num(remaining, "math"),
            "burden": burden(existing_emi, req_emi, income),
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
