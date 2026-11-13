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

import normalize as norm
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
          conversation=None, recent=None, channel="dashboard", prescreen_id="", session_id="",
          workspace=None):
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
        if key == "work" and v:  # category first, then the caller's own words as evidence
            text = norm.work_label(v)
            if norm.work_category(v) != "other":
                text += f' \u2014 "{v}"'
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
            "outcome": (x.get("outcome") or {}).get("title") or x["decision_label"],
            "burden": ((x.get("picture") or {}).get("burden") or {}).get("text"),
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
        **(workspace or {}),
        "flags": (workspace or {}).get("flags", []) + result_flags(result, a),
    }


# ---------- the advisor workspace: corrections, flags, timeline, transcript ----------

FIELD_KIND = {"loan_amount": "money", "monthly_income": "money_month", "other_loans_emi": "money_month",
              "other_loans_outstanding": "money", "tenure_months": "months", "other_loans_count": "count",
              "past_loans": "count", "years_in_work": "years", "age": "years"}
FIELD_TITLE = {"loan_amount": "Requested loan", "monthly_income": "Income", "other_loans_emi": "Existing EMI",
               "other_loans_outstanding": "Existing debt", "tenure_months": "Tenure", "other_loans_count": "Other loans",
               "work": "Work", "years_in_work": "Years in work", "housing": "Housing", "age": "Age",
               "past_loans": "Loans taken before", "ever_refused": "Ever refused", "ever_late": "Ever paid late",
               "late_last_year": "Late in the last 12 months", "late_over_month": "Over a month late",
               "overdue_now": "EMI overdue now"}
CRITICAL_FIELDS = ("loan_amount", "monthly_income")


def _field_text(k, v):
    if isinstance(v, bool) or k in ("ever_refused", "ever_late", "late_last_year", "late_over_month", "overdue_now"):
        return _fmt("yesno", v)
    return _fmt(FIELD_KIND.get(k, "text"), v)


def _flagged(kind, label, detail, source):
    return {"kind": kind, "label": label, "detail": detail, "source": source}


def result_flags(result, applicant):
    """Flags the decision itself raised: its safety checks, the debt rule, a missing assessment."""
    out = []
    if not result:
        return out
    x = result.get("assessment")
    if x is None and result.get("decision") not in (None, "need_more_info"):
        out.append(_flagged("assessment_unavailable", "Assessment details unavailable",
                            "The decision was made, but its explanation couldn't be built.", "server"))
    if result.get("policy_rule") == "debt_over_limit":
        owed = applicant.get("active_loans_outstanding")
        out.append(_flagged("excessive_debt", "Excessive existing debt",
                            f"{inr(owed)} still owed is more than 2 years of income (debt rule)." if owed
                            else "Existing debt is more than 2 years of income (debt rule).", "rules"))
    for c in ((x or {}).get("safety") or {}).get("checks", []):
        if c["state"] == "fail":
            kind = {"required": "missing_critical", "contradictions": "contradiction",
                    "consistency": "contradiction"}.get(c["id"], c["id"])
            out.append(_flagged(kind, c["label"] + ": needs attention", c["detail"], "safety check"))
        elif c["state"] == "flag" and c["id"] == "affordability":
            out.append(_flagged("affordability", "Above the affordability limit", c["detail"], "rules"))
        elif c["state"] == "flag" and c["id"] == "debt" and result.get("policy_rule") != "debt_over_limit":
            out.append(_flagged("excessive_debt", "High existing debt", c["detail"], "rules"))
    if result.get("decision") == "need_more_info" and result.get("missing_fields"):
        out.append(_flagged("missing_critical", "Missing critical information", result["missing_fields"], "server"))
    if x and x.get("status") != "paused":
        unverified = [i["label"].replace(" unavailable", "") for i in x.get("completeness", [])
                      if i["state"] == "unavailable"]
        if unverified:
            out.append(_flagged("unverified", "Unverified information",
                                "Self-reported, not checked: " + ", ".join(unverified).lower() + ".", "pre-screening"))
    return out


