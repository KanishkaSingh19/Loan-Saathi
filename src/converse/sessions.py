"""Sessions, the confirmation summary, buttons and what the call screen sees."""

import time
import uuid

import normalize as norm
from scoring import inr

from .states import COLLECTING, COMPLETED, CORRECTION, FINISHED, RECOVERY, RESULT, stage, START, transition, VERIFYING
from .fields import BY_KEY, CARD, CRITICAL, DEFAULT_TENURE, KEYS, LABELS, SUMMABLE, TITLES
from .texts import GREETING, _help_buttons, PRIVACY, SAY
from .validation import fmt, next_field


# ---------- sessions ----------

SESSIONS = {}
SESSION_TTL = 30 * 60
MAX_SESSIONS = 500


def _prune():
    now = time.time()
    for sid in [k for k, s in SESSIONS.items() if now - s["updated"] > SESSION_TTL]:
        SESSIONS.pop(sid, None)
    while len(SESSIONS) >= MAX_SESSIONS:
        SESSIONS.pop(min(SESSIONS, key=lambda k: SESSIONS[k]["updated"]))


_SET_KEYS = ("skipped", "literal_ok", "changed")


def export_session(s):
    """A JSON-safe copy of a call for storage (sets become lists; callbacks are dropped)."""
    out = {k: v for k, v in s.items() if not k.startswith("_")}
    for k in _SET_KEYS:
        out[k] = sorted(out.get(k) or [])
    out["history"] = [list(h) for h in s.get("history", [])]
    return out


def restore_session(d):
    """Back into memory from storage: the call continues exactly where it was."""
    s = dict(d)
    for k in _SET_KEYS:
        s[k] = set(s.get(k) or [])
    s["history"] = [tuple(h) for h in s.get("history", [])]
    s["updated"] = time.time()
    _prune()
    SESSIONS[s["id"]] = s
    return s


def start(language="hi", study=None, consent=True):
    _prune()
    lang = "en" if language == "en" else "hi"
    s = {"id": uuid.uuid4().hex[:12], "lang": lang, "state": START, "trail": [],
         "answers": {}, "status": {}, "said": {}, "skipped": set(), "tries": {}, "history": [],
         "pending": [], "correcting": None, "corrections": 0, "recoveries": 0, "recover_to": None,
         "literal_ok": set(), "changed": set(), "clarify": None, "just_corrected": None,
         "problem_value": None, "result": None, "prescreen_id": None, "llm": [], "updated": time.time(),
         "study": study, "first_values": {}, "readbacks": 0, "consent": bool(consent)}
    SESSIONS[s["id"]] = s
    transition(s, COLLECTING, "greeting")
    first = next_field(s)
    reply = GREETING[lang] + PRIVACY[lang][bool(consent)] + BY_KEY[first][lang]
    s["history"].append(("Saathi", reply))
    s["asking"] = first
    return s, reply


def summary(s):
    """The profile as Saathi understood it, for the 'Here's what I understood' card."""
    rows = []
    for k in KEYS:
        if k in s["answers"]:
            row = {"field": k, "label": TITLES[k], "value": s["answers"][k],
                   "text": fmt(k, s["answers"][k]), "status": s["status"].get(k, "validated"),
                   "critical": k in CRITICAL, "card": k in CARD}
            if k == "work":  # the category, with the caller's own words kept beside it as evidence
                row["category"] = norm.work_category(s["answers"][k])
                row["said"] = s["said"].get(k) or s["answers"][k]
                if row["category"] != "other":
                    row["text"] = f'{row["text"]} \u2014 "{s["answers"][k]}"'
            rows.append(row)
        elif k == "tenure_months" and "loan_amount" in s["answers"] and k in s["skipped"]:
            rows.append({"field": k, "label": TITLES[k], "value": None, "critical": True, "card": True,
                         "text": f"not given ({DEFAULT_TENURE} months will be used)", "status": "default"})
    return rows


def _spoken_summary(s):
    """A short read-back of the financial profile (the screen shows every field)."""
    a, lang = s["answers"], s["lang"]
    hi = lang == "hi"
    parts = []
    if "loan_amount" in a:
        t = ""
        if "tenure_months" in a:
            t = f", {fmt('tenure_months', a['tenure_months'], lang)} mein" if hi else \
                f" over {fmt('tenure_months', a['tenure_months'], lang)}"
        parts.append(f"loan {inr(a['loan_amount'])}{t}" if hi else f"a loan of {inr(a['loan_amount'])}{t}")
    if "monthly_income" in a:
        source = f" ({norm.work_label(a['work'], lang)})" if a.get("work") else ""
        parts.append(f"kamaai {inr(a['monthly_income'])} mahina{source}" if hi
                     else f"monthly income {inr(a['monthly_income'])}{source}")
    n = a.get("other_loans_count")
    if n == 0:
        parts.append("koi aur loan nahi" if hi else "no other loans")
    elif n:
        bit = f"{n} aur loan" if hi else f"{n} other loan{'s' if n != 1 else ''}"
        if "other_loans_emi" in a:
            bit += f", EMI {inr(a['other_loans_emi'])}"
        if "other_loans_outstanding" in a:
            bit += (f", baaki {inr(a['other_loans_outstanding'])}" if hi
                    else f", {inr(a['other_loans_outstanding'])} still owed")
        parts.append(bit)
    return SAY["summary_intro"][lang] + "; ".join(parts) + "." + SAY["summary_ask"][lang]


