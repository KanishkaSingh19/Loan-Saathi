"""Web-call conversation manager: Gemini understands and phrases, our code decides.

Each caller turn makes one Gemini call that (1) pulls answers out of what the caller said and
(2) phrases the next question in the caller's own style (Hinglish, Hindi or English). Our code
fixes the question order, validates every value with normalize.py, and makes the decision with
the model + affordability rule (server.decide). When all answers are in, a second Gemini call
turns the decision's reply_hint into a short spoken reply.

Gemini never chooses an amount or a decision, and its reply is used only if it asks about the
field our code expects next. If Gemini is unavailable or returns something unusable, the call
carries on with scripted questions and our own parser, so a call never dies on the LLM.
"""
import json
import os
import re
import time
import uuid

import httpx

import normalize as norm

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TIMEOUT = 12.0

# Question order. `need` = we keep asking (up to MAX_TRIES) because no decision is possible
# without it; the rest are skipped after MAX_TRIES unclear answers.
FIELDS = [
    {"key": "loan_amount", "need": True,
     "en": "How much loan do you need?", "hi": "Aapko kitne ka loan chahiye?"},
    {"key": "monthly_income", "need": True,
     "en": "Roughly how much do you earn in a month?", "hi": "Aap mahine mein lagbhag kitna kamaate hain?"},
    {"key": "work",
     "en": "What work do you do?", "hi": "Aap kya kaam karte hain?"},
    {"key": "years_in_work",
     "en": "How many years have you been doing this work?", "hi": "Yeh kaam aap kitne saal se kar rahe hain?"},
    {"key": "other_loans_count",
     "en": "Do you have any other loans running right now? If yes, how many?",
     "hi": "Kya abhi koi aur loan chal raha hai? Agar haan, toh kitne?"},
    {"key": "other_loans_emi",
     "en": "What is the total EMI you pay on those loans every month?",
     "hi": "Un sab loans ki total EMI mahine ki kitni jaati hai?"},
    {"key": "housing",
     "en": "Do you live in your own house, on rent, or with family?",
     "hi": "Aap apne ghar mein rehte hain, kiraye pe, ya parivaar ke saath?"},
    {"key": "age", "en": "And lastly, what is your age?", "hi": "Aur aakhri sawaal, aapki umar kitni hai?"},
]
KEYS = [f["key"] for f in FIELDS]
BY_KEY = {f["key"]: f for f in FIELDS}
MAX_TRIES = 3
HOUSING_WORDS = re.compile(
    r"own|apna|apne|khud|rent|kiray|family|parivaar|parivar|parents|maa|papa|ghar|house|home|flat|"
    r"quarter|government|sarkari|hostel|pg|घर|किराय|परिवार|अपन", re.I)

GREETING = {
    "hi": "Namaste! Main Loan Saathi hoon. Main kuch aasaan sawaal poochkar bataunga ki aapko kitna loan "
          "mil sakta hai. Yeh sirf ek pre-screening hai, final approval nahi. ",
    "en": "Hello! I'm Loan Saathi. I'll ask a few simple questions and tell you how much loan you could get. "
          "This is only a pre-screening, not a final approval. ",
}

