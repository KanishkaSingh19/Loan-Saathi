"""Checking values, formatting them, and choosing the next question (short call included)."""

import json
import re

import normalize as norm
from scoring import inr

from .settings import ASK_HISTORY, PARENT, PROBES, RULE_FIELDS, SHORT_CALL
from .fields import (AGE_RANGE, DONT_KNOW, FIELDS, FILLER, FIRST_TIME, HISTORY, HISTORY_FLAGS, HOUSING_WORDS, KEYS, LIMITS,
    MENTIONS, MONEY, NUMERIC, TENURE_PHRASE, TENURE_RANGE)


def _plain(text):
    """Lowercase, punctuation-free, single-spaced, for comparing a quote with what was said."""
    return " ".join(re.sub(r"[^\wऀ-ॿ₹]+", " ", str(text).lower()).split())


def quoted(quote, said):
    """True if Gemini's quote really is in the caller's words (guards against invented answers)."""
    q = _plain(quote)
    return bool(q) and q in _plain(said)


# ---------- validation ----------

def _none_said(v):
    """'never taken one', 'koi nahi, ek bhi nahi': a negation with no digits means zero, not 'one'."""
    return bool(FIRST_TIME.search(str(v))) and not re.search(r"\d", str(v))


def valid(key, value):
    """The normalised value if our parser understands it and it is in range, else None."""
    if value in (None, ""):
        return None
    v = str(value)
    if key in MONEY and norm.negative(v):
        return None
    if key in ("loan_amount", "monthly_income"):
        n = norm.number(v)
        return n if n and n > 0 else None
    if key in ("other_loans_emi", "other_loans_outstanding"):
        n = norm.number(v)
        if n is None and norm.boolean(v) is False:
            n = 0
        return n if n is not None and n >= 0 else None
    if key == "other_loans_count":
        n = norm.number(v)
        if (n is None and norm.boolean(v) is False) or _none_said(v):
            n = 0
        return int(n) if n is not None and 0 <= n < 50 else None
    if key == "years_in_work":
        n = norm.years(v)
        return n if n is not None and 0 <= n < 70 else None
    if key == "age":
        n = norm.number(v)
        return int(n) if n and AGE_RANGE[0] <= n <= AGE_RANGE[1] else None
    if key == "work":
        # Free text, so almost anything is a description; never "hmm", "pata nahi" or a bare yes/no.
        words = re.sub(r"[^\w\s]", " ", str(v).lower()).split()
        if not norm.work_known(v) and (not words or DONT_KNOW.search(str(v)) or all(w in FILLER for w in words)
                                       or (len(words) <= 2 and norm.boolean(v) is not None)):
            return None
        income_type, _ = norm.work(v)
        return v if income_type else None
    if key == "housing":
        return v if HOUSING_WORDS.search(v) else None  # norm.housing() defaults on any input
    if key == "tenure_months":
        n = norm.months(v)
        return n if n and TENURE_RANGE[0] <= n <= TENURE_RANGE[1] else None
    if key == "past_loans":
        n = norm.number(v)
        if (n is None and (FIRST_TIME.search(v) or norm.boolean(v) is False)) or _none_said(v):
            n = 0
        return int(n) if n is not None and 0 <= n <= 50 else None
    if key in HISTORY_FLAGS:
        b = norm.boolean(v)
        return None if b is None else int(b)
    return None


