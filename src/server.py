"""Webhook for the Gnani Agent Builder loan pre-screening agent.

    POST /v1/prescreen   On-Call action: caller's answers in, decision + reply hint out
    POST /v1/post-call   Post-call trigger: stores whatever Gnani sends about the call
    GET  /v1/applications   Recent pre-screens (for the dashboard)
    GET  /dashboard/     Live React dashboard (built from dashboard/; asks for the webhook key)
    GET  /health

Run:  python -I -X utf8 src/server.py      (port 8000; set WEBHOOK_KEY in .env to require
      the X-Webhook-Key header)

The response is deliberately flat with simple keys so it works whether Agent Builder maps
response keys into "After API Call Variables" or hands the raw JSON to the LLM.
"""
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalize as norm  # noqa: E402
import scoring  # noqa: E402

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
DB_PATH = Path(os.environ.get("DB_PATH") or ROOT / "data" / "prescreens.db")

app = FastAPI(title="Loan pre-screening webhook")


# ---------- storage ----------

def _db():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS prescreens (
        id TEXT PRIMARY KEY, ts REAL, raw TEXT, applicant TEXT, result TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS calls (
        id TEXT PRIMARY KEY, ts REAL, payload TEXT)""")
    return con


def _check_key(key):
    if WEBHOOK_KEY and key != WEBHOOK_KEY:
        raise HTTPException(status_code=401, detail="bad webhook key")


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
}


# ---------- core ----------

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
    return {k: v for k, v in a.items() if v is not None or k == "years_employed"}


def decide(applicant):
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

    r = scoring.score(applicant)
    afford = scoring.affordable_loan(applicant)
    band = r["band"]
    ups = [t for t in (_reason_text(i, "up") for i in r["raises_risk"]) if t]
    downs = [t for t in (_reason_text(i, "down") for i in r["lowers_risk"]) if t]
    tips = [_TIPS[i["feature"]] for i in r["raises_risk"] if i["feature"] in _TIPS]

    requested = applicant["loan_amount"]
    tenure = int(afford["tenure_months"]) if afford else 24
    can_afford = afford and afford["loan_amount"] > 0

    if band == "high" or not can_afford:
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
            hint = (f"Good news: they are pre-approved for {inr(requested)} over {tenure} months, "
                    f"EMI about {inr(offer_emi)} per month. ")
        else:
            decision = "needs_review"
            hint = (f"Their request for {inr(requested)} over {tenure} months (EMI about {inr(offer_emi)}) "
                    "looks possible, and a loan officer will review it and call back within 2 working days. ")
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
        "reply_hint": hint,
    }
    return result, r


@app.get("/dashboard", include_in_schema=False)
def dashboard_redirect():
    return RedirectResponse("/dashboard/")


@app.get("/health")
def health():
    scoring._load()
    return {"ok": True}


@app.post("/v1/prescreen")
async def prescreen(request: Request, x_webhook_key: str = Header(default="")):
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
    with _db() as con:
        con.execute("INSERT INTO prescreens VALUES (?,?,?,?,?)", (
            pid, time.time(), json.dumps(body, ensure_ascii=False), json.dumps(applicant, ensure_ascii=False),
            json.dumps({**result, "details": details}, ensure_ascii=False)))
    print(f"[prescreen {pid}] {result['decision']} band={result['risk_band']} in={body}", flush=True)
    return result


@app.post("/v1/post-call")
async def post_call(request: Request, x_webhook_key: str = Header(default="")):
    _check_key(x_webhook_key)
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"raw": raw.decode("utf-8", "replace")}
    cid = uuid.uuid4().hex[:12]
    with _db() as con:
        con.execute("INSERT INTO calls VALUES (?,?,?)", (cid, time.time(), json.dumps(payload, ensure_ascii=False)))
    print(f"[post-call {cid}] keys={list(payload)[:15]}", flush=True)
    return {"ok": True}


@app.get("/v1/applications")
def applications(limit: int = 50, x_webhook_key: str = Header(default="")):
    _check_key(x_webhook_key)
    with _db() as con:
        rows = con.execute("SELECT id, ts, raw, applicant, result FROM prescreens ORDER BY ts DESC LIMIT ?",
                           (limit,)).fetchall()
        calls = con.execute("SELECT id, ts, payload FROM calls ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return {
        "prescreens": [{"id": i, "ts": t, "said": json.loads(r), "applicant": json.loads(a),
                        "result": json.loads(res)} for i, t, r, a, res in rows],
        "calls": [{"id": i, "ts": t, "payload": json.loads(p)} for i, t, p in calls],
    }


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
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