SYSTEM_PROMPT = """You are Loan Saathi, a warm, patient voice assistant that pre-screens first-time
borrowers in India for small loans. Callers speak Hindi, English or Hinglish, often informally,
and their words come from speech recognition, so expect errors and Devanagari or Latin script.

You get the conversation so far, the answers already collected, the field our system wants
next, and the caller's latest words. Do two things:

1. EXTRACT any of these fields the caller clearly stated (in this turn or earlier, if missed):
   loan_amount, monthly_income, other_loans_emi: rupees as plain digits ("pachas hazaar" -> "50000",
     "dhai lakh" -> "250000", "15k" -> "15000"). Monthly income: if they give a yearly or daily
     figure, convert to monthly (daily x 26).
   work: a short English description of their work ("vegetable seller", "salaried driver",
     "retired", "student", "unemployed", "government job", "own kirana shop").
   years_in_work, age: plain number of years.
   other_loans_count: plain number; "nahi"/"no loans" -> "0".
   housing: one of "own house", "rented", "with family", "government quarters".
   tenure_months: months, only if they mention how long they want to repay.
   Never guess. Leave a field out if it was not clearly stated. Do not invent values.

2. REPLY: one or two short spoken sentences. Briefly acknowledge what they said, then ask
   about the NEXT field: the first one in `still_needed` (it is in order) that the caller has not
   just answered, skipping years_in_work if they are retired, a student or unemployed, and
   skipping other_loans_emi if they have no other loans. Ask it in natural words; the
   `question_hints` show what each field means. If the caller asked
   something, answer it in one sentence first, but never promise approval, quote interest rates,
   or mention any score or model. If nothing is left to ask, set asking to "done" and do not ask anything: just say
   "Thank you, let me check." in their language.
   Match the caller's language and script style: Hinglish in Latin script by default; English if
   they speak English; Devanagari only if they clearly prefer pure Hindi. No emojis, no lists.

Set "asking" to exactly the field you asked about in the reply (or "done").
Set "language" to "hi" (Hindi or Hinglish) or "en" (English)."""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "extracted": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in KEYS + ["tenure_months"]},
        },
        "reply": {"type": "string"},
        "asking": {"type": "string"},
        "language": {"type": "string", "enum": ["hi", "en"]},
    },
    "required": ["extracted", "reply", "asking", "language"],
}

RESULT_PROMPT = """You are Loan Saathi, speaking to a caller on a voice call about a loan pre-screening.
Turn the guidance below into what you say to the caller: 2 to 4 short spoken sentences, warm and
clear, in the caller's language ({lang}: "hi" means Hinglish in Latin script, "en" means English).
Keep every amount, EMI and tenure EXACTLY as written in the guidance (you may write "rupaye" or
"rupees" instead of the rupee sign). Do not add amounts, rates, promises or reasons that are not
in the guidance. Never mention scores, percentages, models or AI. No emojis, no lists."""

RESULT_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}


# ---------- validation ----------

def valid(key, value):
    """The normalised value if our parser understands it, else None."""
    if value in (None, ""):
        return None
    v = str(value)
    if key in ("loan_amount", "monthly_income"):
        n = norm.number(v)
        return n if n and n > 0 else None
    if key == "other_loans_emi":
        n = norm.number(v)
        return n if n is not None and n >= 0 else None
    if key == "other_loans_count":
        n = norm.number(v)
        if n is None and norm.boolean(v) is False:
            n = 0
        return int(n) if n is not None and 0 <= n < 50 else None
    if key == "years_in_work":
        n = norm.years(v)
        return n if n is not None and 0 <= n < 70 else None
    if key == "age":
        n = norm.number(v)
        return int(n) if n and 18 <= n <= 100 else None
    if key == "work":
        income_type, _ = norm.work(v)
        return v if income_type else None
    if key == "housing":
        return v if HOUSING_WORDS.search(v) else None  # norm.housing() defaults on any input
    if key == "tenure_months":
        n = norm.months(v)
        return n if n and 3 <= n <= 120 else None
    return None


def _skip(key, answers):
    """Fields that don't apply given earlier answers."""
    if key == "years_in_work" and answers.get("work"):
        income_type, _ = norm.work(answers["work"])
        return income_type in ("Pensioner", "Unemployed", "Student")
    if key == "other_loans_emi":
        return valid("other_loans_count", answers.get("other_loans_count")) == 0
    return False


def next_field(s):
    for f in FIELDS:
        k = f["key"]
        if k in s["answers"] or k in s["skipped"] or _skip(k, s["answers"]):
            continue
        return k
    return "done"


# ---------- Gemini ----------

async def _gemini(api_key, model, system, user, schema):
    """One structured call. Returns the parsed JSON object, or raises."""
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "temperature": 0.3,
            "maxOutputTokens": 400,
            "thinkingConfig": {"thinkingLevel": "MINIMAL"},
        },
    }
    async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT) as client:
        r = await client.post(GEMINI_URL.format(model=model), json=body,
                              headers={"x-goog-api-key": api_key})
    if not r.is_success:
        raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]
                   if not p.get("thought"))
    out = json.loads(text)
    if not isinstance(out, dict):
        raise ValueError("Gemini returned non-object JSON")
    return out