def buttons(s):
    """Tap targets for the call screen. Each sends `action` (and `text`, for the transcript)."""
    hi = s["lang"] == "hi"
    if s["state"] in FINISHED:
        out = []
        if s["state"] == COMPLETED and s.get("result") and s.get("applicant") and not s.get("advisor_case"):
            out.append({"label": "Try a different amount" if not hi else "Alag rakam dekhein", "action": "whatif",
                        "text": "Alag rakam ya samay dekhna hai" if hi else "I'd like to try a different amount"})
        if not s.get("advisor_case"):
            out.append({"label": "Talk to a loan advisor", "action": "handoff",
                        "text": "Loan advisor se baat karni hai" if hi else "I'd like to talk to a loan advisor"})
        out.append({"label": "Start a new check" if not hi else "Naya check", "action": "new_call",
                    "text": "Naya check shuru karna hai" if hi else "Start a new check"})
        return out
    if s["pending"]:
        p = s["pending"][0]
        if p["kind"] == "choose":
            out = [{"label": fmt(p["field"], o), "action": f"choose:{i}", "text": fmt(p["field"], o, s["lang"])}
                   for i, o in enumerate(p["options"])]
            if p.get("why") == "multiple" and p["field"] in SUMMABLE:
                out.append({"label": f"Total {fmt(p['field'], sum(p['options']))}", "action": "choose:total",
                            "text": "Dono milakar" if hi else "Both together"})
            return out
        return [{"label": "Yes", "action": "yes", "text": "Haan" if hi else "Yes"},
                {"label": "No", "action": "no", "text": "Nahi" if hi else "No"}]
    if s["state"] == COLLECTING and s.get("skip_offer") and s.get("skip_offer") == s.get("asking"):
        return [{"label": "Skip it" if not hi else "Chhod dein", "action": "skip",
                 "text": "Chhod dijiye" if hi else "Skip it"},
                {"label": "Try again" if not hi else "Phir se", "action": "retry",
                 "text": "Phir se poochiye" if hi else "Ask me again"},
                {"label": "Talk to a loan advisor", "action": "handoff",
                 "text": "Loan advisor se baat karni hai" if hi else "I'd like to talk to a loan advisor"}]
    if s["state"] == COLLECTING and s.get("help_for") and s.get("help_for") == s.get("asking"):
        return _help_buttons(s["asking"], s["lang"])
    if s["state"] == RECOVERY:
        return [{"label": "Try again" if not hi else "Phir se", "action": "yes",
                 "text": "Haan, phir se koshish karte hain" if hi else "Yes, let's try again"},
                {"label": "Talk to a loan advisor", "action": "handoff",
                 "text": "Loan advisor se baat karni hai" if hi else "I'd like to talk to a loan advisor"}]
    if s["state"] == VERIFYING:
        return [{"label": "Confirm", "action": "confirm", "text": "Haan, sab sahi hai" if hi else "Yes, that's correct"},
                {"label": "Change", "action": "change", "text": "Kuch badalna hai" if hi else "I need to change something"}]
    if s["state"] == CORRECTION and not s["correcting"]:
        return [{"label": TITLES[k], "action": f"correct:{k}",
                 "text": f"{LABELS['hi'][k]} badalni hai" if hi else f"Change my {LABELS['en'][k]}"}
                for k in KEYS if k in s["answers"] or k == "tenure_months"]
    if s["state"] == RESULT:
        return [{"label": "Yes", "action": "yes", "text": "Haan" if hi else "Yes"},
                {"label": "No, thanks", "action": "no", "text": "Nahi, shukriya" if hi else "No, thanks"},
                {"label": "Talk to a loan advisor", "action": "handoff",
                 "text": "Loan advisor se baat karni hai" if hi else "I'd like to talk to a loan advisor"}]
    return []


def public(s):
    """What the call screen sees: no internal bookkeeping."""
    pending = None
    if s["pending"]:
        p = s["pending"][0]
        pending = {"field": p["field"], "kind": p["kind"], "value": p.get("value"), "options": p.get("options")}
    return {"session_id": s["id"], "state": s["state"], "asking": s.get("asking", "done"),
            "language": s["lang"], "answers": dict(s["answers"]),
            "fields": {k: {"value": v, "status": s["status"].get(k, "validated"), "said": s["said"].get(k, "")}
                       for k, v in s["answers"].items()},
            "summary": summary(s), "pending": pending, "correcting": s["correcting"],
            "buttons": buttons(s), "skipped": sorted(s["skipped"]), "not_needed": list(s.get("not_needed", [])),
            "done": s["state"] in FINISHED, "result": s["result"], "prescreen_id": s["prescreen_id"],
            "simulation": s.get("simulation"), "advisor_case": s.get("advisor_case"),
            "new_call": bool(s.get("new_call")),
            "consent": s.get("consent", True),
            "stage": stage(s),
            "trail": [f"{t['from']} -> {t['to']}" for t in s["trail"][-20:]],
            "llm": s["llm"][-1] if s["llm"] else ""}
