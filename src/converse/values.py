"""Setting values: read-backs, contradictions, pending questions and cross-checks."""

import time

import normalize as norm
from scoring import inr

from .settings import RULE_FIELDS
from .states import CORRECTION
from .fields import BOTH, FIRST, LABELS, LIMITS, MAX_TRIES, MONEY, NO, NUMERIC, SECOND, SUMMABLE, YES
from .texts import PROBLEMS, SAY
from .validation import check, fmt, _same


# ---------- setting values ----------

def _set(s, k, v, said, status="validated"):
    s.setdefault("first_values", {}).setdefault(k, v)  # what we first understood (study: first-pass accuracy)
    old = s["answers"].get(k)
    if old is not None and not _same(k, old, v):
        s.setdefault("change_log", []).append({
            "field": k, "before": old, "after": v, "said": said, "ts": round(time.time(), 3),
            "how": "correction" if s["state"] == CORRECTION else "clarified"})
    s["answers"][k] = v
    s["status"][k] = status
    s["said"][k] = said
    s["skipped"].discard(k)
    s["changed"].add(k)
    if k == "work":
        s["work_category"] = norm.work_category(v)
    if k in RULE_FIELDS and s.get("not_needed"):
        s["skipped"].difference_update(s["not_needed"])  # asked again only if they could now move the band
        s["not_needed"] = []
    if k == "other_loans_count" and v == 0:  # no loans: their EMI and debt are 0 by definition
        for d in ("other_loans_emi", "other_loans_outstanding"):
            s["answers"].pop(d, None)
            s["status"].pop(d, None)


def _flag(s, kind, field, values):
    s.setdefault("flag_log", []).append({"kind": kind, "field": field, "values": values,
                                         "ts": round(time.time(), 3)})


def _unset(s, k):
    s["answers"].pop(k, None)
    s["status"].pop(k, None)


def _queue(s, item):
    if not any(p["field"] == item["field"] for p in s["pending"]):
        s["pending"].append({"tries": 0, **item})
        s["readbacks"] = s.get("readbacks", 0) + 1


def _corrected(s, k):
    if k == s["correcting"]:
        s["correcting"] = None
        s["corrections"] += 1
        s["just_corrected"] = k


def _commit(s, k, v, said, mode):
    """Store a checked value, unless it contradicts what we already have."""
    old = s["answers"].get(k)
    if mode == "collect" and old is not None and not _same(k, old, v):
        _queue(s, {"kind": "choose", "why": "contradiction", "field": k, "options": [old, v], "said": said,
                   "mode": mode})
        _flag(s, "contradiction", k, [old, v])
        return
    if k in ("other_loans_emi", "other_loans_outstanding") and v > 0 and s["answers"].get("other_loans_count") == 0:
        _queue(s, {"kind": "loans_conflict", "field": k, "value": v, "said": said, "mode": mode})
        _flag(s, "loans_conflict", k, [0, v])
        return
    _set(s, k, v, said)
    if mode == "correct":
        _corrected(s, k)


def offer(s, k, raw, words, mode="collect"):
    """Run every check on one heard value and store it, queue a read-back, or return the problem
    code ("negative", "too_small", "too_large", "tenure_range", "age_range", "unclear")."""
    verdict = check(s, k, raw, words)
    kind = verdict[0]
    if kind == "invalid":
        s["problem_value"] = verdict[2]
        return verdict[1]
    if kind == "choose":
        _queue(s, {"kind": "choose", "why": "multiple", "field": k, "options": verdict[1], "said": words,
                   "mode": mode})
        return None
    v = verdict[1]
    # An EMI above the whole monthly income is almost always a loan's size heard as its installment
    # ("13 lakh ka loan"): keep it as the debt and ask for the EMI again, clearly.
    income = s["answers"].get("monthly_income")
    if k == "other_loans_emi" and income and v > income:
        print(f"[converse {s['id']}] EMI {v} is above income {income}: keeping it as debt", flush=True)
        if "other_loans_outstanding" not in s["answers"]:
            _set(s, "other_loans_outstanding", v, words)
        s["clarify"] = "other_loans_emi"
        return None
    if kind == "confirm":
        _queue(s, {"kind": "confirm", "how": verdict[2], "field": k, "value": v, "said": words,
                   "raw": norm.number(words) if verdict[2] == "scale" else None, "mode": mode})
        return None
    _commit(s, k, v, words, mode)
    return None


def _yes(text, action):
    return action == "yes" or (not action and bool(YES.search(text)) and not NO.search(text))


def _no(text, action):
    return action == "no" or (not action and bool(NO.search(text)))