def _transcript(s, limit=10):
    return "\n".join(f"{who}: {text}" for who, text in s["history"][-limit:])


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


def start(language="hi"):
    _prune()
    lang = "en" if language == "en" else "hi"
    s = {"id": uuid.uuid4().hex[:12], "lang": lang, "answers": {}, "said": {}, "skipped": set(),
         "tries": {}, "history": [], "done": False, "result": None, "prescreen_id": None,
         "llm": [], "updated": time.time()}
    SESSIONS[s["id"]] = s
    first = next_field(s)
    reply = GREETING[lang] + BY_KEY[first][lang]
    s["history"].append(("Saathi", reply))
    s["asking"] = first
    return s, reply


def public(s):
    """What the call screen sees: no internal bookkeeping."""
    return {"session_id": s["id"], "asking": s.get("asking", "done"), "language": s["lang"],
            "answers": dict(s["answers"]), "skipped": sorted(s["skipped"]), "done": s["done"],
            "result": s["result"], "prescreen_id": s["prescreen_id"],
            "llm": s["llm"][-1] if s["llm"] else ""}


async def turn(s, text, decide, build_applicant, save, api_key="", model="gemini-3.5-flash-lite"):
    """Handles one caller utterance and returns the reply to speak."""
    text = (text or "").strip()[:1000]
    s["updated"] = time.time()
    if s["done"]:
        return ("Aapka pre-screening ho gaya hai. Dhanyavaad!" if s["lang"] == "hi"
                else "Your pre-screening is complete. Thank you!")
    asked = s.get("asking") or next_field(s)
    s["history"].append(("Caller", text))

    extracted, g_reply, g_asking, used = {}, "", "", "fallback"
    if api_key and text:
        try:
            out = await _gemini(api_key, model, SYSTEM_PROMPT, json.dumps({
                "conversation": _transcript(s),
                "collected": s["answers"],
                "field_just_asked": asked,
                "still_needed": [k for k in KEYS if k not in s["answers"] and k not in s["skipped"]],
                "question_hints": {k: BY_KEY[k]["en"] for k in KEYS
                                   if k not in s["answers"] and k not in s["skipped"]},
                "caller_said": text,
            }, ensure_ascii=False), EXTRACT_SCHEMA)
            extracted = out.get("extracted") or {}
            g_reply, g_asking = str(out.get("reply") or "").strip(), str(out.get("asking") or "")
            if out.get("language") in ("hi", "en"):
                s["lang"] = out["language"]
            used = "gemini"
        except Exception as e:  # noqa: BLE001
            print(f"[converse {s['id']}] Gemini failed, using fallback: {type(e).__name__}: {e}", flush=True)
    elif text:
        extracted = {asked: text}  # our own parser on the raw answer

    # Keep only values our parsers understand; never overwrite with an unclear value.
    for k, v in extracted.items():
        if k in BY_KEY or k == "tenure_months":
            n = valid(k, v)
            if n is not None:
                s["answers"][k] = v if k in ("work", "housing") else n
                s["said"][k] = text
                s["skipped"].discard(k)
    # Gemini may miss a short direct answer ("do", "nahi"); try our parser on it too.
    if asked in BY_KEY and asked not in s["answers"]:
        n = valid(asked, text)
        if n is not None:
            s["answers"][asked] = text if asked in ("work", "housing") else n
            s["said"][asked] = text

    if asked in BY_KEY and asked not in s["answers"]:
        s["tries"][asked] = s["tries"].get(asked, 0) + 1
        if s["tries"][asked] >= MAX_TRIES and not BY_KEY[asked].get("need"):
            s["skipped"].add(asked)

    nxt = next_field(s)
    s["llm"].append(used)

    if nxt != "done" and BY_KEY[nxt].get("need") and s["tries"].get(nxt, 0) >= MAX_TRIES:
        s["done"] = True
        s["asking"] = "done"
        reply = ("Maaf kijiye, mujhe aapki baat theek se samajh nahi aayi. Kripya thodi der baad dobara call karein."
                 if s["lang"] == "hi" else
                 "Sorry, I couldn't catch that clearly. Please try again a little later.")
        s["history"].append(("Saathi", reply))
        return reply

    if nxt != "done":
        s["asking"] = nxt
        if used == "gemini" and g_asking == nxt and g_reply:
            reply = g_reply
        else:
            again = nxt == asked
            prefix = ("" if not again else
                      ("Maaf kijiye, main samjha nahi. " if s["lang"] == "hi" else "Sorry, I didn't catch that. "))
            reply = prefix + BY_KEY[nxt][s["lang"]]
        s["history"].append(("Saathi", reply))
        return reply

    # All answers in: our model + affordability rule decide; Gemini only phrases the result.
    s["asking"] = "done"
    body = {k: str(v) for k, v in s["answers"].items()}
    applicant = build_applicant(body)
    result, details = decide(applicant)
    pid = uuid.uuid4().hex[:12]
    result["prescreen_id"] = pid
    save(pid, {**body, "source": "web-call", "session_id": s["id"]}, applicant, result, details)
    s.update(done=True, result=result, prescreen_id=pid)

    reply = ""
    if api_key:
        try:
            out = await _gemini(api_key, model, RESULT_PROMPT.format(lang=s["lang"]),
                                f"Guidance:\n{result['reply_hint']}", RESULT_SCHEMA)
            reply = str(out.get("reply") or "").strip()
            if reply and not _amounts_ok(reply, result):
                print(f"[converse {s['id']}] Gemini result reply changed an amount; using fallback", flush=True)
                reply = ""
            s["llm"][-1] = "gemini" if reply else "fallback"
        except Exception as e:  # noqa: BLE001
            print(f"[converse {s['id']}] Gemini result phrasing failed: {type(e).__name__}: {e}", flush=True)
    if not reply:
        reply = _fallback_result(result, s["lang"])
    s["history"].append(("Saathi", reply))
    return reply


