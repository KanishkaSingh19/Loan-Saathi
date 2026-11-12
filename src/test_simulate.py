"""Boundary tests for the what-if simulator (src/simulate.py, POST /v1/simulate, voice what-ifs).

    python -I -X utf8 -W ignore src/test_simulate.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "sim-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""

os.environ["SHORT_CALL"] = "0"  # this suite scripts the full, fixed question order
import converse as cv  # noqa: E402
import scoring  # noqa: E402
import server  # noqa: E402
cv_switch = __import__("converse")
cv_switch.ASK_HISTORY = False  # this suite's fixed answer sequences predate the history questions;
                               # src/test_history.py tests them
import simulate  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(server.app)
H = {"X-Webhook-Key": "sim-test-key"}


def prescreen(body):
    r = c.post("/v1/prescreen", headers=H, json=body)
    assert r.status_code == 200, r.text
    return r.json()["prescreen_id"]


def sim(pid, loan=None, tenure=None, scen=False, status=200):
    body = {"prescreen_id": pid, "scenarios": scen}
    if loan is not None:
        body["loan_amount"] = loan
    if tenure is not None:
        body["tenure_months"] = tenure
    r = c.post("/v1/simulate", headers=H, json=body)
    assert r.status_code == status, (r.status_code, r.text)
    return r.json()


BASE = {"monthly_income": "40000", "loan_amount": "3 lakh", "tenure_months": "36", "age": "35",
        "work": "kirana shop", "years_in_work": "10", "housing": "own house", "other_loans_count": "1",
        "other_loans_emi": "8000", "other_loans_outstanding": "1.5 lakh"}
pid = prescreen(BASE)
r = sim(pid, scen=True)
orig = r["original"]
assert orig["loan_amount"]["value"] == 300000 and orig["tenure"]["value"] == 36
assert orig["emi"]["value"] == round(scoring.estimate_emi(300000, 36))
assert orig["total_repayment"]["value"] == round(scoring.estimate_emi(300000, 36) * 36)
assert orig["affordability"]["value"] == 12000 and orig["label"] == "Simulation only — not a lender offer or approval."
assert orig["affordability"]["source_text"] == "Based on affordability rules"
print("ok - original request through the real decision path")

# ---- the three scenarios ----
safer, request, higher = r["scenarios"]
assert [s["kind"] for s in r["scenarios"]] == ["safer", "request", "higher"]
assert safer["loan_amount"]["value"] < request["loan_amount"]["value"] < higher["loan_amount"]["value"]
assert safer["emi"]["value"] < request["emi"]["value"] < higher["emi"]["value"]
assert safer["fits"] and (8000 + safer["emi"]["value"]) <= 0.4 * 40000 + 1          # within 40% of income
assert higher["loan_amount"]["value"] == 450000 and not higher["fits"]
assert all(s["tenure"]["value"] == 36 for s in r["scenarios"])
print("ok - safer option / your request / higher burden")

heavy = prescreen({**BASE, "other_loans_emi": "17000"})                             # EMIs already 42.5%
s0 = sim(heavy, scen=True)["scenarios"][0]
assert s0.get("unavailable") and "40%" in s0["blurb"], s0
print("ok - no safer option when existing EMIs already use 40% of income")

# ---- boundaries ----
small = sim(pid, 1000, 12)["simulation"]
assert small["loan_amount"]["value"] == 1000 and small["fits"] and small["emi"]["value"] > 0
assert "at least ₹1,000" in sim(pid, 999, 12, status=422)["detail"]
assert "at least" in sim(pid, 0, 12, status=422)["detail"]
assert "at least" in sim(pid, -50000, 12, status=422)["detail"]
big = sim(pid, 10_00_00_000, 120)["simulation"]
assert not big["fits"] and big["status"] != "within" and big["emi_share"] > 100
assert "at most" in sim(pid, 10_00_00_001, 120, status=422)["detail"]
assert sim(pid, 300000, 3)["simulation"]["tenure"]["value"] == 3
assert sim(pid, 300000, 120)["simulation"]["tenure"]["value"] == 120
assert "3 months and 10 years" in sim(pid, 300000, 2, status=422)["detail"]
assert "3 months and 10 years" in sim(pid, 300000, 121, status=422)["detail"]
assert c.post("/v1/simulate", headers=H, json={"prescreen_id": pid, "loan_amount": "abc"}).status_code == 422
assert c.post("/v1/simulate", headers=H, json={"prescreen_id": pid}).status_code == 200
print("ok - very small / very large loans, minimum / maximum tenure, invalid values")

# Affordability boundary: the largest loan whose EMI equals the estimated affordability fits; more doesn't.
edge = int(scoring.loan_for_emi(12000, 36) // 1000 * 1000)
at = sim(pid, edge, 36)["simulation"]
over = sim(pid, edge + 2000, 36)["simulation"]
assert at["fits"] and at["headroom"]["value"] >= 0, at["headroom"]
assert not over["fits"] and over["headroom"]["value"] < 0 and over["status"] == "pressure"
print(f"ok - affordability boundary at {edge}")

# EMI greater than the whole income.
whole = sim(pid, 2_00_00_000, 12)["simulation"]
assert whole["emi"]["value"] > 40000 and not whole["fits"] and whole["emi_share"] > 100
print("ok - EMI above income is flagged, never within")

# ---- repeated changes leave the saved request alone ----
before = [p for p in c.get("/v1/applications", headers=H).json()["prescreens"] if p["id"] == pid][0]
for i in range(25):
    out = sim(pid, 50000 + i * 20000, 6 + (i % 10) * 12)
    assert out["original"]["loan_amount"]["value"] == 300000
after = [p for p in c.get("/v1/applications", headers=H).json()["prescreens"] if p["id"] == pid][0]
assert json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)
print("ok - 25 repeated simulations; the saved request is unchanged")

# ---- the simulator can't bypass safety rules ----
debt = prescreen({**BASE, "other_loans_outstanding": "36 lakh"})                    # over 24 months of income
for loan, tenure in ((1000, 120), (20000, 60), (300000, 36)):
    s = sim(debt, loan, tenure)["simulation"]
    assert s["debt_over_limit"] and s["status"] != "within", s
paused = prescreen({"monthly_income": "20000", "loan_amount": "1 lakh", "other_loans_count": "0",
                    "other_loans_emi": "5000"})                                     # contradictory: paused
assert "Finish the assessment first" in sim(paused, 50000, 12, status=409)["detail"]
missing = prescreen({"loan_amount": "1 lakh"})
sim(missing, 50000, 12, status=409)
assert c.post("/v1/simulate", json={"prescreen_id": pid}).status_code == 401
sim("nope", 1000, 12, status=404)
print("ok - debt rule, paused and incomplete assessments, auth: no bypass")

# ---- wording: simulations, never offers or approvals ----
blob = json.dumps(sim(pid, 200000, 48, scen=True), ensure_ascii=False).lower()
for bad in ("approved", "guarantee", "instant", "bank of", "% interest rate", "offer from"):
    assert bad not in blob, bad
assert blob.count("not a lender offer") >= 4
assert "not a lender offer" in simulate.spoken(sim(pid, 200000, 48)["simulation"])
print("ok - labelled as simulations, no approvals or lender offers")

# ---- comparisons ----
cmp = sim(pid, 200000, 48)["compare"]
assert cmp["emi"]["direction"] == "down" and cmp["headroom"]["direction"] == "up"
assert cmp["loan_amount"]["text"] == "−₹1,00,000" and cmp["tenure"]["text"] == "+12 months"
fake_o = {"loan_amount": {"value": 100000}, "tenure": {"value": 36}, "emi": {"value": 3615},
          "total_repayment": {"value": 130140}, "headroom": {"value": 8000}, "risk": {"band": "medium"}}
fake_s = {"loan_amount": {"value": 200000}, "tenure": {"value": 36}, "emi": {"value": 7230},
          "total_repayment": {"value": 260280}, "headroom": {"value": 4770}, "risk": {"band": "low"}}
assert simulate.compare(fake_o, fake_s)["risk"]["direction"] == "same"  # heavier loan is never "safer"
print("ok - changes shown from the borrower's side; a heavier loan is never shown as lower risk")

# ---- voice what-ifs ----
sid = c.post("/v1/converse", headers=H, json={"language": "en"}).json()["session_id"]
for text in ("3 lakh", "3 years", "40,000", "yes", "kirana shop", "10 years", "1", "8,000", "1.5 lakh",
             "own house", "35"):
    t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text}).json()
say = lambda text: c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text}).json()  # noqa: E731
t = say("what if I take 2 lakh?")
assert t["state"] == cv.VERIFYING, "no what-ifs before the profile is confirmed"
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "yes", "action": "confirm"}).json()
t = say("What if I take ₹2 lakh?")
assert t["state"] == cv.WHAT_IF and t["simulation"]["simulation"]["loan_amount"]["value"] == 200000, t
assert "not a lender offer" in t["reply"] and "₹2,00,000" in t["reply"]
t = say("What if I choose 5 years?")
s = t["simulation"]["simulation"]
assert s["loan_amount"]["value"] == 200000 and s["tenure"]["value"] == 60, s          # keeps the amount
t = say("What is the maximum EMI I can afford?")
assert "₹12,000" in t["reply"], t["reply"]
t = say("EMI kam rakhni hai")
assert t["simulation"]["simulation"]["emi"]["value"] < orig["emi"]["value"]
t = say("what if I take 50 crore")
assert "at most" in t["reply"] and t["state"] == cv.WHAT_IF
t = say("back to my original request")
assert t["simulation"]["simulation"]["loan_amount"]["value"] == 300000
t = say("next steps")
assert t["state"] == cv.NEXT_STEPS, t
t = say("what if 1 lakh for 2 years")
assert t["state"] == cv.WHAT_IF and t["simulation"]["simulation"]["tenure"]["value"] == 24
assert cv.SESSIONS[sid]["result"]["offer_amount"] == 300000, "the original result never changes"
print("ok - voice what-ifs: amount, tenure, maximum EMI, lower EMI, invalid, back, after next steps")


# =====================================================================================
# The "What if?" experience: the cases a borrower actually hits.
# =====================================================================================
o = sim(pid)["original"]

# normal simulation: ₹2 lakh instead of ₹3 lakh, same deterministic EMI code as the assessment
r = sim(pid, 200000)
s = r["simulation"]
assert s["loan_amount"]["value"] == 200000 and s["tenure"]["value"] == 36
assert s["emi"]["value"] == round(scoring.estimate_emi(200000, 36))
assert s["total_obligations"]["value"] == 8000 + s["emi"]["value"]
assert s["emi_share"] == round((8000 + scoring.estimate_emi(200000, 36)) / 40000 * 100, 1)
assert s["burden"]["percent"] == round(s["emi_share"]) and s["outcome"]["title"]
assert s["label"] == simulate.LABEL and r["original"] == o, "the original comes back unchanged"
print("ok - normal simulation: EMI, burden and assessment from the existing rules")

# very low and very high amounts
low = sim(pid, 5000)["simulation"]
assert low["fits"] and low["emi"]["value"] < 300 and low["outcome"]["title"] == "Further review recommended"
high = sim(pid, 50_00_000)["simulation"]
assert not high["fits"] and high["burden"]["level"] == "over" and high["outcome"]["title"] in (
    "Lower-burden option recommended", "Not right now")
assert high["emi_share"] > 100
print("ok - very low and very high amounts")

# boundary values: the 40% line the lower-burden option uses, and the 50% affordability line
comfort = int(scoring.loan_for_emi(0.4 * 40000 - 8000, 36) // 1000 * 1000)
safer = sim(pid, scen=True)["scenarios"][0]
assert safer["loan_amount"]["value"] == comfort and safer["burden"]["percent"] <= 40
assert safer["title"] == "Lower-burden option"
assert sim(pid, comfort + 5000)["simulation"]["emi_share"] > 40
within = prescreen({**BASE, "loan_amount": "50000"})                                # already under 40%
s0 = sim(within, scen=True)["scenarios"][0]
assert s0.get("unavailable") and "already" in s0["blurb"], "no invented lower amount"
assert sim(pid, 1000)["simulation"]["loan_amount"]["value"] == 1000
assert sim(pid, 10_00_00_000)["simulation"]["loan_amount"]["value"] == 10_00_00_000
print("ok - boundary values: 40% lower-burden line, 50% limit, min and max amounts")

# risk: recalculated by the model, except where the model can't be trusted (bigger loan rated safer)
assert sim(pid, 200000)["compare"]["risk"]["recalculated"] is True
fake_o["risk"]["band"], fake_s["risk"]["band"] = "medium", "low"
r = simulate.compare(fake_o, fake_s)["risk"]
assert r["recalculated"] is False and "not recalculated" in r["text"]
print("ok - risk is never shown lower for a bigger loan; it says it wasn't recalculated")

# the original application stays as it was, and stays reachable
before = json.dumps(server._load_prescreen(pid), sort_keys=True, default=str)
for loan in (100000, 900000, 1000, 250000):
    assert sim(pid, loan)["original"] == o
assert json.dumps(server._load_prescreen(pid), sort_keys=True, default=str) == before
print("ok - original application unchanged and always returned")

# repeated simulations: the same question gives the same answer, every time
first = sim(pid, 175000, 24)
for _ in range(5):
    again = sim(pid, 175000, 24)
    assert again["simulation"] == first["simulation"] and again["compare"] == first["compare"]
print("ok - repeated simulations are deterministic")

# invalid amounts
for bad in ("abc", "NaN", "1e99", -1, 0, 999):
    r = c.post("/v1/simulate", headers=H, json={"prescreen_id": pid, "loan_amount": bad})
    assert r.status_code == 422, (bad, r.status_code, r.text)
assert c.post("/v1/simulate", headers=H, json={"prescreen_id": pid, "loan_amount": None}).status_code == 200
print("ok - invalid amounts are refused with a reason")

# voice: Hinglish, ambiguous and bare amounts; the reply's numbers come from the simulator
sid = c.post("/v1/converse", headers=H, json={"language": "hi"}).json()["session_id"]
for text in ("3 lakh", "3 saal", "40,000", "haan", "kirana shop", "10 saal", "1", "8,000", "1.5 lakh",
             "apna ghar", "35"):
    t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text}).json()
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "haan", "action": "confirm"}).json()
original_result = json.dumps(cv.SESSIONS[sid]["result"], sort_keys=True, default=str)
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "Agar main 2 lakh loon toh?"}).json()
sm = t["simulation"]["simulation"]
assert t["state"] == cv.WHAT_IF and sm["loan_amount"]["value"] == 200000
assert sm["emi"]["text"].replace("/month", "") in t["reply"], "the spoken EMI is the simulator's"
before_sim = t["simulation"]
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "agar 2 ya 3 lakh loon"}).json()
assert "₹2,00,000" in t["reply"] and "₹3,00,000" in t["reply"] and "Kaunsi" in t["reply"], t["reply"]
assert t["simulation"] == before_sim, "an ambiguous amount is asked about, never simulated"
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "agar 40 loon toh"}).json()
assert "40" in t["reply"] and "Poori rakam" in t["reply"] and t["simulation"] == before_sim, t["reply"]
assert t["state"] == cv.WHAT_IF, "a bare number never skips to next steps"
assert json.dumps(cv.SESSIONS[sid]["result"], sort_keys=True, default=str) == original_result
print("ok - voice: Hinglish what-if, ambiguous and bare amounts asked about, original result kept")

print("All simulator tests passed.")
