"""Webhook for the Gnani Agent Builder loan pre-screening agent.

    POST /v1/prescreen   On-Call action: caller's answers in, decision + reply hint out
    POST /v1/post-call   Post-call trigger: stores whatever Gnani sends about the call
    POST /v1/converse    Browser web call: one transcribed turn in, reply out (Gemini + our model)
    GET  /v1/applications   Recent pre-screens (for the dashboard)
    GET  /dashboard/     Live React dashboard (built from dashboard/; asks for the webhook key)
    GET  /health

Run:  python -I -X utf8 src/server.py      (port 8000; set WEBHOOK_KEY in .env to require
      the X-Webhook-Key header)

The response is deliberately flat with simple keys so it works whether Agent Builder maps
response keys into "After API Call Variables" or hands the raw JSON to the LLM.
"""
import asyncio
import base64
import hashlib
import hmac
import secrets
from collections import deque
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import httpx
from fastapi import (BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket,
                     WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Log lines carry ₹ and Hindi; on a Windows console without -X utf8 printing them raised
# UnicodeEncodeError mid-request (a saved advisor case came back as HTTP 500). Never let a log fail a request.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="backslashreplace")

import advisor  # noqa: E402
import assessment  # noqa: E402
import converse  # noqa: E402
import evaluation  # noqa: E402
import llm as llm_mod  # noqa: E402
import normalize as norm  # noqa: E402
import scoring  # noqa: E402
import simulate  # noqa: E402
import spoken  # noqa: E402
import whatsapp  # noqa: E402
import study  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
WEBHOOK_KEY = os.environ.get("WEBHOOK_KEY", "")
GNANI_STT_API_KEY = os.environ.get("GNANI_STT_API_KEY") or os.environ.get("GNANI_API_KEY", "")
GNANI_TTS_API_KEY = os.environ.get("GNANI_TTS_API_KEY") or os.environ.get("GNANI_API_KEY", "")
DB_PATH = Path(os.environ.get("DB_PATH") or ROOT / "data" / "prescreens.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
# Gemini powers the browser web call (/v1/converse). Without a key the call still works, with
# scripted questions and our own parser.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-3.5-flash-lite"
# Groq (OpenAI-compatible) is tried first when its key is set; Gemini is the fallback; our parser is last.
# GROQ_DISABLED=1 keeps a configured key unused (the test suites set it: live calls are slow, rate-limited
# and not deterministic). An empty GROQ_API_KEY can't do that on Windows, where "" unsets the variable for
# child processes and .env's key comes back.
GROQ_API_KEY = "" if os.environ.get("GROQ_DISABLED", "").strip() == "1" else os.environ.get("GROQ_API_KEY", "").strip()
# One or more Groq models, tried in order. Each has its own free-tier rate limit (8,000 tokens a minute on
# the free plan, a few extraction turns), so a second model is extra capacity before Gemini and the parser.
GROQ_MODELS = [m.strip() for m in (os.environ.get("GROQ_MODEL", "").strip()
                                   or "qwen/qwen3.8-27b,openai/gpt-oss-20b").split(",") if m.strip()]
GROQ_MODEL = GROQ_MODELS[0]
# Comma-separated origins allowed to call the API from a browser, e.g. the Vercel dashboard.
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
# Where new advisor cases are announced: any URL that accepts a JSON POST, e.g. a Slack or Microsoft
# Teams incoming webhook ({"text": ...}). Empty = no notification, and applicants are told so.
ADVISOR_WEBHOOK_URL = os.environ.get("ADVISOR_WEBHOOK_URL", "").strip()
# WhatsApp Cloud API (deploy/DEPLOY.md). The channel is off unless all four are set.
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "").strip()
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip()
WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "").strip()
WHATSAPP_GRAPH = os.environ.get("WHATSAPP_GRAPH", "https://graph.facebook.com/v21.0").rstrip("/")
SESSION_RESUME_HOURS = 2  # a stored call can be resumed (e.g. after a restart) for this long

app = FastAPI(title="Loan pre-screening webhook")
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Webhook-Key", "X-Call-Session", "X-Call-Token"],
    )


class ConverseRequest(BaseModel):
    session_id: str = Field(default="", max_length=40)
    text: str = Field(default="", max_length=2000)
    language: str = "hi"
    # A button press on the call screen: confirm, change, correct:<field>, yes, no, choose:<n>, handoff.
    action: str = Field(default="", max_length=40)
    # Did the caller agree to their answers being stored (for RETENTION_DAYS)? The call screen asks; API
    # integrators (e.g. the phone agent) must ask too. Without it the call runs in memory only.
    consent: bool = True
    # User study (STUDY.md): set when a participant starts a call from a persona card.
    participant: str = Field(default="", max_length=20, pattern=r"^[A-Za-z0-9_-]*$")
    persona: str = Field(default="", max_length=8)
    synthetic: bool = False


class SimulateRequest(BaseModel):
    prescreen_id: str = Field(min_length=1, max_length=40)
    loan_amount: float | None = None
    tenure_months: float | None = None
    scenarios: bool = False


class AdvisorRequest(BaseModel):
    prescreen_id: str = Field(default="", max_length=40)
    session_id: str = Field(default="", max_length=40)
    question: str = Field(default="", max_length=500)
    loan_amount: float | None = None   # the applicant's current simulation, recomputed here
    tenure_months: float | None = None


class SurveyRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=40)
    ease: int = Field(ge=1, le=5)
    understood: str = Field(pattern="^(yes|partly|no)$")
    trust: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=1000)
    stt_failures: int = Field(default=0, ge=0, le=100)


class CaseUpdate(BaseModel):
    status: str = Field(pattern="^(open|in_review|closed)$")
    advisor: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=1000)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    language: str = "hi-en"
    voice: str = "Nalini"
    model: str = "timbre-v2.5"
    speed: float = Field(default=1.0, ge=0.85, le=1.15)
    spell_numbers: bool = True  # "₹3,00,000" is spoken as "teen lakh rupaye" (Gnani asks for spelled numbers)


class LatencyReport(BaseModel):
    """One measured voice turn from the browser (milliseconds), for the Evaluation Lab."""
    session_id: str = Field(default="", max_length=40)
    mode: str = Field(default="live", max_length=20)          # live (streaming mic) or tap (record, then send)
    speech_end_to_transcript_ms: float | None = Field(default=None, ge=0, le=120_000)
    transcript_to_reply_ms: float | None = Field(default=None, ge=0, le=120_000)
    reply_to_first_audio_ms: float | None = Field(default=None, ge=0, le=120_000)
    speech_end_to_first_audio_ms: float | None = Field(default=None, ge=0, le=120_000)
    filler_played: bool = False


# ---------- storage ----------

# SQLite file locally; Postgres (e.g. a free Neon database) when DATABASE_URL is set, because
# free container hosts wipe their disk on restart.
_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS prescreens (id TEXT PRIMARY KEY, ts {real}, raw TEXT, applicant TEXT, result TEXT)",
    "CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, ts {real}, payload TEXT)",
    "CREATE TABLE IF NOT EXISTS advisor_cases (id TEXT PRIMARY KEY, ts {real}, payload TEXT)",
    "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, ts {real}, payload TEXT)",
    "CREATE TABLE IF NOT EXISTS study_results (id TEXT PRIMARY KEY, ts {real}, payload TEXT)",
    "CREATE TABLE IF NOT EXISTS latency_metrics (id TEXT PRIMARY KEY, ts {real}, payload TEXT)",
    "CREATE TABLE IF NOT EXISTS wa_threads (id TEXT PRIMARY KEY, ts {real}, payload TEXT)",
    "CREATE TABLE IF NOT EXISTS decision_audit (id TEXT PRIMARY KEY, ts {real}, payload TEXT)",
]
_schema_ready = False


_pool = None


def _pg_pool():
    """One small Postgres connection pool per process (opening a Neon connection per request is slow).
    `check` re-tests a connection before lending it: Neon's free tier closes idle ones."""
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, timeout=10, max_idle=120,
                               kwargs={"connect_timeout": 5}, check=ConnectionPool.check_connection, open=True)
    return _pool


def _sqlite():
    DB_PATH.parent.mkdir(exist_ok=True)
    return sqlite3.connect(DB_PATH)


def _run(sql, params=(), fetch=False):
    """Runs one statement; write `?` for placeholders and it's adapted per database."""
    global _schema_ready
    if DATABASE_URL:
        with _pg_pool().connection() as con:  # commits on success, rolls back on error, returns to the pool
            if not _schema_ready:
                for stmt in _SCHEMA:
                    con.execute(stmt.format(real="DOUBLE PRECISION"))
                _schema_ready = True
            cur = con.execute(sql.replace("?", "%s"), params)
            return cur.fetchall() if fetch else None
    con = _sqlite()
    try:
        for stmt in _SCHEMA:  # cheap on SQLite, and DB_PATH can change between test runs
            con.execute(stmt.format(real="REAL"))
        cur = con.execute(sql, params)
        rows = cur.fetchall() if fetch else None
        con.commit()
        return rows
    finally:
        con.close()