def workspace(s, case_ts=None):
    """Corrections, flags, a timeline and the transcript of a web call, all from what the session
    recorded. Nothing is reconstructed: a field with no change log has no correction."""
    answers, status, said = s.get("answers", {}), s.get("status", {}), s.get("said", {})

    corrections = []
    for k in dict.fromkeys(c["field"] for c in s.get("change_log", [])):
        log = [c for c in s["change_log"] if c["field"] == k]
        first, final = log[0]["before"], answers.get(k, log[-1]["after"])
        if _same_value(first, final):
            continue
        corrections.append({
            "field": k, "label": FIELD_TITLE.get(k, k),
            "before": _field_text(k, first), "after": _field_text(k, final),
            "status": _status_label(status.get(k)),
            "said": said.get(k, ""),
            "how": "Corrected at the read-back" if any(c["how"] == "correction" for c in log)
            else "Clarified during the questions",
            "steps": len(log)})

    flags = []
    for f in s.get("flag_log", []):
        title = FIELD_TITLE.get(f["field"], f["field"])
        if f["kind"] == "contradiction":
            vals = " and ".join(_field_text(f["field"], v) for v in f["values"])
            flags.append(_flagged("contradiction", f"Contradictory {title.lower()}",
                                  f"Two values were heard: {vals}. Saathi asked which one is right.", "conversation"))
        elif f["kind"] == "loans_conflict":
            flags.append(_flagged("contradiction", "Contradictory loan details",
                                  f"No other loans were mentioned, then {title.lower()} "
                                  f"{_field_text(f['field'], f['values'][1])}. Saathi asked.", "conversation"))
    missing = [FIELD_TITLE[k] for k in CRITICAL_FIELDS if k not in answers]
    if missing:
        flags.append(_flagged("missing_critical", "Missing critical information",
                              "Not given: " + ", ".join(missing).lower() + ".", "conversation"))
    skipped = [FIELD_TITLE.get(k, k) for k in sorted(s.get("skipped") or [])]
    if skipped:
        flags.append(_flagged("skipped", "Questions skipped",
                              "Not answered: " + ", ".join(skipped).lower() + ".", "conversation"))
    if s.get("recoveries"):
        flags.append(_flagged("extraction", "Trouble understanding answers",
                              f"Saathi couldn't understand the caller {s['recoveries']} time(s) and asked again.",
                              "conversation"))
    used = s.get("llm", [])
    fallback = sum(1 for u in used if u == "fallback")
    if fallback:
        every = fallback == len(used)
        flags.append(_flagged("extraction", "Language model unavailable" if every
                              else "Language model unavailable on some turns",
                              f"On {fallback} of {len(used)} turns Saathi's own parser read the answer "
                              "instead of Gemini.", "conversation"))
    unconfirmed = [FIELD_TITLE.get(k, k) for k in answers if status.get(k) != "confirmed"]
    if unconfirmed and answers:
        flags.append(_flagged("unconfirmed", "Not confirmed by the applicant",
                              "Heard but not confirmed: " + ", ".join(unconfirmed).lower() + ".", "conversation"))

    events = []
    trail = s.get("trail", [])
    first_of = {}
    for t in trail:
        first_of.setdefault(t["to"], t)
    if trail:
        events.append({"ts": trail[0]["ts"], "label": "Call started",
                       "detail": "Hindi/Hinglish" if s.get("lang") == "hi" else "English"})
    if "VERIFYING_INFORMATION" in first_of:
        events.append({"ts": first_of["VERIFYING_INFORMATION"]["ts"], "label": "Information collected",
                       "detail": "Saathi read the profile back"})
    for t in trail:
        if t["to"] == "CORRECTION_REQUIRED":
            field = t["why"].replace("caller wants to change ", "")
            events.append({"ts": t["ts"], "label": "Correction requested",
                           "detail": f"Caller wanted to change: {FIELD_TITLE.get(field, field).lower()}"
                           if field in FIELD_TITLE else t["why"].capitalize()})
        if t["to"] == "ERROR_RECOVERY":
            events.append({"ts": t["ts"], "label": "Saathi couldn't understand", "detail": t["why"].capitalize()})
    for c in s.get("change_log", []):
        events.append({"ts": c["ts"], "label": f"{FIELD_TITLE.get(c['field'], c['field'])} changed",
                       "detail": f"{_field_text(c['field'], c['before'])} → {_field_text(c['field'], c['after'])}"})
    if "CONFIRMED" in first_of:
        events.append({"ts": first_of["CONFIRMED"]["ts"], "label": "Profile confirmed", "detail": "By the applicant"})
    if "SHOWING_RESULT" in first_of:
        label = ((s.get("result") or {}).get("assessment") or {}).get("outcome", {}).get("title")
        events.append({"ts": first_of["SHOWING_RESULT"]["ts"], "label": "Assessment completed",
                       "detail": label or ""})
    for e in s.get("sim_events", []):
        events.append({"ts": e["ts"], "label": "Simulation performed", "detail": e["text"]})
    if case_ts:
        events.append({"ts": case_ts, "label": "Advisor requested", "detail": "Case saved for a loan advisor"})
    events.sort(key=lambda e: e["ts"])

    transcript = [{"who": "caller" if who != "Saathi" else "saathi", "text": text}
                  for who, text in s.get("history", [])]
    return {"corrections": corrections, "flags": flags, "timeline": events, "transcript": transcript}


def _same_value(a, b):
    try:
        return abs(float(a) - float(b)) < 0.5
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


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


def digest(cases, now):
    """A short daily summary of advisor cases still waiting: counts, the oldest wait, and one line per
    case (headline and question). Built from the saved cases only."""
    waiting = [c for c in cases if c.get("status", "open") in ("open", "in_review")]
    opened = [c for c in waiting if c.get("status", "open") == "open"]
    review = [c for c in waiting if c.get("status") == "in_review"]
    oldest = max(((now - c["ts"]) / 3600 for c in opened), default=0)
    lines = [f"#{c['id']} ({'open' if c.get('status', 'open') == 'open' else 'in review'}, "
             f"{(now - c['ts']) / 3600:.0f} h): {c.get('headline', '')}. Question: {c.get('question', '')}"
             for c in sorted(waiting, key=lambda c: c["ts"])[:20]]
    head = (f"Loan-Saathi daily digest: {len(opened)} open and {len(review)} in-review advisor case(s)"
            + (f"; the oldest open case has waited {oldest:.0f} hours." if opened else "."))
    text = head if not lines else head + chr(10) + chr(10).join(lines)
    if len(waiting) > 20:
        text += chr(10) + f"...and {len(waiting) - 20} more in the dashboard's Advisor cases view."
    return {"open": len(opened), "in_review": len(review), "oldest_open_hours": round(oldest, 1),
            "lines": lines, "text": text}

