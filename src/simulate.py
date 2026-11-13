"""What-if simulator: how a different loan amount or tenure would change the picture.

Every number comes from the same deterministic code as the real assessment:
server.decide() (safety check, risk model, affordability rule, debt rule) on a copy of the confirmed
applicant with only the loan amount and/or tenure changed. Nothing here is an offer: there is no
lender, no real interest rate (EMIs use the model's assumed 18% a year, and say so) and no approval.

    run(applicant, loan, tenure, decide, context)   one simulation
    scenarios(applicant, decide, context)           safer option / your request / higher burden
    compare(original, simulation)                   what changed, with arrows the UI can show
"""
import math

import policy
import scoring
from policy import POLICY
from scoring import inr

LABEL = "Simulation only — not a lender offer or approval."
MIN_LOAN, MAX_LOAN = POLICY["limits"]["loan_amount"][0], POLICY["limits"]["loan_amount"][2]
MIN_TENURE, MAX_TENURE = POLICY["tenure_min"], POLICY["tenure_max"]
COMFORT_FOIR = POLICY["comfort_foir"]    # the "safer option": all EMIs within this share of income
HIGHER_FACTOR = POLICY["higher_factor"]  # the "higher burden" example: this many times the request
SOURCE = {"simulation": "Your simulation",
          "calc": f"Calculated by Loan-Saathi at an assumed {scoring.ANNUAL_RATE * 100:g}% yearly interest",
          "calc_range": f"Calculated by Loan-Saathi across {scoring.rate_range_text()} yearly interest "
                        f"(the rules use {scoring.ANNUAL_RATE * 100:g}%)",
          "rules": "Based on affordability rules",
          "model": "Estimated by model"}
BAND_ORDER = {"low": 0, "medium": 1, "high": 2}


class InvalidSimulation(ValueError):
    """A loan amount or tenure the simulator refuses; the message is safe to show the applicant."""


def _num(value, source, kind="money", suffix=""):
    if kind == "months":
        value = int(value)
        return {"value": value, "text": f"{value} months", "source": source, "source_text": SOURCE[source]}
    value = int(round(value))
    return {"value": value, "text": inr(value) + suffix, "source": source, "source_text": SOURCE[source]}


def _emi(loan, tenure, emi):
    """The EMI figure: `value` is the EMI at the assumed rate (what the rules use); `low`/`high` and the text
    show the range across the policy's interest range."""
    lo, hi = scoring.emi_range(loan, tenure)
    return {"value": int(round(emi)), "low": int(round(lo)), "high": int(round(hi)),
            "text": scoring.emi_range_text(loan, tenure) + "/month", "source": "calc_range",
            "source_text": SOURCE["calc_range"]}


def check_inputs(loan, tenure):
    """Validated (loan, tenure) or InvalidSimulation with a plain reason."""
    try:
        loan = float(loan)
        tenure = int(round(float(tenure)))
    except (TypeError, ValueError):
        raise InvalidSimulation("Please enter the loan amount and tenure as numbers.") from None
    if not math.isfinite(loan) or loan < MIN_LOAN:
        raise InvalidSimulation(f"The loan amount must be at least {inr(MIN_LOAN)}.")
    if loan > MAX_LOAN:
        raise InvalidSimulation(f"The loan amount can be at most {inr(MAX_LOAN)}.")
    if not MIN_TENURE <= tenure <= MAX_TENURE:
        raise InvalidSimulation(f"The tenure must be between {policy.months_text(MIN_TENURE)} and "
                                f"{policy.months_text(MAX_TENURE)} ({MAX_TENURE} months).")
    return math.floor(loan), tenure


def run(applicant, loan, tenure, decide, context=None, kind="custom"):
    """One simulation through the real decision path. Never raises for a valid input: a safety
    check that fails comes back as status "paused" with what's needed."""
    loan, tenure = check_inputs(loan, tenure)
    app = {**applicant, "loan_amount": loan, "tenure_months": tenure}
    result, _ = decide(app, None, context)
    a = result.get("assessment") or {}
    income = applicant.get("monthly_income") or 0
    emi = scoring.estimate_emi(loan, tenure)
    existing = (a.get("picture") or {}).get("existing_emi", {}).get("value", applicant.get("existing_emi") or 0)
    max_new = (a.get("affordability") or {}).get("value")
    out = {
        "kind": kind,
        "label": LABEL,
        "loan_amount": _num(loan, "simulation"),
        "tenure": _num(tenure, "simulation", kind="months"),
        "emi": _emi(loan, tenure, emi),
        "total_repayment": _num(emi * tenure, "calc"),
        "total_interest": _num(emi * tenure - loan, "calc"),
        "status": a.get("status", "paused"),
        "status_text": a.get("status_text", ""),
        "outcome": a.get("outcome"),
    }
    if out["status"] == "paused":
        out["needed"] = a.get("needed", [])
        return out
    out.update({
        "affordability": _num(max_new, "rules", suffix="/month"),
        "headroom": _num(max_new - emi, "rules", suffix="/month"),
        "fits": emi <= max_new + 0.5,
        "emi_share": round((existing + emi) / income * 100, 1) if income else None,
        "total_obligations": _num(existing + emi, "calc", suffix="/month"),
        "burden": (a.get("picture") or {}).get("burden"),
        "shares": (a.get("picture") or {}).get("shares"),
        "risk": {"band": a["risk"]["band"], "text": a["risk"]["text"], "source": "model",
                 "source_text": SOURCE["model"]},
        "debt_over_limit": result.get("policy_rule") == "debt_over_limit",
    })
    return out