def _save(sql, params):
    """Runs after the response is sent (FastAPI background task, in a worker thread), so a slow
    remote database never delays the agent's reply mid-call. Failures are logged, not raised."""
    try:
        _run(sql, params)
    except Exception as e:  # noqa: BLE001
        print(f"[storage error] {type(e).__name__}: {e}", flush=True)


def _check_key(key):
    # Closed by default: without a WEBHOOK_KEY nothing is served (callers' income, debts and age are
    # behind these routes). A placeholder from .env.example counts as missing.
    if not WEBHOOK_KEY or WEBHOOK_KEY.startswith("replace-with"):
        raise HTTPException(status_code=503, detail="server not configured: set WEBHOOK_KEY")
    if not hmac.compare_digest(str(key or ""), WEBHOOK_KEY):
        raise HTTPException(status_code=401, detail="bad webhook key")


# ---------- public borrower calls (no key) vs staff views (webhook key) ----------
# A borrower can start a call without any key. Each call gets a random token, sent only to the browser that
# started it; the call's turns, voice, what-ifs and advisor request need that token, so a visitor only ever
# reaches their own call. Everything that lists or changes other people's data still needs WEBHOOK_KEY.
# Per-network limits keep Gnani and Gemini spend bounded. PUBLIC_CALLS=0 turns public calls off.
PUBLIC_CALLS = os.environ.get("PUBLIC_CALLS", "1").strip() != "0"
RATE = {"call": (int(os.environ.get("PUBLIC_CALLS_PER_IP_DAY") or 20), 86400),
        "call_total": (int(os.environ.get("PUBLIC_CALLS_PER_DAY") or 300), 86400),
        "turn": (40, 60), "speech": (60, 60), "advisor": (5, 3600), "metrics": (120, 60)}
_HITS = {}


def _limited(bucket, who):
    """True when `who` has used up `bucket` (sliding window); otherwise counts this use."""
    limit, window = RATE[bucket]
    now = time.time()
    q = _HITS.setdefault((bucket, who), deque())
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def _client_ip(headers, client):
    fwd = headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (client.host if client else "unknown")


def _token_hash(token):
    return hashlib.sha256(f"loan-saathi-call:{token}".encode()).hexdigest()


def _open():
    """Closed by default: without a real WEBHOOK_KEY nothing is served, public calls included."""
    if not WEBHOOK_KEY or WEBHOOK_KEY.startswith("replace-with"):
        raise HTTPException(status_code=503, detail="server not configured: set WEBHOOK_KEY")


def _is_staff(key):
    return bool(WEBHOOK_KEY) and not WEBHOOK_KEY.startswith("replace-with") and \
        hmac.compare_digest(str(key or ""), WEBHOOK_KEY)


def _owned_session(sid, token):
    s = get_session(sid) if sid and token else None
    if s is None or not s.get("token_hash") or not hmac.compare_digest(s["token_hash"], _token_hash(token)):
        return None
    return s


def _caller(request, key, bucket):
    """("staff", None) with the webhook key; otherwise ("public", the call this browser holds a token for, or
    None). Public use is rate-limited per network."""
    _open()
    if _is_staff(key):
        return "staff", None
    if not PUBLIC_CALLS:
        raise HTTPException(status_code=401, detail="bad webhook key")
    if _limited(bucket, _client_ip(request.headers, request.client)):
        raise HTTPException(status_code=429, detail="Too many requests from your network. Please wait a minute "
                                                    "and try again.")
    return "public", _owned_session(request.headers.get("x-call-session", ""),
                                    request.headers.get("x-call-token", ""))


def _need_call(request, key, bucket):
    role, owner = _caller(request, key, bucket)
    if role != "staff" and owner is None:
        raise HTTPException(status_code=401, detail="Start a call first (or sign in as staff).")
    return role, owner


def _check_speech_api(api_key):
    if not api_key:
        raise HTTPException(status_code=503, detail="Gnani API key is not configured")


def _check_speech_access(key, api_key):
    if not WEBHOOK_KEY:
        raise HTTPException(status_code=503, detail="WEBHOOK_KEY must be configured for speech APIs")
    _check_key(key)
    if not api_key:
        raise HTTPException(status_code=503, detail="Gnani API key is not configured")


# ---------- formatting ----------

inr = scoring.inr


def _reason_text(item, direction):
    """One plain-English phrase per factor, or None to keep it unspoken."""
    f, v = item["feature"], item["value"]
    up = direction == "up"
    phrases = {
        "ANNUITY_TO_INCOME": (f"the EMI would take {v}", "the EMI is a comfortable share of income"),
        "DEBT_TO_INCOME": ("existing loans are high compared to income", "existing loans are low compared to income"),
        "BUREAU_ACTIVE_DEBT": (f"{v} is still owed on other loans", "little is owed on other loans"),
        "BUREAU_ACTIVE_LOANS": (
            "there is no loan history yet" if v in ("0", "0.0") else "several loans are already running",
            "there is a healthy loan history",
        ),
        "YEARS_EMPLOYED": (f"only {v} in the current work", f"{v} of steady work"),
        "NOT_EMPLOYED": ("there is no regular job income", "there is a steady pension"),
        "OWNS_REALTY": (None, "owning a house"),
        "OWNS_CAR": (None, "owning a vehicle"),
        "NAME_INCOME_TYPE": (None, "a stable source of income"),
        "NAME_HOUSING_TYPE": (None, "stable housing"),
        "AGE_YEARS": (None, None),  # never tell someone their age counts against them
        "PAST_LOANS": ("there are no earlier loans to show a repayment record" if v in ("0", "0.0") else None,
                       "earlier loans give a repayment record"),
        "EVER_REFUSED": ("an earlier loan application was refused", None),
        "EVER_LATE": ("an EMI was paid late before", "EMIs have been paid on time"),
        "LATE_LAST_YEAR": ("an EMI was paid late in the last 12 months", "no late EMIs in the last 12 months"),
        "LATE_OVER_MONTH": ("an EMI was once more than a month late", None),
        "OVERDUE_NOW": ("an EMI is overdue right now", None),
    }
    pair = phrases.get(f)
    return None if pair is None else pair[0 if up else 1]


_TIPS = {
    "ANNUITY_TO_INCOME": "a smaller loan or a longer tenure, to lower the EMI",
    "DEBT_TO_INCOME": "closing some existing loans first",
    "BUREAU_ACTIVE_DEBT": "paying down existing loans first",
    "BUREAU_ACTIVE_LOANS": "starting with a small loan and repaying on time, to build a loan history",
    "YEARS_EMPLOYED": "applying again after more time in the current work",
    "NOT_EMPLOYED": "showing a regular income source",
    "EVER_LATE": "paying every EMI on time for a few months",
    "LATE_LAST_YEAR": "paying every EMI on time for a few months",
    "LATE_OVER_MONTH": "paying every EMI on time for a few months",
    "OVERDUE_NOW": "clearing the overdue EMI first",
}
# The same tips for Hinglish replies (the web call speaks these, so no English inside Hinglish).
_TIPS_HI = {
    "ANNUITY_TO_INCOME": "chhota loan ya lamba samay, taaki EMI kam ho",
    "DEBT_TO_INCOME": "pehle kuch maujooda loan band karna",
    "BUREAU_ACTIVE_DEBT": "pehle maujooda loans ka baaki kam karna",
    "BUREAU_ACTIVE_LOANS": "chhote loan se shuru karke har EMI samay par bharna, taaki loan history bane",
    "YEARS_EMPLOYED": "abhi ke kaam mein kuch aur samay ke baad dobara apply karna",
    "NOT_EMPLOYED": "aamdani ka niyamit zariya dikhana",
    "EVER_LATE": "kuch mahine har EMI samay par bharna",
    "LATE_LAST_YEAR": "kuch mahine har EMI samay par bharna",
    "LATE_OVER_MONTH": "kuch mahine har EMI samay par bharna",
    "OVERDUE_NOW": "pehle bakaya EMI chukana",
}


# ---------- core ----------

DEBT_LIMIT_MONTHS = 24  # see decide(): more owed than this many months of income -> not eligible now

