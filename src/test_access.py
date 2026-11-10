"""Who can reach what: public borrower calls (a per-call token) vs staff views (the webhook key).

    python -I -X utf8 -W ignore src/test_access.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.update({"WEBHOOK_KEY": "access-test-key", "DB_PATH": str(Path(tempfile.mkdtemp()) / "test.db"),
                   "DATABASE_URL": "", "GEMINI_API_KEY": "", "ADVISOR_WEBHOOK_URL": ""})

import converse as cv  # noqa: E402
import server  # noqa: E402
import websockets  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

cv.ASK_HISTORY = False
c = TestClient(server.app)
STAFF = {"X-Webhook-Key": "access-test-key"}
ANSWERS = ("3 lakh", "3 years", "40,000", "kirana shop", "10 years", "1", "8,000", "1.5 lakh", "own house", "35")


def start(ip="1.1.1.1", **extra):
    r = c.post("/v1/converse", headers={"x-forwarded-for": ip}, json={"language": "en", **extra})
    return r


def caller(t, ip="1.1.1.1"):
    return {"X-Call-Session": t["session_id"], "X-Call-Token": t["call_token"], "x-forwarded-for": ip}


def say(h, sid, text="", action=""):
    return c.post("/v1/converse", headers=h, json={"session_id": sid, "text": text, "action": action})


# ---- a borrower needs no key; their call needs its own token ----
r = start()
assert r.status_code == 200, r.text
t = r.json()
assert t["call_token"] and len(t["call_token"]) >= 24 and "Loan Saathi" in t["reply"]
me = caller(t)
assert say(me, t["session_id"], "3 lakh").json()["answers"]["loan_amount"] == 300000
assert say({"x-forwarded-for": "1.1.1.1"}, t["session_id"], "3 years").status_code == 401, "no token"
other = start(ip="2.2.2.2").json()
assert say(caller(other, "2.2.2.2"), t["session_id"], "3 years").status_code == 401, "another call's token"
assert say(STAFF, t["session_id"], "3 years").status_code == 200, "staff can still drive any call"
for a in ANSWERS[2:]:
    last = say(me, t["session_id"], a).json()
res = say(me, t["session_id"], "yes", "confirm").json()
assert res["state"] == cv.RESULT and res["prescreen_id"]
blob = json.dumps(res)
assert t["call_token"] not in blob and "token_hash" not in blob, "the token is only handed out once"
print("ok - a borrower calls without a key; only their own token reaches their call")

# ---- staff views stay locked, even with a call token ----
for method, path in (("get", "/v1/applications"), ("get", "/v1/advisor-cases"), ("get", "/v1/audit"),
                     ("get", "/v1/study/report"), ("post", "/v1/admin/purge"), ("post", "/v1/advisor-digest"),
                     ("post", "/v1/prescreen")):
    r = c.get(path, headers=me) if method == "get" else c.post(path, headers=me, json={})
    assert r.status_code == 401, (path, r.status_code)
    r = c.get(path, headers=STAFF) if method == "get" else c.post(path, headers=STAFF, json={})
    assert r.status_code != 401, (path, r.status_code)
assert c.get("/v1/evaluation").status_code == 200, "the Evaluation Lab is public (no personal data)"
print("ok - pre-screens, cases, audit, study, admin and the phone webhook still need the staff key")

# ---- what-if and advisor requests: only for your own call ----
pid = res["prescreen_id"]
assert c.post("/v1/simulate", headers=me, json={"prescreen_id": pid, "loan_amount": 200000}).status_code == 200
staff_pid = c.post("/v1/prescreen", headers=STAFF, json={"monthly_income": "40000", "loan_amount": "1 lakh"}).json()
assert c.post("/v1/simulate", headers=me, json={"prescreen_id": staff_pid["prescreen_id"],
                                               "loan_amount": 50000}).status_code == 401
assert c.post("/v1/simulate", json={"prescreen_id": pid, "loan_amount": 1000}).status_code == 401
case = c.post("/v1/advisor-cases", headers=me, json={"session_id": t["session_id"], "question": "Longer tenure?"})
assert case.status_code == 200 and case.json()["id"]
assert c.post("/v1/advisor-cases", headers=me, json={"session_id": other["session_id"]}).status_code == 401
assert c.post("/v1/advisor-cases", headers=me, json={"prescreen_id": staff_pid["prescreen_id"]}).status_code == 401
print("ok - what-ifs and advisor requests only for the caller's own call")

# ---- voice needs a live call ----
server.GNANI_TTS_API_KEY = "fake"


class Fake:
    def __init__(self, *a, **k):
        pass

    def build_request(self, *a, **k):
        return None

    async def send(self, req, stream=False):
        class R:
            is_success, status_code = True, 200

            async def aiter_lines(self):
                yield 'data: {"audio": "AAAA", "is_final": true}'

            async def aclose(self):
                pass
        return R()

    async def aclose(self):
        pass


real = server.httpx.AsyncClient
server.httpx.AsyncClient = Fake
assert c.post("/v1/tts/stream", json={"text": "hi"}).status_code == 401
assert c.post("/v1/tts/stream", headers=me, json={"text": "hi"}).status_code == 200
server.httpx.AsyncClient = real
assert c.post("/v1/stt", files={"audio_file": ("a.wav", b"x", "audio/wav")}).status_code == 401
assert c.post("/v1/metrics/latency", headers=me, json={"mode": "live", "transcript_to_reply_ms": 900}).status_code == 200
assert c.post("/v1/metrics/latency", json={"mode": "live"}).status_code == 401


async def fake_connect(*a, **k):
    class U:
        def __aiter__(self):
            return self

        async def __anext__(self):
            import asyncio
            await asyncio.sleep(3600)

        async def send(self, d):
            pass

        async def close(self):
            pass
    return U()

server.GNANI_STT_API_KEY = "fake"
real_connect, websockets.connect = websockets.connect, fake_connect
with c.websocket_connect("/ws/stt") as ws:
    ws.send_text(json.dumps({"session": t["session_id"], "token": "wrong", "language": "en"}))
    assert ws.receive_json() == {"type": "error", "message": "bad webhook key"}
with c.websocket_connect("/ws/stt") as ws:
    ws.send_text(json.dumps({"session": t["session_id"], "token": t["call_token"], "language": "en"}))
    ws.close()
websockets.connect = real_connect
print("ok - voice (streaming and tap) and latency reports need the caller's own call")

# ---- limits, the study kit and the off switch ----
server.RATE["call"] = (3, 86400)
server._HITS.clear()
codes = [start(ip="9.9.9.9").status_code for _ in range(4)]
assert codes == [200, 200, 200, 429], codes
assert start(ip="8.8.8.8").status_code == 200, "limits are per network"
assert "try again tomorrow" in start(ip="9.9.9.9").json()["detail"]
assert start(ip="7.7.7.7", persona="p1", participant="x").status_code == 401, "the study kit is staff-only"
server.PUBLIC_CALLS = False
assert start(ip="6.6.6.6").status_code == 401
assert c.post("/v1/converse", headers=STAFF, json={"language": "en"}).status_code == 200, "staff still can"
server.PUBLIC_CALLS = True
real_key, server.WEBHOOK_KEY = server.WEBHOOK_KEY, ""
assert start(ip="5.5.5.5").status_code == 503, "closed by default: no WEBHOOK_KEY, nothing is served"
server.WEBHOOK_KEY = real_key
print("ok - per-network call limits, staff-only study kit, PUBLIC_CALLS=0 off switch, closed by default")

print("All access tests passed.")
