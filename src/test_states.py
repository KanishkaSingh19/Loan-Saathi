"""Tests the web call's state machine: confirmation, corrections, ambiguity, contradictions,
invalid values and the assessment gate. No Gemini or Gnani calls (Gemini is mocked where used).

    python -I -X utf8 -W ignore src/test_states.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "states-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""  # never write test calls into the real database
os.environ["GEMINI_API_KEY"] = ""

import converse as cv  # noqa: E402
import normalize as norm  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(server.app)
H = {"X-Webhook-Key": "states-test-key"}
passed = []


def ok(name):
    passed.append(name)
    print("ok -", name)


def new(lang="en"):
    r = c.post("/v1/converse", headers=H, json={"language": lang})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def say(sid, text="", action=""):
    r = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text, "action": action})
    assert r.status_code == 200, r.text
    return r.json()


PROFILE = [("loan_amount", "3 lakh"), ("tenure_months", "3 years"), ("monthly_income", "40,000"),
           ("work", "kirana shop"), ("years_in_work", "10 years"), ("other_loans_count", "1"),
           ("other_loans_emi", "8,000"), ("other_loans_outstanding", "1.5 lakh"),
           ("housing", "own house"), ("age", "35")]


def to_summary(lang="en", profile=PROFILE):
    sid = new(lang)
    t = None
    for field, text in profile:
        t = say(sid, text)
    assert t["state"] == cv.VERIFYING, t
    return sid, t


# ---------- parsing ----------
cases = {"₹40,000": 40000, "40k": 40000, "forty thousand": 40000, "4 lakh": 400000, "four lakhs": 400000,
         "1.5 lakh": 150000, "1 crore": 10_000_000, "2 crore 50 lakh": 25_000_000, "dedh lakh": 150000,
         "dhai lakh": 250000, "saadhe teen lakh": 350000, "1 lakh 50 hazaar": 150000,
         "ek lakh pachas hazaar": 150000, "forty five thousand": 45000, "pachas hazaar": 50000,
         "चालीस हज़ार": 40000, "५० हज़ार": 50000, "25k": 25000, "₹1,20,000": 120000, "do lakh": 200000}
for text, want in cases.items():
    assert norm.number(text) == want, (text, norm.number(text), want)
assert [a["value"] for a in norm.amounts("40-50 hazaar")] == [40000, 50000]
assert [a["value"] for a in norm.amounts("13,00,000 का 12,00,000 का")] == [1300000, 1200000]
assert norm.negative("-5000") and norm.negative("minus 5 hazaar") and not norm.negative("5-10 hazaar")
ok("lakh / crore / Hindi / Hinglish number parsing")

# ---------- state machine ----------
s, _ = cv.start("en")
assert s["state"] == cv.COLLECTING and s["trail"][0]["from"] == cv.START
for bad in (cv.RESULT, cv.CONFIRMED, cv.ASSESSING, cv.COMPLETED, cv.START):
    try:
        cv.transition(s, bad)
        raise AssertionError(f"COLLECTING -> {bad} must be refused")
    except cv.InvalidTransition:
        pass
assert s["state"] == cv.COLLECTING  # a refused move changes nothing
done = {"state": cv.COMPLETED, "trail": []}
for target in cv.STATES:
    try:
        cv.transition(done, target)
        raise AssertionError("COMPLETED is final")
    except cv.InvalidTransition:
        pass
assert all(set(v) <= set(cv.STATES) for v in cv.TRANSITIONS.values())
assert cv.CONFIRMED in cv.TRANSITIONS[cv.VERIFYING] and cv.CONFIRMED not in cv.TRANSITIONS[cv.COLLECTING]
assert cv.TRANSITIONS[cv.CONFIRMED] == {cv.ASSESSING, cv.RECOVERY}  # nothing skips the risk model
assert cv.TRANSITIONS[cv.ASSESSING] == {cv.AFFORDABILITY, cv.RECOVERY}
ok("invalid state transitions are refused")

# ---------- assessment before confirmation ----------
s, _ = cv.start("en")
try:
    cv.require_confirmed(s)
    raise AssertionError("must not assess while collecting")
except cv.NotConfirmed:
    pass
sid, t = to_summary()
s = cv.SESSIONS[sid]
assert t["result"] is None and t["prescreen_id"] is None
assert all(f["status"] == "validated" for f in t["fields"].values()), t["fields"]
try:
    cv.require_confirmed(s)
    raise AssertionError("must not assess before the caller confirms")
except cv.NotConfirmed:
    pass
s["state"] = cv.CONFIRMED  # forced into CONFIRMED with unconfirmed fields: still refused
try:
    cv.require_confirmed(s)
    raise AssertionError("unconfirmed fields must block assessment")
except cv.NotConfirmed as e:
    assert "unconfirmed" in str(e)
s["state"] = cv.VERIFYING
ok("assessment refused before confirmation")

# ---------- normal conversation + confirmation (buttons) ----------
assert [b["action"] for b in t["buttons"]] == ["confirm", "change"], t["buttons"]
assert "Here's what I understood" in t["reply"] and "₹3,00,000" in t["reply"] and "₹40,000" in t["reply"]
assert "36 months" in t["reply"] and "₹8,000" in t["reply"] and "₹1,50,000" in t["reply"]
rows = {r["field"]: r for r in t["summary"]}
assert rows["monthly_income"]["text"] == "₹40,000" and rows["other_loans_outstanding"]["critical"]
t = say(sid, "Yes, that's correct", "confirm")
assert t["state"] == cv.RESULT and t["result"]["decision"], t
assert all(f["status"] == "confirmed" for f in t["fields"].values())
trail = t["trail"]
i = trail.index("VERIFYING_INFORMATION -> CONFIRMED")
assert trail[i:i + 4] == ["VERIFYING_INFORMATION -> CONFIRMED", "CONFIRMED -> ASSESSING_RISK",
                          "ASSESSING_RISK -> CHECKING_AFFORDABILITY", "CHECKING_AFFORDABILITY -> SHOWING_RESULT"], trail
t = say(sid, "yes")
assert t["state"] == cv.NEXT_STEPS and "Aadhaar" in t["reply"], t
t = say(sid, "no thanks")
assert t["state"] == cv.COMPLETED and t["done"], t
apps = c.get("/v1/applications", headers=H).json()["prescreens"]
assert any(a["id"] == t["prescreen_id"] for a in apps)
ok("normal conversation: summary -> confirm -> risk -> affordability -> result -> next steps -> done")

# Spoken "haan" works as well as the button, in Hinglish.
sid, t = to_summary("hi")
assert t["reply"].startswith("Maine yeh samjha")
t = say(sid, "haan ji, sab sahi hai")
assert t["state"] == cv.RESULT, t
ok("spoken confirmation")

# ---------- rejection ----------
sid, t = to_summary()
before = dict(t["answers"])
t = say(sid, "no")
assert t["state"] == cv.CORRECTION and t["asking"] == "correction" and "Which detail" in t["reply"], t
assert {b["action"] for b in t["buttons"]} >= {"correct:monthly_income", "correct:loan_amount"}
t = say(sid, "Change my requested loan", "correct:loan_amount")
assert t["correcting"] == "loan_amount" and "correct requested loan" in t["reply"], t
t = say(sid, "2 lakh")
assert t["state"] == cv.VERIFYING and t["answers"]["loan_amount"] == 200000, t
assert {k: v for k, v in t["answers"].items() if k != "loan_amount"} == \
       {k: v for k, v in before.items() if k != "loan_amount"}
ok("rejecting the summary asks which field, then fixes only that one")

# ---------- correction ----------
sid, t = to_summary()
hist_len = len(cv.SESSIONS[sid]["history"])
t = say(sid, "My income is wrong.")
assert t["state"] == cv.CORRECTION and t["asking"] == "monthly_income", t
assert "correct monthly income" in t["reply"], t["reply"]
t = say(sid, "50,000")
assert t["state"] == cv.VERIFYING and t["answers"]["monthly_income"] == 50000, t
assert "changed your monthly income to ₹50,000" in t["reply"], t["reply"]
assert t["fields"]["monthly_income"]["status"] == "validated"   # changed: needs confirming again
assert t["fields"]["loan_amount"]["status"] == "validated" and t["answers"]["loan_amount"] == 300000
assert len(cv.SESSIONS[sid]["history"]) == hist_len + 4          # nothing restarted
t = say(sid, "income 45 hazaar hai, galat tha")                  # field + value in one breath
assert t["state"] == cv.VERIFYING and t["answers"]["monthly_income"] == 45000, t
t = say(sid, "EMI galat hai")
t = say(sid, "6 hazaar")
assert t["answers"]["other_loans_emi"] == 6000 and t["answers"]["monthly_income"] == 45000, t
t = say(sid, "tenure is wrong")
t = say(sid, "2 saal")
assert t["answers"]["tenure_months"] == 24, t
t = say(sid, "debt galat hai")
t = say(sid, "1 lakh")
assert t["answers"]["other_loans_outstanding"] == 100000, t
t = say(sid, "my work is wrong")
assert t["correcting"] == "work", t
t = say(sid, "auto driver")
assert t["answers"]["work"] == "auto driver" and t["state"] == cv.VERIFYING, t
ok("field-level correction of income, EMI, tenure, debt, work")

# ---------- repeated correction ----------
assert cv.SESSIONS[sid]["corrections"] == 6, cv.SESSIONS[sid]["corrections"]
t = say(sid, "income galat hai")
t = say(sid, "42,000")                                            # 7th change: a person takes over
assert t["state"] == cv.HANDOFF and t["done"] and "loan advisor" in t["reply"] and t["advisor_case"], t
ok("repeated corrections, then a handoff after too many")

# A correction that makes new questions needed goes back to collecting them.
sid, t = to_summary(profile=[p if p[0] != "other_loans_count" else ("other_loans_count", "nahi")
                             for p in PROFILE if p[0] not in ("other_loans_emi", "other_loans_outstanding")])
assert "no other loans" in t["reply"], t["reply"]
t = say(sid, "number of loans is wrong")
t = say(sid, "2")
assert t["state"] == cv.COLLECTING and t["asking"] == "other_loans_emi", t
t = say(sid, "5,000")
t = say(sid, "80 hazaar")
assert t["state"] == cv.VERIFYING and "2 other loans" in t["reply"], t
ok("correction that needs follow-up questions")

# ---------- ambiguous numbers ----------
sid = new()
say(sid, "1 lakh")
say(sid, "you decide")
t = say(sid, "40")
assert t["pending"]["kind"] == "confirm" and "only heard 40" in t["reply"] and "₹40,000" in t["reply"], t
assert "monthly_income" not in t["answers"] and [b["action"] for b in t["buttons"]] == ["yes", "no"]
t = say(sid, "", "no")
assert t["asking"] == "monthly_income" and t["pending"] is None and "monthly_income" not in t["answers"], t
t = say(sid, "fourteen thousand")                                 # -teen/-ty: read back
assert t["pending"]["kind"] == "confirm" and "I heard ₹14,000" in t["reply"], t
t = say(sid, "no, 40 thousand")                                   # a different number: check that
assert t["answers"]["monthly_income"] == 40000 and t["asking"] == "work", t
ok("ambiguous numbers are read back: bare 40, fourteen/forty")

sid = new()
t = say(sid, "40-50 hazaar")
assert t["pending"]["kind"] == "choose" and "₹40,000 and ₹50,000" in t["reply"], t
t = say(sid, "50 hazaar")
assert t["answers"]["loan_amount"] == 50000, t
say(sid, "1 saal")
t = say(sid, "salary 20 hazaar aur dukaan se 10 hazaar")
assert t["pending"]["options"] == [20000, 10000] and "total" in t["reply"], t
t = say(sid, "Both together", "choose:total")
assert t["answers"]["monthly_income"] == 30000, t
ok("two amounts in one answer: pick one or the total")

sid = new()
t = say(sid, "1 crore")                                           # far above the usual: read back
assert t["pending"]["kind"] == "confirm" and "₹1,00,00,000" in t["reply"], t
t = say(sid, "haan")
assert t["answers"]["loan_amount"] == 10_000_000, t
ok("very large amounts are read back")

# Gemini's figure must match the caller's own words, or it is read back.
server.GEMINI_API_KEY = "fake-key"
script = []


async def fake(api_key, model, system, user, schema):
    step = script.pop(0)
    if isinstance(step, Exception):
        raise step
    return step

cv._gemini = fake
sid = new("hi")
script[:] = [{"answers": [{"field": "loan_amount", "value": "500000", "quote": "50 hazaar"}],
              "asking": "tenure_months", "reply": "Kitne mahine?", "language": "hi"}]
t = say(sid, "mujhe 50 hazaar chahiye")
assert t["pending"]["kind"] == "confirm" and t["pending"]["value"] == 500000, t
t = say(sid, "nahi")
assert "loan_amount" not in t["answers"] and t["asking"] == "loan_amount", t
ok("a Gemini value that differs from the caller's words is read back")

# Gemini classifies an unclear reply to the summary; our code still decides what happens.
server.GEMINI_API_KEY = ""
sid, t = to_summary("hi")
server.GEMINI_API_KEY = "fake-key"
script[:] = [{"intent": "correct", "field": "work"}]
t = say(sid, "woh pichli baat thodi alag hai")       # no keyword our rules trust
assert t["state"] == cv.CORRECTION and t["correcting"] == "work", t
server.GEMINI_API_KEY = ""
sid, t = to_summary()
server.GEMINI_API_KEY = "fake-key"
script[:] = [RuntimeError("down")]
t = say(sid, "hmm")                                   # Gemini down: ask again, never guess
assert t["state"] == cv.VERIFYING and "say yes" in t["reply"], t
server.GEMINI_API_KEY = ""
ok("Gemini intent fallback for the summary, never deciding on its own")

# ---------- contradictions ----------
sid = new()
for text in ("3 lakh", "3 years", "40,000", "kirana shop", "10 years"):
    say(sid, text)
t = say(sid, "Actually I earn ₹60,000.")
assert t["pending"]["kind"] == "choose" and t["answers"]["monthly_income"] == 40000, t
assert "two different monthly income values: ₹40,000 and ₹60,000" in t["reply"], t["reply"]
t = say(sid, "₹60,000", "choose:1")
assert t["answers"]["monthly_income"] == 60000 and t["asking"] == "other_loans_count", t
t = say(sid, "nahi")
assert t["answers"]["other_loans_count"] == 0 and t["asking"] == "housing", t
t = say(sid, "my EMI is 5,000")                                   # but they said no loans
assert t["pending"]["kind"] == "loans_conflict" and "no other loans" in t["reply"], t
t = say(sid, "haan, ek loan hai")
assert t["answers"]["other_loans_emi"] == 5000 and "other_loans_count" not in t["answers"], t
assert t["asking"] == "other_loans_count", t
ok("contradicting income and debt answers are asked about, never overwritten")

# ---------- invalid values ----------
sid = new()
t = say(sid, "50 crore")
assert "too large" in t["reply"] and "loan_amount" not in t["answers"], t
t = say(sid, "50 hazaar")
t = say(sid, "50 saal")
assert "3 months to 10 years" in t["reply"] and "tenure_months" not in t["answers"], t
t = say(sid, "1 saal")
t = say(sid, "-20000")
assert "can't be negative" in t["reply"] and "monthly_income" not in t["answers"], t
t = say(sid, "0")
assert "at least ₹1,000" in t["reply"], t
t = say(sid, "20 hazaar")
for text in ("driver", "5 saal", "1", ):
    t = say(sid, text)
t = say(sid, "25,000")                                           # EMI above the ₹20,000 income
assert "other_loans_emi" not in t["answers"] and t["answers"]["other_loans_outstanding"] == 25000, t
assert t["asking"] == "other_loans_emi" and "EMI" in t["reply"], t
t = say(sid, "4,000")
t = say(sid, "kiraye pe")
t = say(sid, "15")
assert "18 to 100" in t["reply"] and "age" not in t["answers"], t
ok("invalid values are refused with a reason: loan, tenure, negative and zero income, EMI, age")

# Income corrected below an existing EMI is caught.
sid, t = to_summary()
t = say(sid, "income galat hai")
t = say(sid, "6,000")                                            # EMI is ₹8,000
assert "monthly_income" not in t["answers"] and "more than your income" in t["reply"], t
assert t["state"] == cv.COLLECTING and t["asking"] == "monthly_income", t
t = say(sid, "60,000")
assert t["state"] == cv.VERIFYING, t
ok("income corrected below the EMI is questioned")

# ---------- missing critical fields ----------
sid = new()
for _ in range(cv.MAX_TRIES):
    t = say(sid, "pata nahi")
assert t["state"] == cv.RECOVERY and "loan_amount" not in t["answers"], t
t = say(sid, "haan, phir se")
assert t["state"] == cv.COLLECTING and t["asking"] == "loan_amount", t
t = say(sid, "50 hazaar")
assert t["answers"]["loan_amount"] == 50000, t
ok("a missing required field leads to error recovery, never to an assessment")

# The assessment itself failing goes to recovery, then a retry re-shows the summary.
sid, t = to_summary()
real = server.decide
server.decide = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model file missing"))
t = say(sid, "yes")
assert t["state"] == cv.RECOVERY and t["result"] is None and "went wrong" in t["reply"], t
server.decide = real
t = say(sid, "yes")
assert t["state"] == cv.VERIFYING, t
t = say(sid, "yes")
assert t["state"] == cv.RESULT, t
ok("assessment failure -> error recovery -> retry")

# ---------- human handoff ----------
sid = new()
say(sid, "1 lakh")
t = say(sid, "I want to talk to a real person")
assert t["state"] == cv.HANDOFF and t["done"] and "loan advisor" in t["reply"] and t["advisor_case"], t
t = say(sid, "hello?")
assert cv.SESSIONS[sid]["state"] == cv.COMPLETED
ok("human handoff from any point")

# ---------- regressions found running the live system (Part 5 QA) ----------
# Gnani Prisma writes "one and a half lakh" as "one and a 1/2 lakh".
for text, want in {"one and a 1/2 lakh": 150000, "one and a half lakh": 150000, "1 1/2 lakh": 150000,
                   "two and a half lakh": 250000, "half lakh": 50000, "aadha lakh": 50000}.items():
    assert norm.number(text) == want, (text, norm.number(text))
# The tenure's number is not a second amount: no "₹6,00,000 or ₹2?" read-back.
assert cv.check({"literal_ok": set()}, "loan_amount", "600000", "6 lakh for 2 years") == ("ok", 600000)
assert cv.check({"literal_ok": set()}, "other_loans_emi", "9000", "4 loans with 9,000 EMI") == ("ok", 9000)
# Language is decided by our code from the caller's words: "3 lakh" doesn't flip an English call.
assert cv._language("3 lakh", "hi", "en") == "en" and cv._language("35", "en", "hi") == "hi"
assert cv._language("mujhe 3 lakh ka loan chahiye", "en", "en") == "hi"
assert cv._language("I want 3 lakh for 3 years", "hi", "hi") == "en"
sid = new("en")
t = say(sid, "3 lakh")
assert t["language"] == "en" and t["reply"].startswith("Over how many"), t
st = {"literal_ok": set(), "claims": {"monthly_income": 40000}}
assert cv.check(st, "loan_amount", "300000", "I want 3 lakh for 3 years, I earn 40,000") == ("ok", 300000)
assert cv.check({"literal_ok": set()}, "loan_amount", "300000", "3 lakh, I earn 40,000")[0] == "choose"
sid = new("en")
say(sid, "3 lakh")
say(sid, "3 years")
t = say(sid, "40,000")
t = say(sid, "my income is wrong")                                # before the summary
assert "monthly_income" not in t["answers"] and t["asking"] == "monthly_income", t
assert "correct monthly income" in t["reply"], t["reply"]
t = say(sid, "45,000")
assert t["answers"]["monthly_income"] == 45000 and t["asking"] == "work", t
# Gemini timed out on a long first sentence: the scripted parser must still get every field.
sid = new("en")
t = say(sid, "I need 1 lakh for 2 years, I earn 40,000, I have 4 loans with 9,000 EMI and 50 lakh still owed")
assert t["answers"] == {"loan_amount": 100000, "tenure_months": 24, "monthly_income": 40000,
                        "other_loans_emi": 9000, "other_loans_count": 4, "other_loans_outstanding": 5000000}, t
assert t["asking"] == "work", t
sid = new("en")
t = say(sid, "i earn ₹40,000 per month i already pay 8,000 emi i have around one and a 1/2 lakh debt "
             "i want 3,00,000 loan for three years")                      # Prisma's real transcript
assert t["answers"] == {"monthly_income": 40000, "other_loans_emi": 8000, "other_loans_outstanding": 150000,
                        "loan_amount": 300000, "tenure_months": 36}, t
# A what-if at the summary never changes the request.
sid, t = to_summary()
t = say(sid, "what if I take 2 lakh?")
assert t["state"] == cv.VERIFYING and t["answers"]["loan_amount"] == 300000 and "after the assessment" in t["reply"]
# A retry after error recovery asks plainly.
sid = new()
for _ in range(cv.MAX_TRIES):
    say(sid, "pata nahi")
t = say(sid, "yes, try again")
assert t["state"] == cv.COLLECTING and not t["reply"].startswith("Sorry"), t["reply"]
ok("live-run regressions: spoken halves, tenure numbers, language drift, wide quotes, early corrections, "
   "multi-field fallback, what-if at the summary, plain retry")

asyncio.run(asyncio.sleep(0))
print(f"All {len(passed)} state machine tests passed.")