def build_applicant(body):
    income_type, occupation = norm.work(body.get("work"))
    house_cat, owns = norm.housing(body.get("housing"))
    owns_house = norm.boolean(body.get("owns_house"))
    if owns_house is None:
        owns_house = owns
    years = norm.years(body.get("years_in_work"))
    a = {
        "monthly_income": norm.number(body.get("monthly_income")),
        "loan_amount": norm.number(body.get("loan_amount")),
        "tenure_months": norm.months(body.get("tenure_months")),
        "purchase_price": norm.number(body.get("purchase_price")),
        "age": norm.number(body.get("age")),
        "income_type": income_type,
        "occupation": occupation,
        "education": norm.education(body.get("education")),
        "housing": house_cat,
        "children": norm.number(body.get("children")),
        "family_members": norm.number(body.get("family_members")),
        "owns_car": norm.boolean(body.get("owns_vehicle")),
        "owns_house": owns_house,
        "active_loans": norm.number(body.get("other_loans_count")),
        "active_loans_outstanding": norm.number(body.get("other_loans_outstanding")),
        "existing_emi": norm.number(body.get("other_loans_emi")),
    }
    if income_type in ("Pensioner", "Unemployed", "Student"):
        a["years_employed"] = None  # model's "not employed" code
    elif years is not None:
        a["years_employed"] = years
    if a["active_loans"] == 0:
        a["active_loans_outstanding"] = a["active_loans_outstanding"] or 0
        a["existing_emi"] = a["existing_emi"] or 0
    # Repayment history (features.HISTORY_FEATURES). Unknown stays None (model: "not answered").
    past = norm.number(body.get("past_loans"))
    if past is None and (norm.boolean(body.get("past_loans")) is False):
        past = 0
    if past is not None and a.get("active_loans"):
        past = max(past, a["active_loans"])  # running loans are loans taken
    a["past_loans"] = past
    for key in ("ever_refused", "ever_late", "late_last_year", "late_over_month", "overdue_now"):
        b = norm.boolean(body.get(key))
        a[key] = None if b is None else int(b)
    if past == 0 and not a.get("active_loans"):  # never borrowed: can't have been late or overdue
        for key in ("ever_late", "late_last_year", "late_over_month", "overdue_now"):
            a[key] = 0
    if a.get("ever_late") == 0:
        a["late_last_year"] = a["late_last_year"] if a["late_last_year"] is not None else 0
        a["late_over_month"] = a["late_over_month"] if a["late_over_month"] is not None else 0
    if a.get("active_loans") == 0 and a.get("overdue_now") is None:
        a["overdue_now"] = 0
    return {k: v for k, v in a.items() if v is not None or k == "years_employed"}


# Factors whose direction everyone expects. The model is not monotone in debt (see decide()), so a
# reason that runs against the applicant's own value ("little is owed" for someone owing more than
# typical) is dropped rather than shown.
_WORSE_WHEN_HIGHER = {"BUREAU_ACTIVE_DEBT", "DEBT_TO_INCOME", "ANNUITY_TO_INCOME"}
_BETTER_WHEN_HIGHER = {"YEARS_EMPLOYED"}


def _direction_ok(item, direction):
    raw, typical = item.get("raw"), item.get("typical_raw")
    if raw is None or typical is None:
        return True
    up = direction == "up"
    if item["feature"] in _WORSE_WHEN_HIGHER:
        return raw > typical if up else raw < typical
    if item["feature"] in _BETTER_WHEN_HIGHER:
        return raw < typical if up else raw > typical
    return True


def _reasons(items, direction):
    """[(feature, plain-English text)] for factors that may be spoken and point the expected way."""
    out = []
    for i in items:
        text = _reason_text(i, direction)
        if text and _direction_ok(i, direction):
            out.append((i["feature"], text))
    return out


def decide(applicant, risk=None, context=None):
    """Model risk + the affordability rule + the debt rule -> decision. `risk` is a precomputed
    scoring.score() result (the web call runs risk and affordability as separate steps).
    `context` tells the safety check about the channel: {"channel": "web", "confirmed": bool,
    "open_checks": int}; without it the call is treated as a phone call."""
    missing = [f for f in ("monthly_income", "loan_amount") if not applicant.get(f)]
    if missing:
        ask = {"monthly_income": "monthly income", "loan_amount": "loan amount needed"}
        return {
            "decision": "need_more_info",
            "risk_band": "",
            "risk_percent": "",
            "missing_fields": ", ".join(missing),
            "reply_hint": "Before I can check, please ask the caller for their "
                          + " and ".join(ask[m] for m in missing) + ".",
        }, None

    # Deterministic safety check before anything is assessed; a failure pauses with what's needed.
    checks = assessment.safety_pre(applicant, context)
    if assessment.failed(checks):
        held = assessment.paused(checks)
        return {
            "decision": "need_more_info",
            "risk_band": "",
            "risk_percent": "",
            "missing_fields": "; ".join(held["needed"]),
            "reply_hint": "Before I can check, please clear this up with the caller: " + " ".join(held["needed"]),
            "assessment": held,
        }, None

    r = risk if risk is not None else scoring.score(applicant)
    afford = scoring.affordable_loan(applicant)
    band = r["band"]
    up_items, down_items = _reasons(r["raises_risk"], "up"), _reasons(r["lowers_risk"], "down")
    ups = [t for _, t in up_items]
    downs = [t for _, t in down_items]
    tips = [_TIPS[f] for f, _ in up_items if f in _TIPS]
    tips_hi = [_TIPS_HI[f] for f, _ in up_items if f in _TIPS_HI]

    requested = applicant["loan_amount"]
    tenure = int(afford["tenure_months"]) if afford else 24
    can_afford = afford and afford["loan_amount"] > 0

    # Hard rule on top of the model: owing more than 24 months of income on other loans means no
    # new loan now. The model can't be trusted here: in the training data large bureau debts went
    # with wealthier borrowers, so its risk *falls* as debt grows (₹36L owed on ₹40k/month scored
    # 6.1%, below the 8.2% with no debt at all).
    owed = applicant.get("active_loans_outstanding") or 0
    policy = ""
    if owed > DEBT_LIMIT_MONTHS * applicant["monthly_income"]:
        policy = "debt_over_limit"
        heavy = "existing loans add up to more than 2 years of income"
        ups = [heavy] + [u for u in ups if "existing loans" not in u]
        tips = ["paying down existing loans first"] + [t for t in tips if "existing loans" not in t]
        tips_hi = ["pehle maujooda loans ka baaki kam karna"] + [t for t in tips_hi if "maujooda loan" not in t]

    if band == "high" or not can_afford or policy:
        decision = "not_eligible_now"
        offer_amount, offer_emi = 0, 0
        hint = ("Gently explain that based on what they shared, we cannot offer this loan right now. "
                + (f"Main reasons: {'; '.join(ups)}. " if ups else "")
                + (f"What would help: {'; '.join(tips[:2])}. " if tips else "")
                + "Encourage them to try again later and thank them warmly.")
    elif afford["fits_request"]:
        offer_amount, offer_emi = requested, afford["emi"]
        if band == "low":
            decision = "pre_approved"
            hint = (f"Good news: based on this pre-screening they look eligible for further review for "
                    f"{inr(requested)} over {tenure} months, EMI about {inr(offer_emi)} per month. ")
        else:
            decision = "needs_review"
            hint = (f"Their request for {inr(requested)} over {tenure} months (EMI about {inr(offer_emi)}) "
                    "looks possible, and a loan advisor would need to review it further. ")
        hint += (f"Strengths: {'; '.join(downs)}. " if downs else "")
        hint += ("Next step: keep Aadhaar, PAN and 3 months of bank statements ready for verification. "
                 "Say clearly this is a pre-screening, not a final approval.")
    else:
        decision = "counter_offer"
        offer_amount, offer_emi = afford["loan_amount"], afford["emi"]
        hint = (f"Explain that {inr(requested)} would make their total EMIs more than half of their income, "
                f"which is risky for them. Offer {inr(offer_amount)} over {tenure} months instead, "
                f"EMI about {inr(offer_emi)} per month, and ask if that works. "
                + (f"Strengths: {'; '.join(downs)}. " if downs else "")
                + "Say clearly this is a pre-screening, not a final approval.")

    result = {
        "decision": decision,
        "risk_band": band,
        "risk_percent": f"{r['default_probability'] * 100:.1f}",
        "offer_amount": int(offer_amount),
        "offer_amount_text": inr(offer_amount) if offer_amount else "",
        "offer_emi": int(offer_emi),
        "offer_emi_text": inr(offer_emi) if offer_emi else "",
        "tenure_months": int(tenure),
        "reasons_against": "; ".join(ups),
        "reasons_for": "; ".join(downs),
        "tips": "; ".join(tips[:2]),
        "tips_hi": "; ".join(dict.fromkeys(tips_hi[:2])),
        "missing_fields": "",
        "policy_rule": policy,
        "reply_hint": hint,
    }
    try:
        result["assessment"] = assessment.build(applicant, r, afford, decision, policy, up_items, down_items,
                                                checks, DEBT_LIMIT_MONTHS)
    except Exception as e:  # noqa: BLE001  (the decision must never fail on its explanation)
        print(f"[assessment] could not build: {type(e).__name__}: {e}", flush=True)
        result["assessment"] = None
    path = (result["assessment"] or {}).get("path_to_yes") or []
    if path:  # the phone agent can tell a declined caller what would change the answer
        result["reply_hint"] += " What would change it (say one or two): " + " ".join(p["text"] for p in path[:2])
    result["audit"] = audit_meta()
    return result, r


