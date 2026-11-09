"""The product pipeline: LISTEN -> EXTRACT -> VALIDATE -> CONFIRM -> ASSESS -> EXPLAIN -> SIMULATE -> HANDOFF.

    python -I -X utf8 -W ignore src/test_stages.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "stages-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""

import converse as cv  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(server.app)
H = {"X-Webhook-Key": "stages-test-key"}
assert cv.STAGES == ("LISTEN", "EXTRACT", "VALIDATE", "CONFIRM", "ASSESS", "EXPLAIN", "SIMULATE", "HANDOFF")
assert set(cv.STAGE_OF) | {cv.RECOVERY, cv.COMPLETED} == set(cv.STATES), "every state has a stage"
assert all(v in cv.STAGES for v in cv.STAGE_OF.values())


def say(sid, text="", action=""):
    return c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text, "action": action}).json()


seen = []
t = c.post("/v1/converse", headers=H, json={"language": "en"}).json()
sid = t["session_id"]
seen.append(t["stage"])                                              # EXTRACT: first question asked
for text in ("3 lakh", "3 years"):
    t = say(sid, text)
t = say(sid, "40")                                                   # read-back waiting
assert t["stage"] == "VALIDATE", t["stage"]
seen.append(t["stage"])
t = say(sid, "yes")
assert t["stage"] == "EXTRACT"
for text in ("kirana shop", "10 years", "1", "8,000", "1.5 lakh", "4", "no", "no", "no", "own house", "35"):
    t = say(sid, text)
assert t["stage"] == "CONFIRM", (t["stage"], t["asking"])
seen.append(t["stage"])
t = say(sid, "my income is wrong")
assert t["stage"] == "VALIDATE"
t = say(sid, "40,000")
assert t["stage"] == "CONFIRM"
t = say(sid, "yes", "confirm")
assert t["stage"] == "EXPLAIN" and any("ASSESSING_RISK" in x for x in t["trail"]), t["stage"]
seen += ["ASSESS", t["stage"]]                                       # ASSESS runs inside the confirm turn
t = say(sid, "what if I take 2 lakh?")
assert t["stage"] == "SIMULATE"
seen.append(t["stage"])
t = say(sid, "next steps")
assert t["stage"] == "HANDOFF"
seen.append(t["stage"])
t = say(sid, "thanks")
assert t["state"] == cv.COMPLETED and t["stage"] == "HANDOFF"
assert seen == ["EXTRACT", "VALIDATE", "CONFIRM", "ASSESS", "EXPLAIN", "SIMULATE", "HANDOFF"], seen
print("ok - a full call walks EXTRACT -> VALIDATE -> CONFIRM -> ASSESS -> EXPLAIN -> SIMULATE -> HANDOFF")

s = {"state": cv.ASSESSING, "trail": []}
assert cv.stage(s) == "ASSESS" and cv.stage({"state": cv.START}) == "LISTEN"
assert cv.stage({"state": cv.RECOVERY, "recover_to": cv.VERIFYING}) == "CONFIRM"
assert cv.stage({"state": cv.RECOVERY, "recover_to": cv.COLLECTING}) == "EXTRACT"
sid = c.post("/v1/converse", headers=H, json={"language": "en"}).json()["session_id"]
say(sid, "1 lakh")
t = say(sid, "", "handoff")
assert t["stage"] == "HANDOFF"
print("ok - ASSESS, LISTEN, recovery and early handoff map to their stages")
print("All stage tests passed.")
