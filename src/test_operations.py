"""Persistent calls, advisor notifications and case workflow, and the user-study kit.

    python -I -X utf8 -W ignore src/test_operations.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "ops-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["ADVISOR_WEBHOOK_URL"] = ""

os.environ["SHORT_CALL"] = "0"  # this suite scripts the full, fixed question order
import converse as cv  # noqa: E402
import server  # noqa: E402
cv_switch = __import__("converse")
cv_switch.ASK_HISTORY = False  # this suite's fixed answer sequences predate the history questions;
                               # src/test_history.py tests them
import study  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(server.app)
H = {"X-Webhook-Key": "ops-test-key"}


def start(**kw):
    r = c.post("/v1/converse", headers=H, json={"language": "en", **kw})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def say(sid, text="", action=""):
    r = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text, "action": action})
    assert r.status_code == 200, r.text
    return r.json()


# ---- calls survive a restart ----
sid = start()
say(sid, "3 lakh")
say(sid, "3 years")
t = say(sid, "40")                                   # a read-back is pending
assert t["pending"]["kind"] == "confirm"
cv.SESSIONS.clear()                                  # the server restarted: memory is gone
t = say(sid, "yes")
assert t["answers"] == {"loan_amount": 300000, "tenure_months": 36, "monthly_income": 40000}, t
assert t["asking"] == "work" and t["trail"][0] == "START -> COLLECTING_PROFILE"
cv.SESSIONS.clear()
for text in ("kirana shop", "10 years", "1", "8,000", "1.5 lakh", "own house", "35"):
    t = say(sid, text)
    cv.SESSIONS.clear()                              # restart after every turn
assert t["state"] == cv.VERIFYING, t
t = say(sid, "yes", "confirm")
assert t["state"] == cv.RESULT and t["result"]["assessment"], t
row = server._run("SELECT ts FROM sessions WHERE id = ?", (sid,), fetch=True)
server._run("UPDATE sessions SET ts = ? WHERE id = ?", (row[0][0] - 3 * 3600, sid))
cv.SESSIONS.clear()
assert c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "hi"}).status_code == 404
print("ok - calls survive restarts mid-call (even mid read-back); stale calls expire after 2 hours")

# ---- advisor notifications: only claimed when delivered ----
sent = []


class Resp:
    def __init__(self, code):
        self.status_code, self.is_success = code, 200 <= code < 300


def fake_post(url, json=None, timeout=None):
    sent.append((url, json))
    return Resp(200)

real_post = server.httpx.post
server.httpx.post = fake_post

sid = start()
say(sid, "1 lakh")
t = say(sid, "", "handoff")                          # no webhook configured
assert "no advisor is notified automatically" in t["reply"] and not sent, t["reply"]
case = c.get("/v1/advisor-cases", headers=H).json()["cases"][0]
assert case["notification"] == {"channel": "none", "configured": False, "delivered": False}

server.ADVISOR_WEBHOOK_URL = "https://hooks.example.test/advisor"
sid = start()
say(sid, "1 lakh")
t = say(sid, "I want to talk to a loan advisor about a longer tenure")
assert "notified a loan advisor" in t["reply"], t["reply"]
url, payload = sent[-1]
assert url == "https://hooks.example.test/advisor" and t["advisor_case"] in payload["text"]
assert "₹" in payload["headline"] and "monthly_income" not in str(payload)  # summary only, no full profile
case = next(x for x in c.get("/v1/advisor-cases", headers=H).json()["cases"] if x["id"] == t["advisor_case"])
assert case["notification"]["delivered"] and case["prototype_note"].startswith("A loan advisor was notified")

server.httpx.post = lambda *a, **k: Resp(500)
sid = start()
say(sid, "1 lakh")
t = say(sid, "", "handoff")
assert "no advisor is notified" in t["reply"] and "notified a loan advisor" not in t["reply"], t["reply"]


def boom(*a, **k):
    raise server.httpx.ConnectTimeout("down")

server.httpx.post = boom
r = c.post("/v1/advisor-cases", headers=H, json={"session_id": start(), "question": "?"})
assert r.status_code == 200 and r.json()["notification"]["delivered"] is False
server.httpx.post = real_post
server.ADVISOR_WEBHOOK_URL = ""
print("ok - advisor notified only when the webhook confirms; failures are saved and said honestly")

# ---- advisor case workflow ----
cid = case["id"]
patch = lambda body: c.patch(f"/v1/advisor-cases/{cid}", headers=H, json=body)  # noqa: E731
assert patch({"status": "in_review"}).status_code == 422                    # name required
r = patch({"status": "in_review", "advisor": "Priya", "note": "Calling tomorrow"})
assert r.status_code == 200 and r.json()["status"] == "in_review" and r.json()["advisor"] == "Priya"
r = patch({"status": "closed", "advisor": "Priya", "note": "Offered 36 months via branch"})
assert r.json()["status"] == "closed"
assert patch({"status": "in_review", "advisor": "Priya"}).status_code == 409  # reopen first
assert patch({"status": "open", "advisor": "Priya", "note": "Applicant called back"}).json()["status"] == "open"
events = r.json()["events"]
assert [e["status"] for e in events] == ["open", "in_review", "closed"] and events[1]["by"] == "Priya"
assert patch({"status": "approved", "advisor": "x"}).status_code == 422
assert c.patch("/v1/advisor-cases/nope", headers=H, json={"status": "open"}).status_code == 404
assert c.patch(f"/v1/advisor-cases/{cid}", json={"status": "open"}).status_code == 401
print("ok - cases go open -> in review -> closed (and reopen), with names, notes and history")

# ---- user study ----
personas = c.get("/v1/study/personas", headers=H).json()["personas"]
assert len(personas) == len(study.PERSONAS) >= 10 and "truth" not in personas[0]
assert c.post("/v1/converse", headers=H, json={"language": "en", "persona": "P99"}).status_code == 422
assert c.post("/v1/converse", headers=H, json={"language": "en", "persona": "P03",
                                               "participant": "bad name!"}).status_code == 422

cv.ASK_HISTORY = True  # a study call asks everything on the persona card, history included
sid = start(persona="P03", participant="T01")
t = say(sid, "I want 3 lakh for 3 years, my salary is 40,000")
for text in ("office clerk", "8 years", "one loan", "8,000", "1.5 lakh", "3", "no", "yes", "no", "no", "no",
             "with family", "29"):
    t = say(sid, text)
t = say(sid, "yes", "confirm")
r = c.post("/v1/study/survey", headers=H, json={"session_id": sid, "ease": 4, "understood": "yes", "trust": 4,
                                                "comment": "clear", "stt_failures": 1})
assert r.status_code == 200, r.text
m = r.json()["metrics"]
assert m["persona"] == "P03" and m["participant"] == "T01" and m["reached_result"]
assert m["final_accuracy"] == 1.0 and m["first_pass_accuracy"] == 1.0, m
assert m["seconds_to_result"] is not None and m["seconds_to_summary"] <= m["seconds_to_result"]
assert m["caller_turns"] == 15 and m["corrections"] == 0

sid = start(persona="P09", participant="T02")                                # the correction persona
for text in ("60,000 for 1 year", "20,000", "delivery rider", "2 years", "no", "none", "no", "with family", "24"):
    t = say(sid, text)
say(sid, "my income is wrong")
t = say(sid, "24,000")
t = say(sid, "yes", "confirm")
m = server.study.metrics(cv.SESSIONS[sid])
assert m["wrong_first_pass"] == ["monthly_income"] and m["final_accuracy"] == 1.0 and m["corrections"] == 1, m

sid = start(persona="P01", participant="SYN", synthetic=True)
say(sid, "50 hazaar")
server._store_study(cv.SESSIONS[sid])
assert c.post("/v1/study/survey", headers=H, json={"session_id": start(), "ease": 3, "understood": "yes",
                                                   "trust": 3}).status_code == 404     # not a study call
assert c.post("/v1/study/survey", headers=H, json={"session_id": sid, "ease": 9, "understood": "yes",
                                                   "trust": 3}).status_code == 422

rep = c.get("/v1/study/report", headers=H).json()
p = rep["participants"]
assert p["calls"] == 2 and p["reached_result"] == 2 and p["surveys"] == 1 and p["ease_1to5"] == 4
assert p["field_first_pass_accuracy"]["monthly_income"] == 0.5 and p["corrections_per_call"] == 0.5
assert rep["synthetic_dry_runs"]["calls"] == 1 and "not user results" in rep["note"]
csv_text = c.get("/v1/study/report.csv", headers=H).text
assert csv_text.splitlines()[0].startswith("session_id,participant,persona") and "T01" in csv_text
assert c.get("/v1/study/report", headers={}).status_code == 401
print("ok - study: persona ground truth, first-pass and final accuracy, timings, survey, report, CSV")

print("All operations tests passed.")
