"""Tests the applicant-facing assessment: provenance, wording, explanations, completeness and the
safety check (src/assessment.py, wired in server.decide and the web call).

    python -I -X utf8 -W ignore src/test_assessment.py
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "assessment-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""

os.environ["SHORT_CALL"] = "0"  # this suite scripts the full, fixed question order
import assessment as asm  # noqa: E402
import converse as cv  # noqa: E402
import scoring  # noqa: E402
import server  # noqa: E402
cv.ASK_HISTORY = False  # fixed answer sequences predate the history questions; see test_history.py
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(server.app)
H = {"X-Webhook-Key": "assessment-test-key"}

KIRANA = {"monthly_income": "30 hazaar", "loan_amount": "50,000", "tenure_months": "1 saal", "age": "45",
          "work": "kirana ki dukaan", "years_in_work": "15 saal", "housing": "apna ghar", "other_loans_count": "0"}
TOO_MUCH = {"monthly_income": "25 hazaar", "loan_amount": "3 lakh", "tenure_months": "2 saal", "age": "34",
            "work": "private company me sales", "years_in_work": "6 saal", "housing": "kiraye ka ghar",
            "other_loans_count": "1", "other_loans_outstanding": "40 hazaar", "other_loans_emi": "2000"}
HUGE_DEBT = {"monthly_income": "40000", "loan_amount": "1 lakh", "tenure_months": "2 saal", "age": "30",
             "work": "bus driver", "years_in_work": "2", "housing": "rent", "other_loans_count": "4",
             "other_loans_outstanding": "3651000", "other_loans_emi": "9000"}


def run(body, context=None):
    result, details = server.decide(server.build_applicant(body), context=context)
    return result, details, result["assessment"]


def all_figures(a):
    yield from (a[k] for k in ("risk", "affordability", "requested_loan", "requested_emi") if a.get(k))
    if a.get("suggested_range"):
        yield a["suggested_range"]
    for v in (a.get("picture") or {}).values():
        if isinstance(v, dict) and "source" in v:
            yield v


# ---- provenance: every number says where it came from ----
for body in (KIRANA, TOO_MUCH, HUGE_DEBT):
    _, _, a = run(body)
    for f in all_figures(a):
        assert f["source"] in asm.SOURCES and f["source_text"], f
assert run(KIRANA)[2]["picture"]["monthly_income"]["source_text"] == "You told us"
assert run(KIRANA)[2]["affordability"]["source_text"] == "Based on affordability rules"
assert run(KIRANA)[2]["risk"]["source_text"] == "Estimated by model"
no_tenure = {k: v for k, v in KIRANA.items() if k != "tenure_months"}
a = run(no_tenure)[2]
assert a["picture"]["tenure"]["source"] == "default" and a["picture"]["tenure"]["value"] == 24, a["picture"]["tenure"]
no_emi = {k: v for k, v in TOO_MUCH.items() if k != "other_loans_emi"}
assert run(no_emi)[2]["picture"]["existing_emi"]["source"] == "estimate"
print("ok - every number carries its source")

# ---- numbers agree with the rules ----
r, _, a = run(TOO_MUCH)
income, existing = 25000, 2000
assert a["affordability"]["value"] == round(0.5 * income - existing)          # ₹10,500/month
assert a["requested_emi"]["value"] == round(scoring.estimate_emi(300000, 24))
assert a["picture"]["remaining_income"]["value"] == round(income - existing - scoring.estimate_emi(300000, 24))
assert a["suggested_range"]["high"] == r["offer_amount"] == 210000, (a["suggested_range"], r["offer_amount"])
assert a["suggested_range"]["low"] < a["suggested_range"]["high"]
assert a["status"] == "pressure" and a["status_text"] == "This loan may put too much pressure on your monthly income."
_, _, a = run(KIRANA)
assert a["status"] == "within" and a["status_text"] == "Within your estimated repayment capacity."
assert a["suggested_range"]["text"] == "₹50,000"                                # capped at the request
_, _, a = run(HUGE_DEBT)
assert a["suggested_range"] is None and a["status"] == "pressure"
print("ok - figures match the affordability rule and the decision")

# ---- wording: estimates, never approvals ----
for body in (KIRANA, TOO_MUCH, HUGE_DEBT):
    r, _, a = run(body)
    text = json.dumps(a, ensure_ascii=False).lower() + r["reply_hint"].lower()
    assert "approved" not in text.replace("final approval", ""), text
assert run(KIRANA)[2]["decision_label"] == "Eligible for further review"
assert cv._fallback_result({"decision": "pre_approved", "offer_amount_text": "₹50,000", "offer_emi_text": "₹4,584",
                            "tenure_months": 12}, "en").count("pre-approved") == 0
# The borrower-facing result: the existing decision in careful words, never an approval.
FORBIDDEN = re.compile(r"approved|approval|guarantee|sanction", re.I)
for body, title in ((KIRANA, "Further review recommended"), (HUGE_DEBT, "Not right now")):
    o = run(body)[2]["outcome"]
    assert o["title"] == title and not FORBIDDEN.search(o["title"] + " " + o["detail"].replace(
        "before any decision", "")), o
assert "2 years of income" in run(HUGE_DEBT)[2]["outcome"]["detail"]
assert run(TOO_MUCH)[2]["outcome"]["title"] in ("Lower-burden option recommended", "Not right now")
print("ok - no approval claims")

# ---- borrowing picture: totals and burden come from the same numbers as the rules ----
for body in (KIRANA, TOO_MUCH, HUGE_DEBT):
    p = run(body)[2]["picture"]
    total = p["existing_emi"]["value"] + p["new_emi"]["value"]
    assert abs(p["total_obligations"]["value"] - total) <= 1, p
    assert abs(p["remaining_income"]["value"] - (p["monthly_income"]["value"] - total)) <= 1, p
    b = p["burden"]
    assert b["percent"] == round(total / p["monthly_income"]["value"] * 100) and b["limit"] == 50
    assert b["level"] == ("comfortable" if b["percent"] <= 40 else "stretched" if b["percent"] <= 50 else "over")
    assert p["total_obligations"]["source"] == "math" and b["source"] == "rules"
print("ok - borrowing picture totals and burden")

# ---- why: existing explanations, no protected traits, direction-checked ----
for body in (KIRANA, TOO_MUCH, HUGE_DEBT):
    _, details, a = run(body)
    texts = " ".join(i["text"] for i in a["why"]["helping"] + a["why"]["risk"]).lower()
    assert not re.search(r"gender|female|male|married|marital|single|widow|\bage\b", texts), texts
    assert all(i["source"] in ("rules", "model") for i in a["why"]["helping"] + a["why"]["risk"])
_, details, a = run(HUGE_DEBT)
risk_text = " ".join(i["text"] for i in a["why"]["risk"])
help_text = " ".join(i["text"] for i in a["why"]["helping"])
assert "more than 2 years of income" in risk_text
assert "little is owed" not in help_text and "low compared to income" not in help_text, help_text
# The model really does rate this debt as safer (the known flaw); the reason is dropped, not shown.
debt = next(f for f in details["all_factors"] if f["feature"] == "BUREAU_ACTIVE_DEBT")
assert debt["raw"] > debt["typical_raw"]
assert server._direction_ok({"feature": "BUREAU_ACTIVE_DEBT", "raw": 5e6, "typical_raw": 1e5}, "down") is False
assert server._direction_ok({"feature": "BUREAU_ACTIVE_DEBT", "raw": 0, "typical_raw": 1e5}, "down") is True
assert server._direction_ok({"feature": "YEARS_EMPLOYED", "raw": 15, "typical_raw": 5}, "up") is False
_, _, a = run(KIRANA)
assert any("within your estimated affordability" in i["text"] for i in a["why"]["helping"])
print("ok - explanations come from the rules and the model's spoken-safe factors only")

# ---- completeness ----
_, _, a = run(KIRANA)
labels = {i["label"]: i["state"] for i in a["completeness"]}
for k in ("Monthly income", "Requested loan", "Existing EMI", "Existing debt", "Tenure"):
    assert labels[k] == "known", (k, labels)
for k in ("Credit bureau report unavailable", "Income proof unavailable", "Employment verification unavailable"):
    assert labels[k] == "unavailable", (k, labels)
assert "score" not in json.dumps(a["completeness"]).lower()
labels = {i["label"]: i for i in run(no_tenure)[2]["completeness"]}
assert labels["Tenure"]["state"] == "missing" and "24 months" in labels["Tenure"]["note"]
print("ok - completeness lists what is known and what is not")

# ---- safety check ----
_, _, a = run(TOO_MUCH)
states = {ch["id"]: ch["state"] for ch in a["safety"]["checks"]}
assert states == {"required": "pass", "validated": "pass", "confirmed": "na", "contradictions": "na",
                  "consistency": "pass", "debt": "pass", "affordability": "flag"}, states
assert a["safety"]["passed"]
_, _, a = run(HUGE_DEBT, {"channel": "web", "confirmed": True, "open_checks": 0})
states = {ch["id"]: ch["state"] for ch in a["safety"]["checks"]}
assert states["confirmed"] == "pass" and states["contradictions"] == "pass" and states["debt"] == "flag", states

clash = {"monthly_income": "20000", "loan_amount": "1 lakh", "other_loans_count": "0", "other_loans_emi": "5000"}
r, details, a = run(clash)
assert r["decision"] == "need_more_info" and details is None and a["status"] == "paused", r
assert "no other loans were mentioned" in a["needed"][0]
r, _, a = run(KIRANA, {"channel": "web", "confirmed": False, "open_checks": 1})
assert r["decision"] == "need_more_info" and len(a["needed"]) == 2, a["needed"]
r, _, a = run({**KIRANA, "tenure_months": "50 saal"})
assert r["decision"] == "need_more_info" and "tenure" in a["needed"][0], a
# The phone webhook keeps its old message for missing income.
r = c.post("/v1/prescreen", headers=H, json={"loan_amount": "50000"}).json()
assert r["decision"] == "need_more_info" and "monthly income" in r["reply_hint"], r
print("ok - safety check passes, flags and pauses")

# ---- web call: safety check runs before the model, result carries the assessment ----
sid = c.post("/v1/converse", headers=H, json={"language": "en"}).json()["session_id"]
for text in ("3 lakh", "3 years", "40,000", "kirana shop", "10 years", "1", "8,000", "1.5 lakh", "own house", "35"):
    t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text}).json()
real = server.assessment.safety_pre
scored = []
server.assessment.safety_pre = lambda a, ctx=None: [asm._check("consistency", "Safety rules passed", "fail",
                                                                "Please check: test failure.")]
orig_score = server.scoring.score
server.scoring.score = lambda *a, **k: scored.append(1) or orig_score(*a, **k)
try:  # the route looks both up on each request, so these patches reach the web call
    t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "yes", "action": "confirm"}).json()
finally:
    server.assessment.safety_pre = real
    server.scoring.score = orig_score
assert t["state"] == cv.RECOVERY and t["result"] is None and "test failure" in t["reply"], t
assert not scored, "the model must not run when the safety check fails"
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "yes", "action": "yes"}).json()
assert t["state"] == cv.VERIFYING, t
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "yes", "action": "confirm"}).json()
a = t["result"]["assessment"]
assert t["state"] == cv.RESULT and a and a["safety"]["passed"], t
states = {ch["id"]: ch["state"] for ch in a["safety"]["checks"]}
assert states["confirmed"] == "pass" and states["contradictions"] == "pass", states
assert a["picture"]["existing_debt"]["text"] == "₹1,50,000" and a["picture"]["tenure"]["text"] == "36 months"
print("ok - web call: safety check before the model, assessment with the result")

print("All assessment tests passed.")
