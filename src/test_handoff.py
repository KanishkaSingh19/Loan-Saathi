"""Advisor workspace: what an advisor needs without listening to the call.

    python -I -X utf8 -W ignore src/test_handoff.py

Corrections come only from the session's change log, flags only from checks that actually ran, the
timeline only from recorded transitions, and the transcript is the caller's exact words. The handoff
keeps the confirmed profile and the assessment, and never claims an advisor was told when nobody was.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "handoff-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["ADVISOR_WEBHOOK_URL"] = ""

import advisor  # noqa: E402
import converse as cv  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(server.app)
H = {"X-Webhook-Key": "handoff-test-key"}
ANSWERS = {"loan_amount": "3 lakh", "tenure_months": "3 years", "monthly_income": "30,000",
           "work": "kirana shop", "years_in_work": "10 years", "other_loans_count": "1",
           "other_loans_emi": "8,000", "other_loans_outstanding": "1.5 lakh", "past_loans": "2",
           "ever_refused": "no", "ever_late": "no", "late_last_year": "no", "late_over_month": "no",
           "overdue_now": "no", "housing": "own house", "age": "35"}


def new(lang="en"):
    return c.post("/v1/converse", headers=H, json={"language": lang}).json()["session_id"]


def say(sid, text="", action=""):
    r = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text, "action": action})
    assert r.status_code == 200, r.text
    return r.json()


def until_summary(sid, answers=ANSWERS):
    t = say(sid, answers["loan_amount"])
    for _ in range(30):
        if t["state"] != cv.COLLECTING:
            return t
        t = say(sid, answers[t["asking"]])
    raise AssertionError(t)


def handoff(sid, question=""):
    r = c.post("/v1/advisor-cases", headers=H, json={"session_id": sid, "question": question})
    assert r.status_code == 200, r.text
    return r.json()


# ---- 1. a full call: corrected income, confirmed, assessed, simulated, then the advisor ----
sid = new()
until_summary(sid)
say(sid, "My income is wrong")
say(sid, "40,000")
t = say(sid, "Yes, that's correct", "confirm")
assert t["state"] == cv.RESULT
result = cv.SESSIONS[sid]["result"]
say(sid, "What if I take 2 lakh?")
case = handoff(sid, "Can I get a longer tenure?")

corr = case["corrections"]
assert [(x["label"], x["before"], x["after"]) for x in corr] == [("Income", "₹30,000/month", "₹40,000/month")], corr
assert corr[0]["how"] == "Corrected at the read-back" and corr[0]["status"] == "Confirmed by applicant"
assert corr[0]["said"] == "40,000", "the caller's exact words for the confirmed value"
print("ok 1 - correction shown as originally heard -> confirmed value")

labels = [e["label"] for e in case["timeline"]]
order = ["Call started", "Information collected", "Correction requested", "Income changed",
         "Profile confirmed", "Assessment completed", "Simulation performed", "Advisor requested"]
assert [x for x in labels if x in order] == order, labels
assert all(a["ts"] <= b["ts"] for a, b in zip(case["timeline"], case["timeline"][1:])), "in time order"
sim_event = next(e for e in case["timeline"] if e["label"] == "Simulation performed")
assert "₹2,00,000" in sim_event["detail"]
print("ok 2 - timeline from the recorded state trail, in order")

said = [x["text"] for x in case["transcript"] if x["who"] == "caller"]
assert "My income is wrong" in said and "What if I take 2 lakh?" in said and "30,000" in said
assert case["transcript"][0]["who"] == "saathi"
assert len(case["transcript"]) == len(cv.SESSIONS[sid]["history"])
print("ok 3 - transcript keeps the caller's exact words")

x = case["assessment"]
assert x["risk"].lower() == result["assessment"]["risk"]["band"]
assert x["requested_emi"] == result["assessment"]["requested_emi"]["text"]
assert x["outcome"] == result["assessment"]["outcome"]["title"]
prof = {r["label"]: r for r in case["financial_profile"]}
assert prof["Income"]["text"] == "₹40,000/month" and prof["Income"]["status"] == "Confirmed by applicant"
assert case["request"] == {"loan": "₹3,00,000", "tenure": "36 months"}
assert {r["label"]: r["text"] for r in case["applicant"]}["Work"] == "kirana shop"
assert cv.SESSIONS[sid]["result"] == result, "the handoff never changes the result"
print("ok 4 - handoff keeps the confirmed profile and the assessment")

kinds = {f["kind"] for f in case["flags"]}
assert "unverified" in kinds, kinds                       # income proof etc. are never checked
assert not kinds & {"contradiction", "excessive_debt", "missing_critical", "unconfirmed"}, kinds
# This suite runs without a Gemini key, so every turn really was read by Saathi's own parser: that is
# the one extraction flag, and it says so; there were no misunderstandings to report.
assert [f["label"] for f in case["flags"] if f["kind"] == "extraction"] ==        ["Language model unavailable"], case["flags"]
corr_event = next(e for e in case["timeline"] if e["label"] == "Correction requested")
assert corr_event["detail"] == "Caller wanted to change: income", corr_event  # no internal field names
print("ok 5 - only flags that actually exist (self-reported info), nothing invented")

assert case["notification"] == {"channel": "none", "configured": False, "delivered": False}
assert "No advisor is notified automatically" in case["prototype_note"]
blob = json.dumps(case, ensure_ascii=False).lower()
assert "advisor has been notified" not in blob and "approved" not in blob
print("ok 6 - honest: no advisor channel, so nobody is said to be notified")

# ---- 2. no corrections -> none shown ----
sid2 = new()
until_summary(sid2, {**ANSWERS, "monthly_income": "40,000"})
say(sid2, "yes", "confirm")
case2 = handoff(sid2)
assert case2["corrections"] == [], case2["corrections"]
assert not any(e["label"].endswith("changed") for e in case2["timeline"])
assert "Simulation performed" not in [e["label"] for e in case2["timeline"]]
print("ok 7 - no correction data is invented")

# ---- 3. contradictory income, resolved by the caller ----
sid3 = new()
for text in ("3 lakh", "3 years", "40,000", "kirana shop"):
    say(sid3, text)
say(sid3, "Actually I earn 60,000")
say(sid3, "the second one")
case3 = handoff(sid3)
flag = next(f for f in case3["flags"] if f["kind"] == "contradiction")
assert "₹40,000/month and ₹60,000/month" in flag["detail"], flag
assert [(x["before"], x["after"], x["how"]) for x in case3["corrections"]] == \
       [("₹40,000/month", "₹60,000/month", "Clarified during the questions")]
assert case3["assessment"] is None, "not assessed before confirmation"
assert any(f["kind"] == "unconfirmed" for f in case3["flags"])
assert "Profile confirmed" not in [e["label"] for e in case3["timeline"]]
print("ok 8 - contradiction flagged, the caller's choice shown, unconfirmed answers flagged")

# ---- 4. excessive debt and missing information ----
sid4 = new()
until_summary(sid4, {**ANSWERS, "monthly_income": "40,000", "other_loans_outstanding": "36 lakh"})
say(sid4, "yes", "confirm")
case4 = handoff(sid4)
assert any(f["kind"] == "excessive_debt" and "2 years of income" in f["detail"] for f in case4["flags"])
sid5 = new()
say(sid5, "I don't know")
case5 = handoff(sid5)
missing = next(f for f in case5["flags"] if f["kind"] == "missing_critical")
assert "requested loan" in missing["detail"] and "income" in missing["detail"]
print("ok 9 - excessive debt and missing critical information come from the rules and the profile")

# ---- 5. a saved pre-screen (no call record): no invented timeline or transcript ----
pid = c.post("/v1/prescreen", headers=H, json={"monthly_income": "40000", "loan_amount": "3 lakh"}).json()["prescreen_id"]
r = c.post("/v1/advisor-cases", headers=H, json={"prescreen_id": pid}).json()
assert [e["label"] for e in r["timeline"]] == ["Advisor requested"]
assert "transcript" not in r and "corrections" not in r
print("ok 10 - a pre-screen without a call gets no made-up conversation")

# ---- 6. stored and listed ----
listed = {x["id"]: x for x in c.get("/v1/advisor-cases", headers=H).json()["cases"]}
assert listed[case["id"]]["corrections"] == case["corrections"] and listed[case["id"]]["timeline"]
assert advisor.result_flags(None, {}) == []
print("ok 11 - the workspace is saved with the case")

print("All handoff tests passed.")