# ---------- decision audit (which model, rules and inputs produced each outcome) ----------

def _sha(path):
    import hashlib
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


def _code_commit():
    for var in ("GIT_COMMIT", "RENDER_GIT_COMMIT"):  # set by the host on Render
        if os.environ.get(var):
            return os.environ[var][:7]
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


_AUDIT_STATIC = None


def audit_meta():
    """The model version, rule settings and code version behind a decision (computed once per process)."""
    global _AUDIT_STATIC
    if _AUDIT_STATIC is None:
        _, meta = scoring._load()
        _AUDIT_STATIC = {
            "model": {"file": "models/voice_model.txt", "sha256": _sha(ROOT / "models" / "voice_model.txt"),
                      "validation_auc": meta.get("validation_auc"), "features": len(meta.get("features", [])),
                      "bands": meta.get("bands")},
            "rules": {"max_foir": scoring.MAX_FOIR, "comfort_foir": assessment.COMFORT_FOIR,
                      "debt_limit_months": DEBT_LIMIT_MONTHS, "assumed_annual_rate": scoring.ANNUAL_RATE,
                      "default_tenure_months": scoring.DEFAULT_TENURE},
            "code_commit": _code_commit(),
        }
    return {**_AUDIT_STATIC, "decided_at": round(time.time(), 3)}


def _audit(pid, channel, applicant, result, consent=True):
    """Append-only decision record. Without consent the inputs are left out (decision and versions stay)."""
    a = result.get("assessment") or {}
    record = {"prescreen_id": pid, "channel": channel, "consent": bool(consent),
              "inputs": applicant if consent else None,
              "decision": result.get("decision"), "policy_rule": result.get("policy_rule", ""),
              "risk_band": result.get("risk_band"), "risk_percent": result.get("risk_percent"),
              "affordable_emi": (a.get("affordability") or {}).get("value"),
              "requested_emi": (a.get("requested_emi") or {}).get("value"),
              "outcome": (a.get("outcome") or {}).get("title"),
              **(result.get("audit") or audit_meta())}
    _save("INSERT INTO decision_audit VALUES (?,?,?)", (pid, time.time(), json.dumps(record, ensure_ascii=False)))


@app.get("/v1/audit")
def audit_log(prescreen_id: str = "", limit: int = 50, x_webhook_key: str = Header(default="")):
    """Decision records, newest first (or the one for a pre-screen)."""
    _check_key(x_webhook_key)
    if prescreen_id:
        rows = _run("SELECT payload FROM decision_audit WHERE id = ?", (prescreen_id,), fetch=True)
        if not rows:
            raise HTTPException(status_code=404, detail="no audit record for that pre-screen")
        return json.loads(rows[0][0])
    rows = _run("SELECT payload FROM decision_audit ORDER BY ts DESC LIMIT ?", (min(max(limit, 1), 500),), fetch=True)
    return {"records": [json.loads(r[0]) for r in rows]}


@app.get("/dashboard", include_in_schema=False)
def dashboard_redirect():
    return RedirectResponse("/dashboard/")


@app.get("/", include_in_schema=False)
def root_redirect():
    """A hosted Space opens at '/': send visitors to the dashboard."""
    return RedirectResponse("/dashboard/")


@app.get("/health")
def health():
    scoring._load()
    return {"ok": True}


@app.post("/v1/prescreen")
async def prescreen(request: Request, tasks: BackgroundTasks, x_webhook_key: str = Header(default="")):
    _check_key(x_webhook_key)
    try:
        body = await request.json()
    except Exception:
        body = dict(await request.form()) if request.headers.get("content-type", "").startswith(
            ("application/x-www-form", "multipart/")) else {}
    body = {**dict(request.query_params), **(body if isinstance(body, dict) else {})}

    applicant = build_applicant(body)
    result, details = decide(applicant)
    pid = uuid.uuid4().hex[:12]
    result["prescreen_id"] = pid
    consented = norm.boolean(body.get("consent")) is not False
    tasks.add_task(_audit, pid, "phone", applicant, result, consented)
    if not consented:  # the caller said no to storage
        _remember_prescreen(pid, body, applicant, result)
        print(f"[prescreen {pid}] {result['decision']} (not stored: no consent)", flush=True)
        return result
    tasks.add_task(_save, "INSERT INTO prescreens VALUES (?,?,?,?,?)", (
        pid, time.time(), json.dumps(body, ensure_ascii=False), json.dumps(applicant, ensure_ascii=False),
        json.dumps({**result, "details": details}, ensure_ascii=False)))
    print(f"[prescreen {pid}] {result['decision']} band={result['risk_band']} in={body}", flush=True)
    return result


@app.post("/v1/post-call")
async def post_call(request: Request, tasks: BackgroundTasks, x_webhook_key: str = Header(default="")):
    _check_key(x_webhook_key)
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"raw": raw.decode("utf-8", "replace")}
    cid = uuid.uuid4().hex[:12]
    tasks.add_task(_save, "INSERT INTO calls VALUES (?,?,?)", (cid, time.time(), json.dumps(payload, ensure_ascii=False)))
    print(f"[post-call {cid}] keys={list(payload)[:15]}", flush=True)
    return {"ok": True}


@app.get("/v1/applications")
def applications(limit: int = 50, x_webhook_key: str = Header(default="")):
    _check_key(x_webhook_key)
    limit = max(1, min(limit, 500))
    rows = _run("SELECT id, ts, raw, applicant, result FROM prescreens ORDER BY ts DESC LIMIT ?",
                (limit,), fetch=True)
    calls = _run("SELECT id, ts, payload FROM calls ORDER BY ts DESC LIMIT ?", (limit,), fetch=True)
    return {
        "prescreens": [{"id": i, "ts": t, "said": json.loads(r), "applicant": json.loads(a),
                        "result": json.loads(res)} for i, t, r, a, res in rows],
        "calls": [{"id": i, "ts": t, "payload": json.loads(p)} for i, t, p in calls],
    }


# ---------- what-if simulator ----------

WEB_CONTEXT = {"channel": "web", "confirmed": True, "open_checks": 0}


_PRESCREEN_MEM = {}  # pre-screens of callers who didn't agree to storage: memory only, for a few hours


def _remember_prescreen(pid, said, applicant, result):
    now = time.time()
    for k in [k for k, v in _PRESCREEN_MEM.items() if now - v[0] > SESSION_RESUME_HOURS * 3600]:
        _PRESCREEN_MEM.pop(k, None)
    _PRESCREEN_MEM[pid] = (now, said, applicant, result)


def _load_prescreen(pid):
    if pid in _PRESCREEN_MEM:
        _, said, applicant, result = _PRESCREEN_MEM[pid]
        return said, applicant, result
    rows = _run("SELECT raw, applicant, result FROM prescreens WHERE id = ?", (pid,), fetch=True)
    if not rows:
        raise HTTPException(status_code=404, detail="This assessment wasn't found. Please run the pre-screening again.")
    said, applicant, result = (json.loads(x) for x in rows[0])
    return said, applicant, result


def simulate_for(applicant, loan=None, tenure=None, context=None, with_scenarios=False):
    """Original request + optional simulation (+ the three scenarios), all through decide()."""
    base_tenure = int(applicant.get("tenure_months") or scoring.DEFAULT_TENURE)
    original = simulate.run(applicant, applicant["loan_amount"], base_tenure, decide, context, kind="request")
    out = {"label": simulate.LABEL, "original": original}
    if with_scenarios:
        out["scenarios"] = simulate.scenarios(applicant, decide, context)
    if loan is not None or tenure is not None:
        sim = simulate.run(applicant, applicant["loan_amount"] if loan is None else loan,
                           base_tenure if tenure is None else tenure, decide, context)
        out["simulation"] = sim
        out["compare"] = simulate.compare(original, sim)
    return out