def _digits(text):
    return {int(m.replace(",", "")) for m in re.findall(r"\d[\d,]*", text) if m.replace(",", "").isdigit()}


def _amounts_ok(reply, result):
    """Every number Gemini says must appear in the guidance it was given."""
    allowed = _digits(result["reply_hint"]) | {result.get("tenure_months", 0)}
    return _digits(reply) <= allowed


def _fallback_result(r, lang):
    d = r["decision"]
    amt, emi, n = r.get("offer_amount_text", ""), r.get("offer_emi_text", ""), r.get("tenure_months", "")
    tips = r.get("tips", "")
    if lang == "hi":
        if d == "pre_approved":
            return (f"Achhi khabar! Aap {amt} ke loan ke liye pre-approved hain, {n} mahine ke liye, "
                    f"EMI lagbhag {emi} mahina. Aadhaar, PAN aur 3 mahine ka bank statement taiyaar rakhiye. "
                    "Yeh sirf pre-screening hai, final approval nahi.")
        if d == "needs_review":
            return (f"Aapki {amt} ki request, {n} mahine mein, EMI lagbhag {emi}, mumkin lagti hai. "
                    "Hamare loan officer ise dekhkar 2 working din mein call karenge.")
        if d == "counter_offer":
            return (f"Aapki maangi hui rakam se EMI aapki kamaai ke aadhe se zyada ho jaati. "
                    f"Hum {amt} {n} mahine ke liye de sakte hain, EMI lagbhag {emi} mahina. Kya yeh theek rahega?")
        return ("Abhi aapki batayi jaankari ke hisaab se hum yeh loan nahi de paayenge. "
                + (f"Isme madad karega: {tips}. " if tips else "") + "Kuch samay baad dobara try zaroor kijiye. Dhanyavaad!")
    if d == "pre_approved":
        return (f"Good news! You're pre-approved for {amt} over {n} months, with an EMI of about {emi} a month. "
                "Keep your Aadhaar, PAN and 3 months of bank statements ready. This is a pre-screening, not a final approval.")
    if d == "needs_review":
        return (f"Your request for {amt} over {n} months, EMI about {emi}, looks possible. "
                "A loan officer will review it and call you within 2 working days.")
    if d == "counter_offer":
        return (f"The amount you asked for would take more than half your income in EMIs. "
                f"We could offer {amt} over {n} months instead, EMI about {emi} a month. Would that work?")
    return ("Based on what you shared, we can't offer this loan right now. "
            + (f"What would help: {tips}. " if tips else "") + "Please do try again later. Thank you!")