def check(s, key, raw, words):
    """Our verdict on one heard value: ("ok", v), ("confirm", v, how), ("choose", [a, b]) or
    ("invalid", code, v). `raw` is the value (Gemini's or the caller's text); `words` is what the
    caller actually said for it."""
    words = words or str(raw)
    if key in NUMERIC and (norm.negative(words) or norm.negative(raw)):
        return ("invalid", "negative", None)
    if key in MONEY:
        # "6 lakh for 2 years": the tenure's number is not a second amount.
        heard = norm.amounts(TENURE_PHRASE.sub(" ", str(words)))
        if any(a["value"] >= 1000 for a in heard):
            heard = [a for a in heard if a["value"] >= 100 or a["scaled"]]
        strong = [a for a in heard if a["digits"] or a["scaled"]] or heard
        # An over-wide quote ("3 lakh chahiye, 40,000 kamata hoon" for the loan) is not ambiguous when
        # the other amount belongs to another field the caller stated in the same breath.
        others = [v for f, v in (s.get("claims") or {}).items() if f != key and v]
        strong = [a for a in strong if not any(abs(a["value"] - o) <= 0.5 for o in others)] or strong
        distinct = []
        for a in strong:
            if all(abs(a["value"] - d) > 0.5 for d in distinct):
                distinct.append(a["value"])
        if len(distinct) > 1:
            return ("choose", distinct[:2])
        n = valid(key, raw)
        if str(raw) == str(words) and len(distinct) == 1 and n is not None:
            n = distinct[0]  # the caller's own words: use the amount we picked out, not just the first number
        if n is None:
            if norm.number(raw) == 0 and key in ("loan_amount", "monthly_income"):
                return ("invalid", "too_small", 0)
            return ("invalid", "unclear", None)
        lo, soft, hard = LIMITS[key]
        own = strong[0] if strong else None
        if own and abs(own["value"] - n) > 0.5:
            return ("confirm", n, "number")  # Gemini's figure isn't what the caller's words say
        small = 1000 if key in ("loan_amount", "monthly_income") else 100
        if own and not own["scaled"] and 0 < n < small and key not in s["literal_ok"]:
            return ("confirm", n * 1000, "scale")  # "40" for an income almost always means 40 hazaar
        if n < lo:
            return ("invalid", "too_small", n)
        if n > hard:
            return ("invalid", "too_large", n)
        if norm.TEEN_TY.search(words) or (soft and n > soft):
            return ("confirm", n, "number")
        return ("ok", n)
    if key == "tenure_months":
        n = norm.months(raw)
        own = norm.months(words)
        # The caller's own words win when they name the unit ("six months" is 6 even if Gemini sent "6").
        if own is not None and re.search(r"month|mahin|महीन|saal|sal\b|year|varsh|baras|साल", str(words), re.I):
            n = own
        if n is None:
            return ("invalid", "unclear", None)
        if not TENURE_RANGE[0] <= n <= TENURE_RANGE[1]:
            return ("invalid", "tenure_range", n)
        if own is not None and own != n:
            return ("confirm", n, "number")
        return ("ok", n)
    if key == "age":
        n = norm.number(raw)
        if n is not None and not AGE_RANGE[0] <= n <= AGE_RANGE[1]:
            return ("invalid", "age_range", n)
    n = valid(key, raw)
    if n is None:
        return ("invalid", "unclear", None)
    return ("ok", raw if key in ("work", "housing") else n)


def _same(key, a, b):
    if key in ("work", "housing"):
        return True  # free text: the latest description wins
    return abs(float(a) - float(b)) <= max(0.5, 0.01 * abs(float(a)))


def fmt(key, v, lang="en"):
    if key in MONEY:
        return inr(v)
    if key == "tenure_months":
        n = int(v)
        return f"{n} mahine" if lang == "hi" else f"{n} months"
    if key in ("age", "years_in_work"):
        n = round(float(v), 1)
        n = int(n) if n == int(n) else n
        return f"{n} saal" if lang == "hi" else f"{n} years"
    if key in ("other_loans_count", "past_loans"):
        return str(int(v))
    if key in HISTORY_FLAGS:
        return ("Haan" if v else "Nahi") if lang == "hi" else ("Yes" if v else "No")
    if key == "work":
        return norm.work_label(v, lang)  # the category, read back; the caller's own words stay in s["said"]
    return str(v)


def mentioned_field(text):
    for key, pat in MENTIONS:
        if pat.search(text or ""):
            return key
    return None