@app.post("/v1/simulate")
def simulate_route(body: SimulateRequest, request: Request, x_webhook_key: str = Header(default="")):
    """What-if simulation for a completed pre-screen. Read-only: the saved request never changes.
    Staff can simulate any pre-screen; a caller only their own call's."""
    role, owner = _need_call(request, x_webhook_key, "turn")
    if role != "staff" and owner.get("prescreen_id") != body.prescreen_id:
        raise HTTPException(status_code=401, detail="You can only try options on your own assessment.")
    said, applicant, result = _load_prescreen(body.prescreen_id)
    if result.get("decision") == "need_more_info" or not applicant.get("loan_amount") \
            or not applicant.get("monthly_income"):
        raise HTTPException(status_code=409, detail="Finish the assessment first: the simulator only works on a "
                                                    "completed, checked profile.")
    context = WEB_CONTEXT if said.get("source") == "web-call" else None
    try:
        return simulate_for(applicant, body.loan_amount, body.tenure_months, context, body.scenarios)
    except simulate.InvalidSimulation as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


# ---------- advisor cases ----------

def notify_advisor(case):
    """Announce a new case on ADVISOR_WEBHOOK_URL. Sends only the headline, the question and the case
    id (the full profile stays in the dashboard). Returns what happened, for the case and the reply."""
    if not ADVISOR_WEBHOOK_URL:
        return {"channel": "none", "configured": False, "delivered": False}
    text = (f"New Loan-Saathi advisor case #{case['id']}: {case['headline']}. "
            f"Question: {case['question']} Open the dashboard's Advisor cases view to review it.")
    try:
        r = httpx.post(ADVISOR_WEBHOOK_URL, json={"text": text, "case_id": case["id"], "headline": case["headline"]},
                       timeout=5.0)
        ok = r.is_success
        status = r.status_code
    except httpx.HTTPError as e:
        ok, status = False, type(e).__name__
    print(f"[advisor case {case['id']}] notification {'delivered' if ok else 'FAILED'} ({status})", flush=True)
    return {"channel": "webhook", "configured": True, "delivered": ok, "status": status, "ts": time.time()}


def save_case(case):
    case["notification"] = notify_advisor(case)
    if case["notification"]["delivered"]:
        case["prototype_note"] = "A loan advisor was notified about this case."
    elif case["notification"]["configured"]:
        case["prototype_note"] = ("The advisor notification didn't go through; the case is saved in the "
                                  "Loan-Saathi dashboard for an advisor to pick up.")
    case.setdefault("events", []).append({"ts": case["ts"], "status": "open", "by": "applicant",
                                          "note": "Case created"})
    _run("INSERT INTO advisor_cases VALUES (?,?,?)", (case["id"], case["ts"], json.dumps(case, ensure_ascii=False)))
    print(f"[advisor case {case['id']}] {case['headline']}", flush=True)
    return case


# ---------- web-call sessions (survive restarts and run across instances) ----------

def _store_session(s):
    """Upsert the call after each turn (background task). Works on SQLite and Postgres."""
    _save("INSERT INTO sessions VALUES (?,?,?) ON CONFLICT (id) DO UPDATE SET ts = excluded.ts, "
          "payload = excluded.payload WHERE excluded.ts >= sessions.ts",
          (s["id"], time.time(), json.dumps(converse.export_session(s), ensure_ascii=False, default=str)))


def _persist(s):
    """Store the call only if the caller agreed; if they withdrew consent, delete what was stored."""
    if s.get("consent", True):
        _store_session(s)
    else:
        _save("DELETE FROM sessions WHERE id = ?", (s["id"],))
        if s.get("prescreen_id") and s.get("consent_changed"):
            _save("DELETE FROM prescreens WHERE id = ?", (s["prescreen_id"],))


# ---------- retention (DPDP: personal data is kept only as long as needed) ----------

RETENTION_DAYS = converse.RETENTION_DAYS
RETAINED_TABLES = ("prescreens", "calls", "sessions", "advisor_cases", "latency_metrics", "wa_threads",
                   "study_results", "decision_audit")


