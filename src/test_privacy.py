"""Data protection: answers are stored only with the caller's yes, and deleted after RETENTION_DAYS.

    python -I -X utf8 -W ignore src/test_privacy.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.update({"WEBHOOK_KEY": "privacy-test-key", "DB_PATH": str(Path(tempfile.mkdtemp()) / "test.db"),
                   "DATABASE_URL": "", "GEMINI_API_KEY": "", "RETENTION_DAYS": "30"})

import converse as cv  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

cv.ASK_HISTORY = False
c = TestClient(server.app)
H = {"X-Webhook-Key": "privacy-test-key"}
ANSWERS = ("3 lakh", "3 years", "40,000", "kirana shop", "10 years", "1", "8,000", "1.5 lakh", "own house", "35")


def rows(table, key=None):
    if key:
        return server._run(f"SELECT id FROM {table} WHERE id = ?", (key,), fetch=True)
    return server._run(f"SELECT id FROM {table}", fetch=True)


def call(consent, lang="en"):
    t = c.post("/v1/converse", headers=H, json={"language": lang, "consent": consent}).json()
    return t["session_id"], t


def say(sid, text="", action=""):
    return c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text, "action": action}).json()


def to_result(sid):
    for a in ANSWERS:
        say(sid, a)
    return say(sid, "yes", "confirm")


# ---- no consent: everything works, nothing is stored ----
sid, t = call(False)
assert "aren't being saved" in t["reply"] and t["consent"] is False
r = to_result(sid)
assert r["state"] == cv.RESULT and r["result"]["assessment"] and r["prescreen_id"]
assert not rows("sessions", sid) and not rows("prescreens", r["prescreen_id"]), "no consent, nothing stored"
assert all(p["id"] != r["prescreen_id"] for p in c.get("/v1/applications", headers=H).json()["prescreens"])
sim = c.post("/v1/simulate", headers=H, json={"prescreen_id": r["prescreen_id"], "loan_amount": 200000}).json()
assert sim["simulation"]["loan_amount"]["value"] == 200000, "what-if still works, from memory"
print("ok - without consent the whole call works and nothing reaches the database")

# ---- consent: stored, with the retention period said out loud ----
sid2, t = call(True)
assert "kept for 30 days" in t["reply"] and t["consent"] is True
r2 = to_result(sid2)
assert rows("sessions", sid2) and rows("prescreens", r2["prescreen_id"])
print("ok - with consent the call is stored and the caller hears for how long")

# ---- withdrawing consent deletes what was stored; agreeing later stores it ----
t = say(sid2, "please don't save my answers")
assert "won't be saved" in t["reply"] and t["consent"] is False
assert not rows("sessions", sid2) and not rows("prescreens", r2["prescreen_id"]), "withdrawal deletes"
sid3, _ = call(False, "hi")
t = say(sid3, "3 lakh")
t = say(sid3, "haan save kar do")
assert "30 din" in t["reply"] and t["consent"] is True and rows("sessions", sid3)
assert t["answers"]["loan_amount"] == 300000 and t["asking"] == "tenure_months", "the call carries on"
print("ok - 'save mat karo' deletes what was stored; 'save kar do' starts storing; the call carries on")

# ---- asking for an advisor is asking us to keep the summary ----
sid4, _ = call(False)
say(sid4, "3 lakh")
t = say(sid4, "", "handoff")
assert t["advisor_case"] and t["consent"] is True and cv.SESSIONS[sid4]["consent_via"] == "advisor request"
print("ok - an advisor request keeps the summary (the caller asked for it)")

# ---- phone pre-screens honour the agent's consent answer ----
p = c.post("/v1/prescreen", headers=H, json={"monthly_income": "40000", "loan_amount": "1 lakh", "consent": "nahi"}).json()
assert p["decision"] and not rows("prescreens", p["prescreen_id"])
p2 = c.post("/v1/prescreen", headers=H, json={"monthly_income": "40000", "loan_amount": "1 lakh", "consent": "haan"}).json()
assert rows("prescreens", p2["prescreen_id"])
print("ok - phone pre-screens are stored only when the caller agreed")

# ---- retention: older than RETENTION_DAYS is deleted, newer is kept ----
old = time.time() - 31 * 86400
for table in server.RETAINED_TABLES:
    cols = {"prescreens": "(?,?,?,?,?)"}.get(table, "(?,?,?)")
    vals = ("old-" + table, old) + (("{}",) * (3 if table == "prescreens" else 1))
    server._run(f"INSERT INTO {table} VALUES {cols}", vals)
gone = c.post("/v1/admin/purge", headers=H).json()
assert gone["retention_days"] == 30 and all(gone["deleted"][t] == 1 for t in server.RETAINED_TABLES), gone
assert not any(rows(t, "old-" + t) for t in server.RETAINED_TABLES)
assert rows("prescreens", p2["prescreen_id"]), "recent data stays"
assert c.post("/v1/admin/purge").status_code == 401
print("ok - data older than 30 days is deleted from every table; recent data stays")

print("All privacy tests passed.")