def _resolve_pending(s, text, action):
    """The caller's answer to a read-back. Returns (prefix to say, problem code)."""
    p = s["pending"][0]
    k, lang, mode = p["field"], s["lang"], p.get("mode", "collect")
    # Only real amounts count as a new value: in "the second one" / "pehla wala, ek" the word "one" is not ₹1.
    heard = [a["value"] for a in norm.amounts(text) if a["digits"] or a["scaled"]] if not action else []

    if p["kind"] == "choose":
        pick = None
        if action.startswith("choose:"):
            arg = action.split(":", 1)[1]
            if arg == "total":
                pick = sum(p["options"])
            elif arg.isdigit() and int(arg) < len(p["options"]):
                pick = p["options"][int(arg)]
        elif heard and k in NUMERIC:
            match = [o for o in p["options"] if any(_same(k, o, h) for h in heard)]
            if match:
                pick = match[0]
            else:  # a third value: check it like a fresh answer, replacing the old one
                s["pending"].pop(0)
                if p.get("why") == "contradiction":
                    _unset(s, k)
                return "", offer(s, k, text, text, mode)
        elif BOTH.search(text) and p.get("why") == "multiple" and k in SUMMABLE:
            pick = sum(p["options"])
        elif FIRST.search(text):
            pick = p["options"][0]
        elif SECOND.search(text):
            pick = p["options"][1]
        if pick is None:
            p["tries"] += 1
            if p["tries"] >= MAX_TRIES:
                s["pending"].pop(0)
                _unset(s, k)
                return SAY["again"][lang], None
            return SAY["sorry"][lang], None
        s["pending"].pop(0)
        _set(s, k, pick, p["said"])
        if mode == "correct":
            _corrected(s, k)
        return SAY["noted"][lang], None

    if p["kind"] == "loans_conflict":
        if _yes(text, action):
            s["pending"].pop(0)
            _unset(s, "other_loans_count")  # ask how many again
            _set(s, k, p["value"], p["said"])
            return SAY["noted"][lang], None
        if _no(text, action):
            s["pending"].pop(0)
            return SAY["noted"][lang], None
        p["tries"] += 1
        if p["tries"] >= MAX_TRIES:
            s["pending"].pop(0)
        return SAY["sorry"][lang], None

    # confirm: "I heard X. Is that right?"
    same = any(_same(k, p["value"], h) for h in heard) or (
        p["how"] == "scale" and any(_same(k, p["raw"] or 0, h) for h in heard))
    if heard and not same:
        s["pending"].pop(0)  # they said a different number: check that one instead
        return "", offer(s, k, text, text, mode)
    if _yes(text, action):
        s["pending"].pop(0)
        _commit(s, k, p["value"], p["said"], mode)
        return SAY["noted"][lang], None
    if _no(text, action):
        s["pending"].pop(0)
        if p["how"] == "scale":
            s["literal_ok"].add(k)  # next time, take a bare number as said
        if mode == "correct":
            s["correcting"] = k
        return SAY["again"][lang], None
    p["tries"] += 1
    if p["tries"] >= MAX_TRIES:
        s["pending"].pop(0)
        return SAY["again"][lang], None
    return "", None


def _pending_prompt(s):
    p, lang = s["pending"][0], s["lang"]
    k = p["field"]
    label = LABELS[lang][k]
    if p["kind"] == "confirm":
        if p["how"] == "scale":
            raw = p["raw"]
            raw = int(raw) if raw is not None and raw == int(raw) else raw
            text = SAY["confirm_scale"][lang].format(label=label, raw=raw, value=fmt(k, p["value"], lang))
        else:
            text = SAY["confirm_number"][lang].format(label=label, value=fmt(k, p["value"], lang))
        return text + (SAY["yes_no"][lang] if p["tries"] else "")
    if p["kind"] == "choose":
        a, b = (fmt(k, o, lang) for o in p["options"][:2])
        if p.get("why") == "contradiction":
            return SAY["contradiction"][lang].format(label=label, a=a, b=b)
        text = SAY["choose_multiple"][lang].format(label=label, a=a, b=b)
        return text + (SAY["choose_total"][lang] if k in SUMMABLE else "")
    return SAY["loans_conflict"][lang].format(label=label, value=fmt(k, p["value"], lang))


def _problem_text(s, k, code):
    lang = s["lang"]
    if code == "unclear" or code not in PROBLEMS:
        return SAY["sorry"][lang]
    lo = LIMITS.get(k, (0,))[0]
    v = s.get("problem_value")
    return PROBLEMS[code][lang].format(label=LABELS[lang][k], min=inr(lo) if k in MONEY else lo,
                                       value=fmt(k, v, lang) if v is not None else "")


def _cross_checks(s):
    """Checks between answers. Returns (field to ask again, message) or None."""
    a, lang = s["answers"], s["lang"]
    emi, income = a.get("other_loans_emi"), a.get("monthly_income")
    if emi is not None and income and emi > income and "other_loans_emi" not in s["changed"]:
        # The income changed to below an EMI we already had: ask for the income again.
        msg = SAY["emi_over_income"][lang].format(emi=inr(emi), income=inr(income))
        _unset(s, "monthly_income")
        return "monthly_income", msg
    return None