def purge_expired(now=None):
    """Delete everything older than RETENTION_DAYS. Returns rows deleted per table."""
    cutoff = (now or time.time()) - RETENTION_DAYS * 86400
    out = {}
    for table in RETAINED_TABLES:
        before = _run(f"SELECT COUNT(*) FROM {table} WHERE ts < ?", (cutoff,), fetch=True)[0][0]
        if before:
            _run(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
        out[table] = int(before)
    return out


async def _purge_loop():
    while True:
        try:
            gone = await asyncio.to_thread(purge_expired)
            if any(gone.values()):
                print(f"[retention] deleted rows older than {RETENTION_DAYS} days: {gone}", flush=True)
        except Exception as e:  # noqa: BLE001  (storage may be down; try again later)
            print(f"[retention] purge failed: {type(e).__name__}", flush=True)
        await asyncio.sleep(6 * 3600)


@app.on_event("startup")
async def _start_retention():
    if WEBHOOK_KEY:
        asyncio.get_running_loop().create_task(_purge_loop())


@app.post("/v1/admin/purge")
def purge_now(x_webhook_key: str = Header(default="")):
    """Run the retention clean-up now (it also runs at start-up and every 6 hours)."""
    _check_key(x_webhook_key)
    return {"retention_days": RETENTION_DAYS, "deleted": purge_expired()}


def _save_study_quietly(s):
    try:
        _store_study(s)
    except Exception as e:  # noqa: BLE001
        print(f"[study] save failed: {type(e).__name__}: {e}", flush=True)


def get_session(sid):
    """The call from memory, or from the database (e.g. after a restart) if it's recent enough."""
    s = converse.SESSIONS.get(sid)
    if s is not None or not sid:
        return s
    try:
        rows = _run("SELECT ts, payload FROM sessions WHERE id = ?", (sid,), fetch=True)
    except Exception as e:  # noqa: BLE001
        print(f"[sessions] load failed: {type(e).__name__}: {e}", flush=True)
        return None
    if not rows or time.time() - rows[0][0] > SESSION_RESUME_HOURS * 3600:
        return None
    print(f"[web-call {sid}] resumed from storage", flush=True)
    return converse.restore_session(json.loads(rows[0][1]))


def case_from_session(s, question="", loan=None, tenure=None):
    """An advisor case from a live web call (partial profiles are fine)."""
    body = {k: str(v) for k, v in s["answers"].items()}
    applicant = s.get("applicant") or build_applicant(body)
    sim = (s.get("simulation") or {}).get("simulation")
    if (loan is not None or tenure is not None) and s.get("result") and s.get("applicant"):
        try:
            sim = simulate_for(s["applicant"], loan, tenure, WEB_CONTEXT)["simulation"]
        except simulate.InvalidSimulation:
            pass
    now = time.time()
    case = advisor.build(applicant, s["answers"], s["status"], s.get("result"), sim, question,
                         advisor.conversation_summary(s), advisor.recent_turns(s), "web-call",
                         s.get("prescreen_id") or "", s["id"], advisor.workspace(s, now))
    case["ts"] = now
    return case


@app.post("/v1/advisor-cases")
def create_advisor_case(body: AdvisorRequest, request: Request, x_webhook_key: str = Header(default="")):
    """Save a structured summary for a human loan advisor. A caller can only do it for their own call."""
    role, owner = _need_call(request, x_webhook_key, "advisor")
    if role != "staff" and (not body.session_id or owner["id"] != body.session_id):
        raise HTTPException(status_code=401, detail="You can only ask for an advisor about your own call.")
    s = get_session(body.session_id) if body.session_id else None
    if s is not None:
        case = case_from_session(s, body.question, body.loan_amount, body.tenure_months)
        s["advisor_case"] = case["id"]
    elif body.prescreen_id:
        said, applicant, result = _load_prescreen(body.prescreen_id)
        sim = None
        if (body.loan_amount is not None or body.tenure_months is not None) \
                and result.get("decision") != "need_more_info":
            context = WEB_CONTEXT if said.get("source") == "web-call" else None
            try:
                sim = simulate_for(applicant, body.loan_amount, body.tenure_months, context)["simulation"]
            except simulate.InvalidSimulation as e:
                raise HTTPException(status_code=422, detail=str(e)) from None
        answers = {k: v for k, v in said.items() if k in converse.BY_KEY}
        now = time.time()
        case = advisor.build(applicant, answers, {}, result, sim, body.question,
                             ["Created from the dashboard for a saved pre-screen."], [],
                             said.get("source") or "phone", body.prescreen_id,
                             workspace={"timeline": [{"ts": now, "label": "Advisor requested",
                                            "detail": "From a saved pre-screen (no call record)"}]})
        case["ts"] = now
    else:
        raise HTTPException(status_code=400, detail="Start a pre-screening first so there is something to summarise.")
    return save_case(case)


@app.patch("/v1/advisor-cases/{case_id}")
def update_advisor_case(case_id: str, body: CaseUpdate, x_webhook_key: str = Header(default="")):
    """An advisor picks up (in_review) or closes a case, with a note. Every change is kept in `events`."""
    _check_key(x_webhook_key)
    rows = _run("SELECT payload FROM advisor_cases WHERE id = ?", (case_id,), fetch=True)
    if not rows:
        raise HTTPException(status_code=404, detail="Case not found.")
    case = json.loads(rows[0][0])
    if case.get("status") == "closed" and body.status != "open":
        raise HTTPException(status_code=409, detail="This case is closed. Reopen it first.")
    if body.status in ("in_review", "closed") and not body.advisor.strip():
        raise HTTPException(status_code=422, detail="Please give the advisor's name.")
    case["status"] = body.status
    if body.advisor.strip():
        case["advisor"] = body.advisor.strip()
    case.setdefault("events", []).append({"ts": time.time(), "status": body.status,
                                          "by": body.advisor.strip() or "advisor", "note": body.note.strip()})
    _run("UPDATE advisor_cases SET payload = ? WHERE id = ?", (json.dumps(case, ensure_ascii=False), case_id))
    return case


# ---------- user study (STUDY.md) ----------

def _store_study(s, survey=None):
    """Save (or update) one study call's metrics; called after each turn of a study call and with the survey."""
    row = {"metrics": study.metrics(s), "survey": survey}
    old = _run("SELECT payload FROM study_results WHERE id = ?", (s["id"],), fetch=True)
    if old and survey is None:
        row["survey"] = json.loads(old[0][0]).get("survey")
    payload = json.dumps(row, ensure_ascii=False)
    if old:
        _run("UPDATE study_results SET ts = ?, payload = ? WHERE id = ?", (time.time(), payload, s["id"]))
    else:
        _run("INSERT INTO study_results VALUES (?,?,?)", (s["id"], time.time(), payload))
    return row


@app.get("/v1/study/personas")
def study_personas(x_webhook_key: str = Header(default="")):
    _check_key(x_webhook_key)
    return {"personas": study.public_personas()}


@app.post("/v1/study/survey")
def study_survey(body: SurveyRequest, x_webhook_key: str = Header(default="")):
    _check_key(x_webhook_key)
    s = get_session(body.session_id)
    if s is None or not s.get("study"):
        raise HTTPException(status_code=404, detail="Study call not found.")
    return _store_study(s, body.model_dump(exclude={"session_id"}))


def _study_rows():
    return [json.loads(p) for (p,) in _run("SELECT payload FROM study_results ORDER BY ts", fetch=True)]


@app.get("/v1/study/report")
def study_report(x_webhook_key: str = Header(default="")):
    _check_key(x_webhook_key)
    rows = _study_rows()
    return {**study.report(rows), "calls": rows}


@app.get("/v1/study/report.csv")
def study_report_csv(x_webhook_key: str = Header(default="")):
    _check_key(x_webhook_key)
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=study.CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for r in _study_rows():
        w.writerow({**r["metrics"], **(r.get("survey") or {}),
                    "wrong_first_pass": " ".join(r["metrics"]["wrong_first_pass"]),
                    "wrong_final": " ".join(r["metrics"]["wrong_final"])})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=loan-saathi-study.csv"})


@app.get("/v1/evaluation")
def evaluation_evidence(x_webhook_key: str = Header(default="")):
    """The Evaluation Lab: model metrics from models/*.json, safeguards checked against the code, and the
    last recorded test run (python -I -X utf8 src/evaluation.py run). Nothing is computed for show.
    Public: it holds no personal data (latency records are timings only)."""
    _open()
    try:
        live = latency_records()
    except Exception as e:  # noqa: BLE001  (the evidence page must load even if storage is down)
        print(f"[evaluation] latency records unavailable: {type(e).__name__}", flush=True)
        live = None
    return evaluation.evidence(live)


@app.get("/v1/advisor-cases")
def list_advisor_cases(limit: int = 50, x_webhook_key: str = Header(default="")):
    _check_key(x_webhook_key)
    limit = max(1, min(limit, 200))
    rows = _run("SELECT payload FROM advisor_cases ORDER BY ts DESC LIMIT ?", (limit,), fetch=True)
    return {"cases": [json.loads(p) for (p,) in rows]}


def llm_router():
    """Groq first (if GROQ_API_KEY), then Gemini (if GEMINI_API_KEY), sharing one response cache. Built per
    turn from the current settings, so changing a key needs no restart."""
    providers = []
    if GROQ_API_KEY:
        providers += [llm_mod.GroqProvider(GROQ_API_KEY, m) for m in GROQ_MODELS]
    if GEMINI_API_KEY:
        providers.append(llm_mod.GeminiProvider(GEMINI_API_KEY, GEMINI_MODEL,
                                                send=lambda *a: converse._gemini(*a)))
    return llm_mod.Router(providers, deadline_s=converse.LLM_DEADLINE_S, cache=converse._LLM_CACHE)


async def run_turn(s, text, action, defer, channel="web-call"):
    """One caller turn through the web-call state machine (any channel). `defer(fn, *args)` runs the
    storage writes later (a FastAPI background task, or directly from a background job)."""
    def save(pid, said, applicant, result, details):
        defer(_audit, pid, channel, applicant, result, s.get("consent", True))
        if not s.get("consent", True):
            _remember_prescreen(pid, {**said, "channel": channel}, applicant, result)
            print(f"[{channel} {s['id']} -> prescreen {pid}] {result['decision']} (not stored: no consent)", flush=True)
            return
        defer(_save, "INSERT INTO prescreens VALUES (?,?,?,?,?)", (
            pid, time.time(), json.dumps({**said, "channel": channel}, ensure_ascii=False),
            json.dumps(applicant, ensure_ascii=False), json.dumps({**result, "details": details}, ensure_ascii=False)))
        print(f"[{channel} {s['id']} -> prescreen {pid}] {result['decision']} band={result['risk_band']}", flush=True)

    try:
        return await converse.turn(s, text, decide, build_applicant, save, GEMINI_API_KEY, GEMINI_MODEL,
                                   action=action, assess=scoring.score, safety=assessment.safety_pre,
                                   llm=llm_router(),
                                   simulator=lambda a, loan=None, tenure=None, scen=False:
                                   simulate_for(a, loan, tenure, WEB_CONTEXT, scen),
                                   advisor=lambda sess, question: save_case(case_from_session(sess, question)))
    except Exception as e:  # noqa: BLE001  (never show a stack trace to the applicant)
        print(f"[{channel} {s['id']}] turn failed: {type(e).__name__}: {e}", flush=True)
        reply = converse.SAY["glitch"][s["lang"]]
        s["history"].append(("Saathi", reply))
        return reply


@app.post("/v1/converse")
async def converse_turn(body: ConverseRequest, request: Request, tasks: BackgroundTasks,
                        x_webhook_key: str = Header(default="")):
    """Browser web call: one caller utterance in (already transcribed), Saathi's reply out.
    An empty session_id starts a new call and returns the greeting, plus the call's token (public callers)."""
    role, owner = _caller(request, x_webhook_key, "turn")
    if not body.session_id:
        if role != "staff":
            if body.persona:
                raise HTTPException(status_code=401, detail="bad webhook key")  # the study kit is staff-only
            ip = _client_ip(request.headers, request.client)
            if _limited("call", ip) or _limited("call_total", "all"):
                raise HTTPException(status_code=429, detail="Saathi has taken a lot of calls today. Please try "
                                                            "again tomorrow.")
        info = None
        if body.persona:
            if body.persona not in study.BY_ID:
                raise HTTPException(status_code=422, detail="Unknown persona card.")
            info = {"participant": body.participant or "anon", "persona": body.persona,
                    "synthetic": body.synthetic}
        s, reply = converse.start(body.language, study=info, consent=body.consent or bool(info))
        token = secrets.token_urlsafe(24)
        s["token_hash"] = _token_hash(token)
        tasks.add_task(_persist, s)
        return {**converse.public(s), "reply": reply, "call_token": token}
    s = get_session(body.session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="call not found or expired; start a new one")
    if role != "staff" and (owner is None or owner["id"] != s["id"]):
        raise HTTPException(status_code=401, detail="This call belongs to another browser; start a new call.")
    reply = await run_turn(s, body.text, body.action, tasks.add_task)
    tasks.add_task(_persist, s)
    if s.get("study") and s.get("consent"):
        tasks.add_task(_save_study_quietly, s)
    return {**converse.public(s), "reply": reply}


@app.post("/v1/stt")
async def speech_to_text(
    request: Request,
    audio_file: UploadFile = File(...),
    language_code: str = Form(default="hi-IN"),
    output_format: str = Form(default="transcribe", alias="format"),
    x_webhook_key: str = Header(default=""),
):
    _need_call(request, x_webhook_key, "speech")
    _check_speech_api(GNANI_STT_API_KEY)
    audio = await audio_file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="audio_file must not be empty")
    try:
        async with httpx.AsyncClient(timeout=65.0) as client:
            upstream = await client.post(
                "https://api.vachana.ai/stt/v3",
                headers={"X-API-Key-ID": GNANI_STT_API_KEY},
                data={"language_code": language_code, "format": output_format},
                files={"audio_file": (
                    audio_file.filename or "speech.wav",
                    audio,
                    audio_file.content_type or "application/octet-stream",
                )},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Gnani STT request failed") from exc
    if not upstream.is_success:
        raise HTTPException(status_code=502, detail=f"Gnani STT returned HTTP {upstream.status_code}")
    try:
        result = upstream.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Gnani STT returned invalid JSON") from exc
    if (not isinstance(result, dict) or result.get("success") is not True
            or not isinstance(result.get("transcript"), str)):
        raise HTTPException(status_code=502, detail="Gnani STT response did not contain a transcript")
    return result


GNANI_TTS_URL = "https://api.vachana.ai/api/v1/tts/inference"
GNANI_TTS_SSE_URL = "https://api.vachana.ai/api/v1/tts/sse"
GNANI_STT_WS_URL = "wss://api.vachana.ai/stt/v3/stream"
STREAM_RATE = 24000  # streamed replies: raw 16-bit mono PCM at 24 kHz
# Gnani allows one active TTS request per key (HTTP 429 otherwise): replies and fillers take turns.
_TTS_LOCK = asyncio.Lock()


def _speech_text(body):
    lang = "en" if body.language.lower().startswith("en") else "hi"
    return spoken.speakable(body.text, lang) if body.spell_numbers else body.text


def _tts_payload(body, container="wav", rate=48000):
    return {"text": _speech_text(body), "voice": body.voice, "model": body.model, "language": body.language,
            "speed": body.speed, "audio_config": {"sample_rate": rate, "num_channels": 1, "sample_width": 2,
                                                  "encoding": "linear_pcm", "container": container}}


async def _tts_turn():
    try:
        await asyncio.wait_for(_TTS_LOCK.acquire(), timeout=20)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Voice is busy; please try again") from None


@app.post("/v1/tts")
async def text_to_speech(body: SpeechRequest, request: Request, x_webhook_key: str = Header(default="")):
    _need_call(request, x_webhook_key, "speech")
    _check_speech_api(GNANI_TTS_API_KEY)
    await _tts_turn()
    try:
        async with httpx.AsyncClient(timeout=65.0) as client:
            upstream = await client.post(GNANI_TTS_URL, headers={"X-API-Key-ID": GNANI_TTS_API_KEY},
                                         json=_tts_payload(body))
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Gnani TTS request failed") from exc
    finally:
        _TTS_LOCK.release()
    if not upstream.is_success:
        raise HTTPException(status_code=502, detail=f"Gnani TTS returned HTTP {upstream.status_code}")
    if not upstream.content:
        raise HTTPException(status_code=502, detail="Gnani TTS returned empty audio")
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "audio/wav"),
    )