def _skip(key, answers):
    """Fields that don't apply given earlier answers."""
    if key == "years_in_work" and answers.get("work"):
        income_type, _ = norm.work(answers["work"])
        return income_type in ("Pensioner", "Unemployed", "Student")
    if key in ("other_loans_emi", "other_loans_outstanding"):
        return valid("other_loans_count", answers.get("other_loans_count")) == 0
    # The history funnel: no loans ever -> no lateness questions; "never late" -> no follow-ups;
    # nothing running now -> nothing can be overdue.
    never_borrowed = answers.get("past_loans") == 0 and answers.get("other_loans_count") in (0, None)
    if key == "ever_late":
        return never_borrowed
    if key in ("late_last_year", "late_over_month"):
        return never_borrowed or answers.get("ever_late") == 0
    if key == "overdue_now":
        return answers.get("other_loans_count") == 0 or never_borrowed
    return False


def _open(s, k):
    return not ((not ASK_HISTORY and k in HISTORY) or k in s["answers"] or k in s["skipped"]
                or _skip(k, s["answers"]))


def next_field(s):
    if not SHORT_CALL:
        return next((f["key"] for f in FIELDS if _open(s, f["key"])), "done")
    rule = [k for k in KEYS if k in RULE_FIELDS and _open(s, k)]
    if rule:
        return rule[0]
    return _model_question(s)


def _band_probe(s, remaining):
    """For each remaining model-only field: the risk band with each possible answer (others unanswered), and
    the band with every field at its riskiest and at its safest answer together. None if we can't score."""
    build = s.get("_build_applicant")
    if build is None:
        return None
    import scoring
    base = {k: str(v) for k, v in s["answers"].items()}
    tries = [(k, v) for k in remaining for v in PROBES.get(k, [])]
    # Two model calls in all: every single answer (and none) in one batch, then all-riskiest / all-safest.
    ps = scoring.risk_probabilities([build(base)] + [build({**base, k: v}) for k, v in tries])
    p_now, by_field = ps[0], {}
    for (k, v), pk in zip(tries, ps[1:]):
        by_field.setdefault(k, []).append((pk, v))
    worst = {k: max(t)[1] for k, t in by_field.items()}
    best = {k: min(t)[1] for k, t in by_field.items()}
    swing = {k: {scoring.band_of(x) for x, _ in t} | {scoring.band_of(p_now)} for k, t in by_field.items()}
    p_worst, p_best = scoring.risk_probabilities([build({**base, **worst}), build({**base, **best})])
    bands = {scoring.band_of(p_now), scoring.band_of(p_worst), scoring.band_of(p_best)}.union(*swing.values())
    return {"bands": bands, "swing": swing}


def _model_question(s):
    """The next model-only question, or "done" once no answer to any of them could move the risk band. Those
    are then recorded as not needed (skipped, so the model reads them as not answered)."""
    remaining = [k for k in KEYS if k not in RULE_FIELDS and _open(s, k)]
    if not remaining:
        return "done"
    key = json.dumps([sorted((k, str(v)) for k, v in s["answers"].items()), remaining])
    cached = s.get("_probe_cache")
    if cached and cached[0] == key:
        probe = cached[1]
    else:
        try:
            probe = _band_probe(s, remaining)
        except Exception as e:  # noqa: BLE001  (never lose a call over the shortcut: ask everything)
            print(f"[converse {s['id']}] band probe failed, asking every question: {type(e).__name__}: {e}",
                  flush=True)
            probe = None
        s["_probe_cache"] = (key, probe)
    if probe is None:
        return remaining[0]
    if len(probe["bands"]) == 1:
        s.setdefault("not_needed", [])
        for k in remaining:
            s["skipped"].add(k)
            if k not in s["not_needed"]:
                s["not_needed"].append(k)
        return "done"
    # The first remaining question (in the usual order) whose answer alone can move the band; if none can
    # alone but together they could, the first one left. A follow-up never comes before its question.
    k = next((k for k in remaining if len(probe["swing"].get(k, ())) > 1), remaining[0])
    while PARENT.get(k) in remaining:
        k = PARENT[k]
    return k
