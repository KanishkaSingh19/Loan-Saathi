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
import hmac
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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
import normalize as norm  # noqa: E402
import scoring  # noqa: E402
import simulate  # noqa: E402
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
# Comma-separated origins allowed to call the API from a browser, e.g. the Vercel dashboard.
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
# Where new advisor cases are announced: any URL that accepts a JSON POST, e.g. a Slack or Microsoft
# Teams incoming webhook ({"text": ...}). Empty = no notification, and applicants are told so.
ADVISOR_WEBHOOK_URL = os.environ.get("ADVISOR_WEBHOOK_URL", "").strip()
SESSION_RESUME_HOURS = 2  # a stored call can be resumed (e.g. after a restart) for this long

app = FastAPI(title="Loan pre-screening webhook")
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Webhook-Key"],
    )


class ConverseRequest(BaseModel):
    session_id: str = Field(default="", max_length=40)
    text: str = Field(default="", max_length=2000)
    language: str = "hi"
    # A button press on the call screen: confirm, change, correct:<field>, yes, no, choose:<n>, handoff.
    action: str = Field(default="", max_length=40)
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


# ---------- storage ----------

# SQLite file locally; Postgres (e.g. a free Neon database) when DATABASE_URL is set, because
# free container hosts wipe their disk on restart.
_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS prescreens (id TEXT PRIMARY KEY, ts {real}, raw TEXT, applicant TEXT, result TEXT)",
    "CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, ts {real}, payload TEXT)",
    "CREATE TABLE IF NOT EXISTS advisor_cases (id TEXT PRIMARY KEY, ts {real}, payload TEXT)",
    "CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, ts {real}, payload TEXT)",
    "CREATE TABLE IF NOT EXISTS study_results (id TEXT PRIMARY KEY, ts {real}, payload TEXT)",
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
    return result, r


@app.get("/dashboard", include_in_schema=False)
def dashboard_redirect():
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


def _load_prescreen(pid):
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
def simulate_route(body: SimulateRequest, x_webhook_key: str = Header(default="")):
    """What-if simulation for a completed pre-screen. Read-only: the saved request never changes."""
    _check_key(x_webhook_key)
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
def create_advisor_case(body: AdvisorRequest, x_webhook_key: str = Header(default="")):
    """Save a structured summary for a human loan advisor (prototype: nobody is notified)."""
    _check_key(x_webhook_key)
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


@app.get("/v1/advisor-cases")
def list_advisor_cases(limit: int = 50, x_webhook_key: str = Header(default="")):
    _check_key(x_webhook_key)
    limit = max(1, min(limit, 200))
    rows = _run("SELECT payload FROM advisor_cases ORDER BY ts DESC LIMIT ?", (limit,), fetch=True)
    return {"cases": [json.loads(p) for (p,) in rows]}


@app.post("/v1/converse")
async def converse_turn(body: ConverseRequest, tasks: BackgroundTasks, x_webhook_key: str = Header(default="")):
    """Browser web call: one caller utterance in (already transcribed), Saathi's reply out.
    An empty session_id starts a new call and returns the greeting."""
    _check_key(x_webhook_key)
    if not body.session_id:
        info = None
        if body.persona:
            if body.persona not in study.BY_ID:
                raise HTTPException(status_code=422, detail="Unknown persona card.")
            info = {"participant": body.participant or "anon", "persona": body.persona,
                    "synthetic": body.synthetic}
        s, reply = converse.start(body.language, study=info)
        tasks.add_task(_store_session, s)
        return {**converse.public(s), "reply": reply}
    s = get_session(body.session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="call not found or expired; start a new one")

    def save(pid, said, applicant, result, details):
        tasks.add_task(_save, "INSERT INTO prescreens VALUES (?,?,?,?,?)", (
            pid, time.time(), json.dumps(said, ensure_ascii=False), json.dumps(applicant, ensure_ascii=False),
            json.dumps({**result, "details": details}, ensure_ascii=False)))
        print(f"[web-call {s['id']} -> prescreen {pid}] {result['decision']} band={result['risk_band']}", flush=True)

    try:
        reply = await converse.turn(s, body.text, decide, build_applicant, save, GEMINI_API_KEY, GEMINI_MODEL,
                                    action=body.action, assess=scoring.score, safety=assessment.safety_pre,
                                    simulator=lambda a, loan=None, tenure=None, scen=False:
                                    simulate_for(a, loan, tenure, WEB_CONTEXT, scen),
                                    advisor=lambda sess, question: save_case(case_from_session(sess, question)))
    except Exception as e:  # noqa: BLE001  (never show a stack trace to the applicant)
        print(f"[web-call {s['id']}] turn failed: {type(e).__name__}: {e}", flush=True)
        reply = converse.SAY["glitch"][s["lang"]]
        s["history"].append(("Saathi", reply))
    tasks.add_task(_store_session, s)
    if s.get("study"):
        tasks.add_task(_save_study_quietly, s)
    return {**converse.public(s), "reply": reply}


@app.post("/v1/stt")
async def speech_to_text(
    audio_file: UploadFile = File(...),
    language_code: str = Form(default="hi-IN"),
    output_format: str = Form(default="transcribe", alias="format"),
    x_webhook_key: str = Header(default=""),
):
    _check_speech_access(x_webhook_key, GNANI_STT_API_KEY)
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


@app.post("/v1/tts")
async def text_to_speech(body: SpeechRequest, x_webhook_key: str = Header(default="")):
    _check_speech_access(x_webhook_key, GNANI_TTS_API_KEY)
    try:
        async with httpx.AsyncClient(timeout=65.0) as client:
            upstream = await client.post(
                "https://api.vachana.ai/api/v1/tts/inference",
                headers={"X-API-Key-ID": GNANI_TTS_API_KEY},
                json={
                    "text": body.text,
                    "voice": body.voice,
                    "model": body.model,
                    "language": body.language,
                    "speed": body.speed,
                    "audio_config": {
                        "sample_rate": 48000,
                        "num_channels": 1,
                        "sample_width": 2,
                        "encoding": "linear_pcm",
                        "container": "wav",
                    },
                },
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Gnani TTS request failed") from exc
    if not upstream.is_success:
        raise HTTPException(status_code=502, detail=f"Gnani TTS returned HTTP {upstream.status_code}")
    if not upstream.content:
        raise HTTPException(status_code=502, detail="Gnani TTS returned empty audio")
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "audio/wav"),
    )


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
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))
