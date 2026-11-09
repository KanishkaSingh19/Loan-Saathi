"""Advisor cases: a structured summary for a human loan advisor, built from the session.

When an applicant asks to talk to a loan advisor (the button, or by voice), Loan-Saathi saves a case
in the `advisor_cases` table and shows it in the dashboard's "Advisor cases" view. This is a
prototype: nothing notifies an advisor, and the applicant is told so (PROTOTYPE_NOTE).

Everything in a case is copied from what the system already has: the caller's answers (with their
confirmation status), the assessment, the current simulation (recomputed on the server), the
caller's question and a deterministic conversation summary. No LLM writes any part of it.
"""
import time
import uuid

from scoring import DEFAULT_TENURE, inr

PROTOTYPE_NOTE = ("Prototype: this case is saved in the Loan-Saathi dashboard for a loan advisor to pick up. "
                  "No advisor is notified automatically.")
# applicant key -> (answer key, label, kind)
PROFILE = [
    ("monthly_income", "monthly_income", "Income", "money_month"),
    ("existing_emi", "other_loans_emi", "Existing EMI", "money_month"),
    ("active_loans_outstanding", "other_loans_outstanding", "Existing debt", "money"),
    ("active_loans", "other_loans_count", "Other loans running", "count"),
    ("past_loans", "past_loans", "Loans taken before", "count"),
    ("ever_refused", "ever_refused", "Ever refused", "yesno"),
    ("ever_late", "ever_late", "Ever paid an EMI late", "yesno"),
    ("late_last_year", "late_last_year", "Late in the last 12 months", "yesno"),
    ("overdue_now", "overdue_now", "EMI overdue now", "yesno"),
]
APPLICANT = [("work", "Work"), ("years_in_work", "Years in work"), ("housing", "Housing"), ("age", "Age")]
STATE_NAMES = {
    "COLLECTING_PROFILE": "answering questions", "VERIFYING_INFORMATION": "checking the summary",
    "CORRECTION_REQUIRED": "correcting an answer", "SHOWING_RESULT": "the result",
    "WHAT_IF": "trying what-if options", "NEXT_STEPS": "next steps", "ERROR_RECOVERY": "error recovery",
}


def _fmt(kind, v):
    if v is None:
        return "Not given"
    if kind == "money":
        return inr(v)
    if kind == "money_month":
        return inr(v) + "/month"
    if kind == "months":
        return f"{int(v)} months"
    if kind == "years":
        n = round(float(v), 1)
        return f"{int(n) if n == int(n) else n} years"
    if kind == "count":
        return str(int(v))
    if kind == "yesno":
        return "Yes" if v else "No"
    return str(v)


def _status_label(status):
    return {"confirmed": "Confirmed by applicant", "validated": "Heard, not yet confirmed"}.get(
        status, "As heard on the call")


def build(applicant, answers=None, statuses=None, result=None, simulation=None, question="",
          conversation=None, recent=None, channel="dashboard", prescreen_id="", session_id=""):
    """The case payload. `answers`/`statuses` come from a web call (may be partial); `applicant` is the
    normalised profile (server.build_applicant)."""
    answers, statuses = answers or {}, statuses or {}
    a = applicant or {}
    profile = []
    for akey, answer_key, label, kind in PROFILE:
        v = a.get(akey)
        if v is None and a.get("active_loans") == 0 and akey in ("existing_emi", "active_loans_outstanding"):
            v = 0
        profile.append({"label": label, "text": _fmt(kind, v),
                        "status": _status_label(statuses.get(answer_key)) if answer_key in answers or not answers
                        else "Not asked yet"})
    request = {
        "loan": _fmt("money", a.get("loan_amount")),
        "tenure": _fmt("months", a["tenure_months"]) if a.get("tenure_months")
        else f"Not given ({DEFAULT_TENURE} months used)",
    }
    who = []
    for key, label in APPLICANT:
        v = answers.get(key)
        if v is None and key == "age":
            v = a.get("age")
        text = _fmt("years", v) if key in ("years_in_work", "age") and v is not None else (
            str(v) if v is not None else "Not given")
        who.append({"label": label, "text": text})

    assessed = None
    if result and result.get("assessment") and result["assessment"].get("status") != "paused":
        x = result["assessment"]
        assessed = {
            "status_text": x["status_text"], "label": x["decision_label"],
            "risk": x["risk"]["text"].capitalize(),
            "affordable_emi": x["affordability"]["text"],
            "requested_emi": x["requested_emi"]["text"],
            "suggested_range": (x.get("suggested_range") or {}).get("text") or "Not available",
            "why_helping": [i["text"] for i in x["why"]["helping"]],
            "why_risk": [i["text"] for i in x["why"]["risk"]],
        }
        completeness = [{"label": i["label"], "state": i["state"]} for i in x["completeness"]]
    else:
        completeness = [{"label": label, "state": "known" if answer_key in answers or a.get(akey) is not None
                         else "missing"} for akey, answer_key, label, _ in PROFILE]
        completeness.append({"label": "Credit bureau report unavailable", "state": "unavailable"})

    sim = None
    if simulation and simulation.get("status") != "paused":
        sim = {"loan": simulation["loan_amount"]["text"], "tenure": f"{simulation['tenure']['value']} months",
               "emi": simulation["emi"]["text"],
               "fits": "Within estimated affordability" if simulation.get("fits") else "Above estimated affordability",
               "label": simulation.get("label", "")}

    headline = f"{request['loan']} over {request['tenure']}"
    if a.get("monthly_income"):
        headline += f" · income {inr(a['monthly_income'])}/month"
    if assessed:
        headline += f" · {assessed['risk']} estimated risk"

    return {
        "id": uuid.uuid4().hex[:10],
        "ts": time.time(),
        "status": "open",
        "channel": channel,
        "prototype_note": PROTOTYPE_NOTE,
        "headline": headline,
        "applicant": who,
        "financial_profile": profile,
        "request": request,
        "assessment": assessed,
        "simulation": sim,
        "question": (question or "").strip()[:500] or "No question given.",
        "conversation": conversation or [],
        "recent": recent or [],
        "completeness": completeness,
        "prescreen_id": prescreen_id,
        "session_id": session_id,
    }


def conversation_summary(s):
    """Deterministic bullets about a web call, from its state trail and log."""
    out = [f"Web call in {'Hindi/Hinglish' if s['lang'] == 'hi' else 'English'}."]
    trail = s.get("trail", [])
    changed = [t["why"].replace("caller wants to change ", "") for t in trail
               if t["to"] == "CORRECTION_REQUIRED" and t["why"].startswith("caller wants to change")]
    if any(t["to"] == "CONFIRMED" for t in trail):
        out.append("Applicant confirmed the profile Saathi read back.")
    else:
        out.append(f"Profile not confirmed yet (stopped while {STATE_NAMES.get(s['state'], s['state'].lower())}).")
    if changed:
        out.append("Corrected during the call: " + ", ".join(c.replace("_", " ") for c in changed) + ".")
    if s.get("result"):
        label = (s["result"].get("assessment") or {}).get("decision_label") or s["result"]["decision"]
        out.append(f"Pre-screening result: {label}.")
    for sim in s.get("sim_log", [])[-3:]:
        out.append(f"Tried a simulation: {sim}.")
    if s.get("recoveries"):
        out.append(f"Saathi had trouble understanding {s['recoveries']} time(s).")
    return out


def recent_turns(s, n=8):
    return [f"{who}: {text}" for who, text in s.get("history", [])[-n:]]
