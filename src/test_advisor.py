"""Advisor handoff, "Listen to this result", code-mixed speech and error recovery.

    python -I -X utf8 -W ignore src/test_advisor.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "advisor-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""

os.environ["SHORT_CALL"] = "0"  # this suite scripts the full, fixed question order
import converse as cv  # noqa: E402
import server  # noqa: E402
cv_switch = __import__("converse")
cv_switch.ASK_HISTORY = False  # this suite's fixed answer sequences predate the history questions;
                               # src/test_history.py tests them
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(server.app)
H = {"X-Webhook-Key": "advisor-test-key"}


def start(lang="en"):
    return c.post("/v1/converse", headers=H, json={"language": lang}).json()["session_id"]


def say(sid, text="", action=""):
    r = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text, "action": action})
    assert r.status_code == 200, r.text
    return r.json()


def profile(sid):
    for text in ("3 lakh", "3 years", "40,000", "yes", "kirana shop", "10 years", "1", "8,000", "1.5 lakh",
                 "own house", "35"):
        t = say(sid, text)
    return t


# ---- advisor case from a finished web call, with a simulation and a question ----
sid = start()
t = profile(sid)
say(sid, "My income is wrong")
say(sid, "40,000")
t = say(sid, "yes", "confirm")
assert t["state"] == cv.RESULT
t = say(sid, "what if I take 2.5 lakh for 4 years")
assert t["state"] == cv.WHAT_IF
t = say(sid, "Can I get a longer tenure? I want to talk to a loan advisor")
assert t["state"] == cv.HANDOFF and t["advisor_case"], t
assert "no advisor is notified automatically" in t["reply"] and t["advisor_case"] in t["reply"], t["reply"]
cases = c.get("/v1/advisor-cases", headers=H).json()["cases"]
case = next(x for x in cases if x["id"] == t["advisor_case"])
profile_rows = {r["label"]: r for r in case["financial_profile"]}
assert profile_rows["Income"]["text"] == "₹40,000/month" and profile_rows["Income"]["status"] == "Confirmed by applicant"
assert profile_rows["Existing EMI"]["text"] == "₹8,000/month"
assert profile_rows["Existing debt"]["text"] == "₹1,50,000"
assert case["request"] == {"loan": "₹3,00,000", "tenure": "36 months"}
assert case["assessment"]["risk"] in ("Low", "Medium", "High")
assert case["assessment"]["affordable_emi"] == "₹12,000/month"
assert case["simulation"]["loan"] == "₹2,50,000" and case["simulation"]["tenure"] == "48 months"
assert "longer tenure" in case["question"]
convo = " ".join(case["conversation"])
assert "confirmed the profile" in convo and "monthly income" in convo and "Tried a simulation" in convo, convo
assert any("Caller:" in line for line in case["recent"])
assert "No advisor is notified automatically" in case["prototype_note"]
assert {i["label"] for i in case["completeness"]} >= {"Monthly income", "Credit bureau report unavailable"}
print("ok - voice handoff saves a full advisor case (profile, request, risk, affordability, simulation, question)")

# ---- from the dashboard button, for a saved pre-screen, with the on-screen simulation ----
pid = t["prescreen_id"]
r = c.post("/v1/advisor-cases", headers=H, json={"prescreen_id": pid, "question": "Can I get a longer tenure?",
                                                 "loan_amount": 250000, "tenure_months": 48})
assert r.status_code == 200, r.text
case = r.json()
assert case["simulation"]["emi"].startswith("₹") and case["question"] == "Can I get a longer tenure?"
assert c.post("/v1/advisor-cases", headers=H, json={"prescreen_id": pid, "loan_amount": 5}).status_code == 422
assert c.post("/v1/advisor-cases", headers=H, json={}).status_code == 400
assert c.post("/v1/advisor-cases", json={"prescreen_id": pid}).status_code == 401
print("ok - dashboard button: case with the current simulation, recomputed on the server")

# ---- handoff before any assessment: partial profile, nothing invented ----
sid = start()
say(sid, "1 lakh")
t = say(sid, "", "handoff")
case = next(x for x in c.get("/v1/advisor-cases", headers=H).json()["cases"] if x["id"] == t["advisor_case"])
assert case["assessment"] is None and case["simulation"] is None
assert case["request"]["loan"] == "₹1,00,000"
assert any(r["status"] == "Not asked yet" for r in case["financial_profile"])
assert "not confirmed yet" in " ".join(case["conversation"])
print("ok - early handoff: partial profile, no assessment invented")

# ---- a failing case store never strands the caller ----
sid = start()
say(sid, "1 lakh")
real = server.save_case
server.save_case = lambda case: (_ for _ in ()).throw(RuntimeError("db down"))
t = say(sid, "talk to a real person")
server.save_case = real
assert t["state"] == cv.COLLECTING and "couldn't save" in t["reply"], t
print("ok - advisor case failure is explained, the call carries on")

# ---- listen to this result ----
sid = start()
profile(sid)
t = say(sid, "yes", "confirm")
spoken = t["result"]["assessment"]["spoken"]
assert spoken["en"].startswith("Within your estimated repayment capacity.") and "₹12,000 a month" in spoken["en"]
assert "not a lender's decision" in spoken["en"] and "approved" not in spoken["en"].lower()
assert "anumaan" in spoken["hi"]
print("ok - concise spoken summary in English and Hinglish")

# ---- code-mixed speech through the scripted parser (no Gemini) ----
sid = start("hi")
t = say(sid, "mujhe 3 lakh ka loan chahiye")
assert t["answers"]["loan_amount"] == 300000, t
t = say(sid, "EMI kam rakhni hai")
assert "tenure_months" in t["skipped"] and "kam EMI" in t["reply"] and t["asking"] == "monthly_income", t
t = say(sid, "meri salary 40 hazaar hai")
assert t["answers"]["monthly_income"] == 40000, t
t = say(sid, "kirana ki dukaan")
t = say(sid, "das saal se")
t = say(sid, "haan ek loan hai")
t = say(sid, "aath hazaar ki kist")
t = say(sid, "dedh lakh baaki hai")
assert t["answers"]["other_loans_emi"] == 8000 and t["answers"]["other_loans_outstanding"] == 150000, t
t = say(sid, "kiraye pe")
t = say(sid, "35 saal")
assert t["state"] == cv.VERIFYING and t["reply"].startswith("Maine yeh samjha"), t
t = say(sid, "haan sahi hai")
t = say(sid, "EMI kam rakhni hai")
assert t["state"] == cv.WHAT_IF and t["simulation"]["simulation"]["emi"]["value"] < t["result"]["assessment"]["requested_emi"]["value"]
print("ok - 'mujhe 3 lakh ka loan chahiye', 'meri salary 40 hazaar hai', 'EMI kam rakhni hai'")

# ---- error recovery ----
sid = start()
t = say(sid, "hmm")
assert t["reply"].startswith("Sorry, I couldn't understand that.") and "requested loan" in t["reply"], t
say(sid, "3 lakh")
say(sid, "3 years")
t = say(sid, "blah")
assert "Could you repeat your monthly income?" in t["reply"], t["reply"]
real_turn = cv.turn


async def broken(*a, **k):
    raise RuntimeError("Traceback: something deep failed")

cv.turn = broken
t = say(sid, "40,000")
cv.turn = real_turn
assert "something went wrong on my side" in t["reply"] and "Traceback" not in t["reply"], t
t = say(sid, "40,000")
assert t["answers"]["monthly_income"] == 40000, "the call continues after a server glitch"
r = c.post("/v1/stt", headers=H, files={"audio_file": ("a.wav", b"", "audio/wav")})
assert r.status_code in (400, 503) and "Traceback" not in r.text
print("ok - clear recovery: repeat prompts, server glitches, no stack traces")

# Recovery menu: "save my summary" goes to an advisor.
sid = start()
for _ in range(cv.RECOVER_AFTER):
    t = say(sid, "pata nahi")
assert t["state"] == cv.RECOVERY and "loan advisor" in t["reply"], t
t = say(sid, "please save my summary")
assert t["state"] == cv.HANDOFF and t["advisor_case"], t
print("ok - recovery offers the advisor")

print("All advisor, voice and recovery tests passed.")
