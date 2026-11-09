"""Financial profile confirmation: nothing is assessed until the caller confirms what Saathi understood.

    python -I -X utf8 -W ignore src/test_confirmation.py

Extraction is not confirmation, and parsed values are not confirmation: only the caller's "yes" to
"Here's what I understood" moves the call to CONFIRMED, and only then can the assessment run.
Covers: normal confirmation, single-field correction, ambiguous amount, contradictory income, missing
required field, assessment attempted before confirmation, lakh/crore values, invalid financial values.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "confirm-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""

import converse as cv  # noqa: E402
import normalize as norm  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(server.app)
H = {"X-Webhook-Key": "confirm-test-key"}
passed = []

# What the caller says when asked each field (the call asks in its own order).
ANSWERS = {"loan_amount": "3 lakh", "tenure_months": "3 years", "monthly_income": "40,000",
           "work": "kirana shop", "years_in_work": "10 years", "other_loans_count": "1",
           "other_loans_emi": "8,000", "other_loans_outstanding": "1.5 lakh", "past_loans": "2",
           "ever_refused": "no", "ever_late": "no", "late_last_year": "no", "late_over_month": "no",
           "overdue_now": "no", "housing": "own house", "age": "35"}


def ok(name):
    passed.append(name)
    print("ok -", name)


def new(lang="en"):
    return c.post("/v1/converse", headers=H, json={"language": lang}).json()["session_id"]


def say(sid, text="", action=""):
    r = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text, "action": action})
    assert r.status_code == 200, r.text
    return r.json()


def answer_until_summary(sid, answers=ANSWERS, limit=30):
    t = None
    for _ in range(limit):
        t = t or say(sid, answers["loan_amount"])
        if t["state"] != cv.COLLECTING:
            return t
        assert not t["pending"], f"unexpected read-back: {t['pending']}"
        t = say(sid, answers[t["asking"]])
    raise AssertionError(f"never reached the summary: {t['state']} asking {t['asking']}")


def no_result(t):
    assert t["result"] is None and t["prescreen_id"] is None, "assessed without confirmation"


# ---- 1. normal confirmation ----
sid = new()
t = answer_until_summary(sid)
assert t["state"] == cv.VERIFYING and t["asking"] == "confirm", t
assert t["reply"].startswith("Here's what I understood") and "₹3,00,000" in t["reply"] and "kirana shop" in t["reply"]
card = {r["field"]: r for r in t["summary"] if r["card"]}
assert set(card) >= {"loan_amount", "tenure_months", "monthly_income", "other_loans_emi",
                     "other_loans_outstanding", "work"}, set(card)
assert card["monthly_income"]["text"] == "₹40,000" and card["other_loans_outstanding"]["text"] == "₹1,50,000"
assert all(r["status"] == "validated" for r in t["summary"]), "parsed values are not confirmed values"
assert [b["action"] for b in t["buttons"]] == ["confirm", "change"]
no_result(t)
t = say(sid, "Yes, that's correct", "confirm")
assert t["state"] == cv.RESULT and t["result"]["assessment"], t["state"]
assert all(f["status"] == "confirmed" for f in t["fields"].values())
trail = t["trail"]
assert trail.index("VERIFYING_INFORMATION -> CONFIRMED") < trail.index("CONFIRMED -> ASSESSING_RISK")
ok("normal confirmation: card with income source, then confirm, then assess")

# ---- 2. single-field correction ----
sid = new()
t = answer_until_summary(sid)
before = dict(t["answers"])
history_len = len(cv.SESSIONS[sid]["history"])
t = say(sid, "My income is wrong")
assert t["state"] == cv.CORRECTION and t["correcting"] == "monthly_income", t
no_result(t)
t = say(sid, "45,000")
assert t["state"] == cv.VERIFYING and t["answers"]["monthly_income"] == 45000, t
assert {k: v for k, v in t["answers"].items() if k != "monthly_income"} == \
       {k: v for k, v in before.items() if k != "monthly_income"}, "only that field changes"
assert t["fields"]["monthly_income"]["status"] == "validated", "a corrected value needs confirming again"
assert len(cv.SESSIONS[sid]["history"]) == history_len + 4, "nothing restarted"
t = say(sid, "", "correct:other_loans_emi")                 # the Change -> field button path
t = say(sid, "9,000")
assert t["answers"]["other_loans_emi"] == 9000 and t["answers"]["monthly_income"] == 45000
t = say(sid, "yes", "confirm")
assert t["state"] == cv.RESULT
ok("single-field correction: by voice and by button, only that field changes, then re-confirm")

# ---- 3. ambiguous amount ----
sid = new()
say(sid, "3 lakh")
say(sid, "3 years")
t = say(sid, "40")                                          # 40 rupees or 40 thousand?
assert t["pending"]["kind"] == "confirm" and "₹40,000" in t["reply"] and "monthly_income" not in t["answers"], t
assert [b["action"] for b in t["buttons"]] == ["yes", "no"]
t = say(sid, "", "no")
assert "monthly_income" not in t["answers"] and t["asking"] == "monthly_income"
t = say(sid, "40-50 hazaar")                                # a range: which one?
assert t["pending"]["kind"] == "choose" and t["pending"]["options"] == [40000, 50000], t
t = say(sid, "₹50,000", "choose:1")
assert t["answers"]["monthly_income"] == 50000
ok("ambiguous amounts are asked about, never guessed (bare number, range)")

# ---- 4. contradictory income ----
sid = new()
for text in ("3 lakh", "3 years", "40,000", "kirana shop"):
    t = say(sid, text)
t = say(sid, "Actually I earn 60,000")
assert t["pending"]["kind"] == "choose" and t["answers"]["monthly_income"] == 40000, t
assert "two different monthly income values: ₹40,000 and ₹60,000" in t["reply"], t["reply"]
assert [b["label"] for b in t["buttons"]] == ["₹40,000", "₹60,000"]
t = say(sid, "the second one")
assert t["answers"]["monthly_income"] == 60000 and t["pending"] is None
ok("contradictory income: both values shown, the caller picks")

# ---- 5. missing required field ----
sid = new()
for _ in range(cv.MAX_TRIES):
    t = say(sid, "I don't know")
assert "loan_amount" not in t["answers"] and t["state"] == cv.RECOVERY, t["state"]
no_result(t)
t = say(sid, "yes, try again")
t = say(sid, "1 lakh")
assert t["answers"]["loan_amount"] == 100000
s, _ = cv.start("en")
s["state"] = cv.CONFIRMED                                   # even forced into CONFIRMED...
try:
    cv.require_confirmed(s)
    raise AssertionError("assessed without the required fields")
except cv.NotConfirmed as e:
    assert "loan_amount" in str(e) and "monthly_income" in str(e)
ok("missing required field: no assessment, recovery asks again")

# ---- 6. assessment attempted before confirmation ----
sid = new()
t = answer_until_summary(sid)
s = cv.SESSIONS[sid]
for attempt in ("what if I take 2 lakh?", "just give me the result"):
    t = say(sid, attempt)
    assert t["state"] == cv.VERIFYING, (attempt, t["state"])
    no_result(t)
try:
    cv.require_confirmed(s)
    raise AssertionError("assessed while verifying")
except cv.NotConfirmed:
    pass
s["state"] = cv.CONFIRMED                                   # forced state, but answers never confirmed
try:
    cv.require_confirmed(s)
    raise AssertionError("assessed unconfirmed answers")
except cv.NotConfirmed as e:
    assert "unconfirmed" in str(e)
s["state"] = cv.VERIFYING
for bad in (cv.CONFIRMED, cv.ASSESSING, cv.RESULT):
    probe = {"state": cv.COLLECTING, "trail": []}
    try:
        cv.transition(probe, bad)
        raise AssertionError(f"COLLECTING -> {bad} allowed")
    except cv.InvalidTransition:
        pass
assert c.post("/v1/simulate", headers=H, json={"prescreen_id": "none", "loan_amount": 1000}).status_code == 404
ok("assessment before confirmation is impossible (conversation, gate, state machine, API)")

# ---- 7. lakh / crore values ----
for said, want in {"3 lakh": 300000, "1.5 lakh": 150000, "dedh lakh": 150000, "sava lakh": 125000,
                   "ek lakh pachas hazaar": 150000, "2 crore 50 lakh": 25_000_000, "₹1,20,000": 120000,
                   "40k": 40000, "four lakhs": 400000, "डेढ़ लाख": 150000}.items():
    assert norm.number(said) == want, (said, norm.number(said))
sid = new()
t = say(sid, "1 crore")                                     # very large: read back, not assumed
assert t["pending"]["kind"] == "confirm" and "₹1,00,00,000" in t["reply"]
t = say(sid, "yes")
assert t["answers"]["loan_amount"] == 10_000_000
ok("lakh/crore values parse correctly; very large ones are read back")

# ---- 8. invalid financial values ----
sid = new()
t = say(sid, "50 crore")
assert "too large" in t["reply"] and "loan_amount" not in t["answers"]
say(sid, "1 lakh")
t = say(sid, "50 years")
assert "3 months to 10 years" in t["reply"] and "tenure_months" not in t["answers"]
say(sid, "2 years")
t = say(sid, "-20000")
assert "can't be negative" in t["reply"] and "monthly_income" not in t["answers"]
t = say(sid, "0")
assert "at least ₹1,000" in t["reply"] and "monthly_income" not in t["answers"]
for key, raw in (("monthly_income", "-5000"), ("loan_amount", "0"), ("tenure_months", "500"),
                 ("age", "12"), ("other_loans_count", "999")):
    assert cv.valid(key, raw) is None, (key, raw)
res, _ = server.decide(server.build_applicant({"monthly_income": "20000", "loan_amount": "1 lakh",
                                               "other_loans_count": "0", "other_loans_emi": "5000"}))
assert res["decision"] == "need_more_info", "contradictory phone answers pause instead of assessing"
ok("invalid values are refused with a reason and never reach the assessment")

print(f"All {len(passed)} confirmation tests passed.")