def scenarios(applicant, decide, context=None):
    """Three illustrations around the request. The safer option keeps all EMIs within 40% of income
    (or, if the request already does, borrows a quarter less); the higher-burden one borrows 50%
    more. Same tenure as the request, so only the amount differs."""
    loan = applicant["loan_amount"]
    tenure = int(applicant.get("tenure_months") or scoring.DEFAULT_TENURE)
    income = applicant["monthly_income"]
    existing = applicant.get("existing_emi")
    if existing is None:
        existing = scoring.estimate_emi(applicant.get("active_loans_outstanding") or 0, scoring.DEFAULT_TENURE)
    comfort = scoring.loan_for_emi(COMFORT_FOIR * income - existing, tenure)
    safer_amount = math.floor(comfort / 1000) * 1000

    out = []
    if MIN_LOAN <= safer_amount < loan:
        s = run(applicant, safer_amount, tenure, decide, context, kind="safer")
        s.update(title="Lower-burden option",
                 blurb="The largest amount that keeps all your EMIs within 40% of your income.")
        out.append(s)
    elif safer_amount >= loan:
        out.append({"kind": "safer", "title": "Lower-burden option", "label": LABEL, "unavailable": True,
                    "blurb": "Your request already keeps all your EMIs within 40% of your income."})
    else:
        out.append({"kind": "safer", "title": "Lower-burden option", "label": LABEL, "unavailable": True,
                    "blurb": "No lower-burden amount: your existing EMIs already use 40% or more of your income."})
    r = run(applicant, loan, tenure, decide, context, kind="request")
    r.update(title="Your request", blurb="The loan you asked for.")
    out.append(r)
    higher = math.floor(min(loan * HIGHER_FACTOR, MAX_LOAN) / 1000) * 1000
    h = run(applicant, higher, tenure, decide, context, kind="higher")
    h.update(title="Higher burden", blurb="Borrowing more raises your monthly burden.")
    out.append(h)
    return out


def _delta(new, old):
    d = new - old
    return {"value": int(round(d)), "text": ("+" if d > 0 else "−" if d < 0 else "") + inr(abs(d)),
            "direction": "up" if d > 0.5 else "down" if d < -0.5 else "same"}


def compare(original, sim):
    """What changed between the original request and a simulation, for arrows in the UI."""
    if sim.get("status") == "paused" or original.get("status") == "paused":
        return None
    out = {
        "loan_amount": _delta(sim["loan_amount"]["value"], original["loan_amount"]["value"]),
        "tenure": {"value": sim["tenure"]["value"] - original["tenure"]["value"],
                   "text": f"{sim['tenure']['value'] - original['tenure']['value']:+d} months",
                   "direction": "up" if sim["tenure"]["value"] > original["tenure"]["value"] else
                   "down" if sim["tenure"]["value"] < original["tenure"]["value"] else "same"},
        "emi": _delta(sim["emi"]["value"], original["emi"]["value"]),
        "total_repayment": _delta(sim["total_repayment"]["value"], original["total_repayment"]["value"]),
        "headroom": _delta(sim["headroom"]["value"], original["headroom"]["value"]),
    }
    a, b = BAND_ORDER[original["risk"]["band"]], BAND_ORDER[sim["risk"]["band"]]
    heavier = out["emi"]["direction"] == "up" or out["loan_amount"]["direction"] == "up"
    lighter = out["emi"]["direction"] == "down" or out["loan_amount"]["direction"] == "down"
    if b > a:
        risk = ("up", "Estimated risk goes up.")
    elif b < a and not heavier:
        risk = ("down", "Estimated risk goes down.")
    elif b < a:  # a heavier loan the model rates lower: don't present that as safer
        out["risk"] = {"direction": "same", "recalculated": False,
                       "text": "Risk estimate not recalculated: the model can't reliably rate a larger loan as "
                               "safer, so your original estimate stands."}
        return out
    else:
        risk = ("same", "Estimated risk stays about the same."
                if not lighter else "Estimated risk band stays the same; a lower EMI still eases your budget.")
    out["risk"] = {"direction": risk[0], "text": risk[1], "recalculated": True}
    return out


def spoken(sim, lang="en"):
    """A short deterministic sentence about a simulation, for the voice call."""
    if sim.get("status") == "paused":
        need = " ".join(sim.get("needed") or [])
        return (f"Is simulation ke liye pehle yeh chahiye: {need}" if lang == "hi"
                else f"Before I can simulate that: {need}")
    loan, n = sim["loan_amount"]["text"], sim["tenure"]["value"]
    e = sim["emi"]
    emi = (f"{inr(e['low'])} se {inr(e['high'])}" if lang == "hi" else f"{inr(e['low'])} to {inr(e['high'])}") \
        if "low" in e else e["text"].replace("/month", "")
    total = sim["total_repayment"]["text"]
    cap = sim["affordability"]["text"].replace("/month", "")
    if lang == "hi":
        fit = (f"Yeh aapki anumanit affordability {cap} mahina ke andar hai." if sim["fits"] else
               f"Yeh aapki anumanit affordability {cap} mahina se zyada hai.")
        debt = " Lekin aapke maujooda loan 2 saal ki kamaai se zyada hain." if sim["debt_over_limit"] else ""
        return (f"Simulation: {loan}, {n} mahine mein, EMI lagbhag {emi} mahina, kul chukana lagbhag {total}. "
                f"{fit}{debt} Yeh sirf simulation hai, lender ka offer ya approval nahi.")
    fit = (f"That is within your estimated affordability of {cap} a month." if sim["fits"] else
           f"That is above your estimated affordability of {cap} a month.")
    debt = " But your existing loans are more than 2 years of income." if sim["debt_over_limit"] else ""
    return (f"Simulation: {loan} over {n} months, EMI about {emi} a month, about {total} repaid in total. "
            f"{fit}{debt} This is a simulation only, not a lender offer or approval.")
