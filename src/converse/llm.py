"""The LLM, through the provider router: masking, one call per turn, quote-gated extraction."""

import asyncio
import json
import time

import httpx

import llm as llm_mod
import normalize as norm
import pii

from .settings import GEMINI_TIMEOUT, GEMINI_URL, _LLM_CACHE, LLM_DEADLINE_S, PROMPT_VERSION
from .fields import BY_KEY, KEYS, MONEY, TENURE_PHRASE
from .texts import EXTRACT_SCHEMA, SYSTEM_PROMPT
from .validation import _plain, quoted
from .parser import fallback_candidates


# ---------- Gemini ----------

async def _gemini(api_key, model, system, user, schema):
    """One structured call. Returns the parsed JSON object, or raises."""
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "temperature": 0.2,
            "maxOutputTokens": 400,
            "thinkingConfig": {"thinkingLevel": "MINIMAL"},
        },
    }
    async def post():
        async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT) as client:
            return await client.post(GEMINI_URL.format(model=model), json=body, headers={"x-goog-api-key": api_key})

    # httpx's timeout is per phase (connect, each read...), so a slow, trickling reply could hold a voice
    # turn for minutes (seen in a dry run). This is a hard deadline for the whole call.
    try:
        r = await asyncio.wait_for(post(), timeout=GEMINI_TIMEOUT)
    except asyncio.TimeoutError:
        raise TimeoutError(f"Gemini took longer than {GEMINI_TIMEOUT:.0f}s") from None
    if not r.is_success:
        raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]
                       if not p.get("thought"))
    except (KeyError, IndexError, TypeError):
        raise llm_mod.InvalidJSON("Gemini reply had no content") from None
    return llm_mod.parse_json(text)


# ---------- the LLM, through the provider router ----------

def _router(env):
    """The server's router (Groq then Gemini), or, for direct callers and tests, Gemini alone."""
    if env.get("llm") is not None:
        return env["llm"]
    if env.get("api_key"):
        return llm_mod.Router([llm_mod.GeminiProvider(env["api_key"], env.get("model", ""),
                                                      send=lambda *a: _gemini(*a))],
                              deadline_s=LLM_DEADLINE_S, cache=_LLM_CACHE)
    return None


def _llm_available(env):
    r = _router(env)
    return bool(r and r.available)


async def _llm(s, env, purpose, system, user, schema):
    """One LLM use: (JSON object, provider). At most one per turn, so a turn never waits on two LLM calls
    in a row; a second use raises LLMUnavailable and the caller takes the deterministic path. Every
    attempt is written to the call's llm_trail (provider, purpose, milliseconds)."""
    if s.get("_llm_used_this_turn"):
        s.setdefault("llm_trail", []).append({"ts": round(time.time(), 3), "state": s["state"], "purpose": purpose,
                                              "provider": "fallback", "ms": 0, "why": "one LLM call per turn"})
        raise llm_mod.LLMUnavailable("one LLM call per turn")
    s["_llm_used_this_turn"] = True
    t0 = time.monotonic()
    entry = {"ts": round(time.time(), 3), "state": s["state"], "purpose": purpose}
    # Personal identifiers (phone, Aadhaar, PAN, account numbers, a name...) never reach a provider; the trail
    # records how many of each kind were masked, never the values.
    system, _ = pii.mask(system)
    user, masked = pii.mask(user)
    if masked:
        entry["masked"] = masked
    try:
        out, provider = await _router(env).call(system, user, schema, prompt_version=PROMPT_VERSION)
    except Exception as e:
        s.setdefault("llm_trail", []).append({**entry, "provider": "fallback",
                                              "ms": round((time.monotonic() - t0) * 1000),
                                              "why": f"{type(e).__name__}: {str(e)[:160]}"})
        raise
    s.setdefault("llm_trail", []).append({**entry, "provider": provider,
                                          "ms": round((time.monotonic() - t0) * 1000)})
    return out, provider


def _transcript(s, limit=10):
    return "\n".join(f"{who}: {text}" for who, text in s["history"][-limit:])


async def _extract(s, env, text, asked):
    """Candidate answers [(field, value, caller's words)] and Gemini's reply, if any."""
    out = []
    g = {"reply": "", "asking": "", "used": "fallback"}
    # Identifiers are masked for every reader of this text: the LLM, our parser (a phone number is not an
    # amount) and the quote kept as evidence.
    text, _ = pii.mask(text)
    if _llm_available(env) and text:
        try:
            res, provider = await _llm(s, env, "extract", SYSTEM_PROMPT, json.dumps({
                "conversation": _transcript(s),
                "collected": s["answers"],
                "field_just_asked": asked,
                "still_needed": [k for k in KEYS if k not in s["answers"] and k not in s["skipped"]],
                "question_hints": {k: BY_KEY[k]["en"] for k in KEYS
                                   if k not in s["answers"] and k not in s["skipped"]},
                "caller_said": text,
                "reply_language": "English" if s["lang"] == "en" else "Hinglish (Hindi in Latin script)",
            }, ensure_ascii=False), EXTRACT_SCHEMA)
            for a in res.get("answers") or []:
                if not isinstance(a, dict) or a.get("field") not in BY_KEY:
                    continue
                if quoted(a.get("quote", ""), text) and not pii.has_token(a.get("quote")):
                    out.append((a["field"], a.get("value"), a.get("quote")))
                else:
                    print(f"[converse {s['id']}] dropped unquoted answer {a.get('field')}={a.get('value')!r}", flush=True)
            g = {"reply": str(res.get("reply") or "").strip(), "asking": str(res.get("asking") or ""),
                 "used": provider}
        except Exception as e:  # noqa: BLE001
            print(f"[converse {s['id']}] LLM unavailable, using our parser: {type(e).__name__}: {e}", flush=True)
    if g["used"] == "fallback" and text:
        out = fallback_candidates(text, asked)
    elif text:
        # Belt and braces: Gemini sometimes skips a field the caller clearly stated ("my salary is 40,000"
        # in a dry run). Our parser fills only money/tenure fields Gemini didn't return; every value still
        # goes through check() and the read-back.
        have = {k for k, _, _ in out}
        ours = fallback_candidates(text, asked)
        for k, raw, words in ours:
            if k not in have and k not in s["answers"] and (k in MONEY or k in ("tenure_months", "years_in_work",
                                                                                 "other_loans_count", "loan_purpose")):
                out.append((k, raw, words))
        # Gemini may quote one end of a range ("35 hazaar" out of "30-35 hazaar"). When our parser's span
        # for the same money field holds more amounts than the quote, check the wider span, so a range or
        # two amounts are always asked about instead of one being stored.
        span = {k: w for k, _, w in ours if k in MONEY}

        def count(w):
            return len(norm.amounts(TENURE_PHRASE.sub(" ", str(w or ""))))
        out = [(k, raw, span[k] if k in span and count(span[k]) > count(words) else words) for k, raw, words in out]
    # One spoken amount answers one question: when the same words are given to two fields (Gemini read
    # "sixty thousand", said to the income question, as the loan), keep the field we asked about. Only money
    # fields compete: "bike ke liye 80 hazaar" is both the purpose and the amount.
    by_words, kept = {}, []
    for cand in out:
        if cand[0] in MONEY:
            by_words.setdefault(_plain(cand[2]), []).append(cand)
        else:
            kept.append(cand)
    out = kept + [next((c for c in group if c[0] == asked), group[0]) for group in by_words.values()]
    return out, g