@app.post("/v1/tts/stream")
async def text_to_speech_stream(body: SpeechRequest, request: Request, x_webhook_key: str = Header(default="")):
    """The reply as raw 16-bit mono PCM (24 kHz), streamed as Gnani produces it (TTS over SSE), so the
    browser starts speaking before the whole reply is synthesised."""
    _need_call(request, x_webhook_key, "speech")
    _check_speech_api(GNANI_TTS_API_KEY)
    await _tts_turn()
    client = httpx.AsyncClient(timeout=httpx.Timeout(65.0, connect=10.0))
    try:
        upstream = await client.send(client.build_request(
            "POST", GNANI_TTS_SSE_URL, headers={"X-API-Key-ID": GNANI_TTS_API_KEY},
            json=_tts_payload(body, container="raw", rate=STREAM_RATE)), stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        _TTS_LOCK.release()
        raise HTTPException(status_code=502, detail="Gnani TTS request failed") from exc
    if not upstream.is_success:
        await upstream.aclose()
        await client.aclose()
        _TTS_LOCK.release()
        raise HTTPException(status_code=502, detail=f"Gnani TTS returned HTTP {upstream.status_code}")

    async def pcm():
        carry = b""
        try:
            event = None
            async for line in upstream.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    try:
                        data = json.loads(line[5:])
                    except ValueError:
                        continue
                    if event == "error":
                        print(f"[tts stream] Gnani error: {data.get('message', '')}", flush=True)
                        break
                    chunk = carry + base64.b64decode(data.get("audio") or "")
                    cut = len(chunk) - len(chunk) % 2      # keep whole 16-bit samples
                    carry = chunk[cut:]
                    if cut:
                        yield chunk[:cut]
                    if data.get("is_final"):
                        break
        finally:
            await upstream.aclose()
            await client.aclose()
            _TTS_LOCK.release()

    return StreamingResponse(pcm(), media_type=f"audio/L16; rate={STREAM_RATE}; channels=1",
                             headers={"X-Sample-Rate": str(STREAM_RATE), "Cache-Control": "no-store"})


@app.websocket("/ws/stt")
async def speech_to_text_stream(ws: WebSocket):
    """Live microphone -> Gnani streaming STT. The browser sends {"key", "language"} first, then raw
    16-bit mono PCM at 16 kHz; it receives Gnani's events (speech_start, processing, transcript, error).
    Gnani's own voice-activity detection ends each turn. The API key never leaves the server."""
    await ws.accept()

    async def refuse(message, code=1008):
        try:
            await ws.send_json({"type": "error", "message": message})
            await ws.close(code=code)
        except Exception:  # noqa: BLE001
            pass

    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
    except Exception:  # noqa: BLE001
        return await refuse("Send {\"key\", \"language\"} first.")
    if not WEBHOOK_KEY or WEBHOOK_KEY.startswith("replace-with"):
        return await refuse("server not configured: set WEBHOOK_KEY", 1011)
    if not _is_staff(hello.get("key")):
        owned = PUBLIC_CALLS and _owned_session(str(hello.get("session") or ""), str(hello.get("token") or ""))
        if not owned:
            return await refuse("bad webhook key")
        if _limited("speech", _client_ip(ws.headers, ws.client)):
            return await refuse("Too many requests from your network. Please wait a minute.")
    if not GNANI_STT_API_KEY:
        return await refuse("Gnani API key is not configured", 1011)
    import websockets  # uvicorn[standard] ships it

    headers = {"x-api-key-id": GNANI_STT_API_KEY, "language_code": "en-IN" if hello.get("language") == "en"
               else "hi-IN", "x-sample-rate": "16000", "x-min-silence-ms": "600", "x-idle-timeout-sec": "120"}
    try:
        upstream = await websockets.connect(GNANI_STT_WS_URL, additional_headers=headers, open_timeout=10,
                                            max_size=2**20)
    except Exception as e:  # noqa: BLE001
        print(f"[stt stream] connect failed: {type(e).__name__}", flush=True)
        return await refuse("Live speech recognition is unavailable right now.", 1011)

    async def to_gnani():
        buf = b""
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes"):
                buf += msg["bytes"]
                while len(buf) >= 1024:                 # Gnani takes exactly 1,024-byte frames
                    await upstream.send(buf[:1024])
                    buf = buf[1024:]
            elif msg.get("text"):
                try:
                    if json.loads(msg["text"]).get("type") == "commit":
                        await upstream.send(json.dumps({"type": "commit"}))
                except ValueError:
                    pass

    async def to_browser():
        async for m in upstream:
            if isinstance(m, str):
                await ws.send_text(m)

    tasks = [asyncio.create_task(to_gnani()), asyncio.create_task(to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        await upstream.close()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


@app.post("/v1/metrics/latency")
def record_latency(body: LatencyReport, request: Request, tasks: BackgroundTasks,
                   x_webhook_key: str = Header(default="")):
    """A measured voice turn from a real browser session (for the Evaluation Lab's latency section)."""
    _need_call(request, x_webhook_key, "metrics")
    tasks.add_task(_save, "INSERT INTO latency_metrics VALUES (?,?,?)",
                   (uuid.uuid4().hex[:12], time.time(), json.dumps(body.model_dump())))
    return {"ok": True}


def latency_records(limit=2000):
    rows = _run("SELECT payload FROM latency_metrics ORDER BY ts DESC LIMIT ?", (limit,), fetch=True)
    return [json.loads(r[0]) for r in rows]


# ---------- WhatsApp channel ----------

_WA_SEEN = {}  # message id -> time, so Meta's retries are answered once


def whatsapp_ready():
    return all((WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET))


@app.get("/v1/whatsapp/webhook")
def whatsapp_verify(request: Request):
    """Meta's one-time webhook check: echo hub.challenge only for our verify token."""
    q = request.query_params
    if not whatsapp_ready():
        raise HTTPException(status_code=503, detail="WhatsApp is not configured")
    if q.get("hub.mode") != "subscribe" or not hmac.compare_digest(q.get("hub.verify_token", ""), WHATSAPP_VERIFY_TOKEN):
        raise HTTPException(status_code=403, detail="bad verify token")
    return PlainTextResponse(q.get("hub.challenge", ""))


@app.post("/v1/whatsapp/webhook")
async def whatsapp_receive(request: Request, tasks: BackgroundTasks):
    """Incoming WhatsApp messages. Signed by Meta (X-Hub-Signature-256); answered in the background so
    Meta gets its 200 at once."""
    if not whatsapp_ready():
        raise HTTPException(status_code=503, detail="WhatsApp is not configured")
    raw = await request.body()
    if not whatsapp.valid_signature(WHATSAPP_APP_SECRET, raw, request.headers.get("x-hub-signature-256", "")):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="not JSON") from None
    now = time.time()
    for k in [k for k, t in _WA_SEEN.items() if now - t > 3600]:
        _WA_SEEN.pop(k, None)
    for msg in whatsapp.parse(payload):
        if msg["id"] in _WA_SEEN:
            continue
        _WA_SEEN[msg["id"]] = now
        tasks.add_task(whatsapp_handle, msg)
    return {"ok": True}


async def _wa_send(client, body):
    r = await client.post(f"{WHATSAPP_GRAPH}/{WHATSAPP_PHONE_NUMBER_ID}/messages", json=body,
                          headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"})
    if not r.is_success:
        print(f"[whatsapp] send failed: HTTP {r.status_code}", flush=True)
    return r.is_success


async def _wa_voice_note(client, text, lang):
    """Saathi's reply as an OGG/Opus voice note from Gnani TTS, uploaded to WhatsApp; returns its media id."""
    body = SpeechRequest(text=text[:5000], language="en-IN" if lang == "en" else "hi-en")
    await _tts_turn()
    try:
        r = await client.post(GNANI_TTS_URL, headers={"X-API-Key-ID": GNANI_TTS_API_KEY},
                              json={**_tts_payload(body), "audio_config": {
                                  "sample_rate": 48000, "num_channels": 1, "sample_width": 2,
                                  "encoding": "oggopus", "container": "ogg"}})
    finally:
        _TTS_LOCK.release()
    if not r.is_success or not r.content:
        return None
    up = await client.post(f"{WHATSAPP_GRAPH}/{WHATSAPP_PHONE_NUMBER_ID}/media",
                           headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
                           data={"messaging_product": "whatsapp", "type": "audio/ogg"},
                           files={"file": ("saathi.ogg", r.content, "audio/ogg")})
    return up.json().get("id") if up.is_success else None


async def _wa_transcribe(client, media_id, lang):
    """A caller's voice note: fetch it from WhatsApp, then Gnani REST STT (reads OGG; 30 s limit)."""
    auth = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    meta = await client.get(f"{WHATSAPP_GRAPH}/{media_id}", headers=auth)
    if not meta.is_success or not meta.json().get("url"):
        return ""
    audio = await client.get(meta.json()["url"], headers=auth)
    if not audio.is_success or not audio.content or not GNANI_STT_API_KEY:
        return ""
    r = await client.post("https://api.vachana.ai/stt/v3", headers={"X-API-Key-ID": GNANI_STT_API_KEY},
                          data={"language_code": "en-IN" if lang == "en" else "hi-IN", "format": "transcribe"},
                          files={"audio_file": ("voice.ogg", audio.content, "audio/ogg")})
    try:
        return (r.json().get("transcript") or "").strip() if r.is_success else ""
    except ValueError:
        return ""


_WA_MEM = {}  # thread key -> session id, for callers who didn't agree to storage


def _wa_session(key):
    sid = _WA_MEM.get(key)
    if sid is None:
        rows = _run("SELECT payload FROM wa_threads WHERE id = ?", (key,), fetch=True)
        sid = json.loads(rows[0][0]).get("session_id") if rows else None
    s = get_session(sid) if sid else None
    return None if s is None or s.get("state") == "COMPLETED" else s


async def whatsapp_handle(msg):
    """One WhatsApp message -> one turn of the same conversation -> text (+ buttons), and a voice note
    back when the caller sent one."""
    key = whatsapp.thread_key(msg["wa_id"])
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            s = _wa_session(key)
            lang = s["lang"] if s else whatsapp.guess_language(msg["text"])
            text, action = msg["text"], msg["action"]
            if msg["kind"] == "audio":
                text = await _wa_transcribe(client, msg["media_id"], lang)
                if not text:
                    await _wa_send(client, whatsapp.outgoing(msg["wa_id"], converse.SAY["wa_unheard"][lang])[0])
                    return
            elif msg["kind"] == "unsupported":
                await _wa_send(client, whatsapp.outgoing(msg["wa_id"], converse.SAY["wa_unsupported"][lang])[0])
                return
            greeting = None
            if s is None:                     # a new thread: greet, ask about storage, answer what they said
                s, greeting = converse.start(lang, consent=False)
                s["channel"] = "whatsapp"
            reply = await run_turn(s, text, action, lambda fn, *a: fn(*a), channel="whatsapp")                 if (text or action) else greeting
            _persist(s)
            if s.get("consent"):
                _WA_MEM.pop(key, None)
                _run("INSERT INTO wa_threads VALUES (?,?,?) ON CONFLICT (id) DO UPDATE SET ts = excluded.ts, "
                     "payload = excluded.payload", (key, time.time(), json.dumps({"session_id": s["id"]})))
            else:
                _WA_MEM[key] = s["id"]
                _run("DELETE FROM wa_threads WHERE id = ?", (key,))
            if greeting and reply is not greeting:
                await _wa_send(client, whatsapp.outgoing(msg["wa_id"], greeting)[0])
            if greeting:                      # then ask, with two buttons, before anything is stored
                await _wa_send(client, whatsapp.outgoing(msg["wa_id"], converse.SAY["wa_consent"][lang], [
                    {"label": "Haan, save karo" if lang == "hi" else "Yes, save", "action": "consent:yes"},
                    {"label": "Nahi" if lang == "hi" else "No", "action": "consent:no"}])[0])
            if msg["kind"] == "audio" and GNANI_TTS_API_KEY:
                media = await _wa_voice_note(client, reply, s["lang"])
                if media:
                    await _wa_send(client, {"messaging_product": "whatsapp", "to": msg["wa_id"], "type": "audio",
                                            "audio": {"id": media}})
            for body in whatsapp.outgoing(msg["wa_id"], reply, converse.buttons(s)):
                await _wa_send(client, body)
    except Exception as e:  # noqa: BLE001
        print(f"[whatsapp] message failed: {type(e).__name__}: {e}", flush=True)


# ---------- daily advisor digest ----------

@app.post("/v1/advisor-digest")
def advisor_digest(x_webhook_key: str = Header(default="")):
    """A summary of open advisor cases, sent to ADVISOR_WEBHOOK_URL when one is set. Call it once a day
    from a scheduler (deploy/DEPLOY.md); it reports honestly whether anything was delivered."""
    _check_key(x_webhook_key)
    rows = _run("SELECT payload FROM advisor_cases ORDER BY ts DESC LIMIT 500", fetch=True)
    digest = advisor.digest([json.loads(r[0]) for r in rows], time.time())
    if not ADVISOR_WEBHOOK_URL:
        return {**digest, "delivered": False, "configured": False}
    try:
        r = httpx.post(ADVISOR_WEBHOOK_URL, json={"text": digest["text"]}, timeout=5.0)
        ok = r.is_success
    except httpx.HTTPError:
        ok = False
    print(f"[advisor digest] {digest['open']} open, {digest['in_review']} in review: "
          f"{'delivered' if ok else 'FAILED'}", flush=True)
    return {**digest, "delivered": ok, "configured": True}


# The built React app holds no data: it asks for the webhook key, then polls /v1/applications.
DASHBOARD_DIST = ROOT / "dashboard" / "dist"
if DASHBOARD_DIST.exists():
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")
else:
    @app.get("/dashboard/", include_in_schema=False)
    def dashboard_missing():
        return HTMLResponse("<p>Dashboard not built yet. Run <code>npm install && npm run build</code> "
                            "in the <code>dashboard/</code> folder, then restart the server.</p>")


if __name__ == "__main__":
    import uvicorn
    if not WEBHOOK_KEY or WEBHOOK_KEY.startswith("replace-with"):
        sys.exit("Refusing to start: set WEBHOOK_KEY in .env to a long random secret (every /v1 route needs it).")
    # HOST=0.0.0.0 inside a container; the default keeps a local run off the network.
    # An empty HOST= (as in .env.example) must not mean "every interface": it used to bind ":8000".
    uvicorn.run(app, host=os.environ.get("HOST", "").strip() or "127.0.0.1",
                port=int(os.environ.get("PORT", "").strip() or 8000))
