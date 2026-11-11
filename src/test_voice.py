"""Live voice: streaming STT relay, streaming TTS, spoken numbers, latency records, parser regressions.

    python -I -X utf8 -W ignore src/test_voice.py

Gnani is faked here (no network): the real protocols were checked by hand against the live service
(src/bench_latency.py measures them end to end).
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "voice-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""

import converse as cv  # noqa: E402
import evaluation  # noqa: E402
import normalize as norm  # noqa: E402
import server  # noqa: E402
import spoken  # noqa: E402
import websockets  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(server.app)
H = {"X-Webhook-Key": "voice-test-key"}

# ---- spoken numbers ----
S = spoken.speakable
assert S("EMI ₹14,977/month", "hi") == "EMI chaudah hazaar nau sau sathattar rupaye mahina"
assert S("Loan of ₹3,00,000 over 24 months", "en") == "Loan of three lakh rupees over twenty-four months"
assert S("₹1,00,00,000", "hi") == "ek crore rupaye"
assert S("57% of income", "en") == "fifty-seven percent of income"
assert S("case #a63f8db7c1 saved", "en") == "case #a63f8db7c1 saved", "codes are not numbers"
assert S("1.5 saal", "hi") == "dedh saal" and S("-₹500", "en") == "minus five hundred rupees"
assert all(spoken.words(n, "hi") for n in range(100)) and spoken.words(99, "hi") == "ninyaanave"
assert spoken.words(1234567, "en") == "twelve lakh thirty-four thousand five hundred sixty-seven"
print("ok - numbers are spoken the way people say them (Hinglish and English)")

# ---- streaming TTS: framing, the one-at-a-time lock, errors ----
SENT = []


def fake_tts(status=200, chunks=(b"\x01\x02\x03", b"\x04\x05\x06\x07"), error=False):
    class Resp:
        is_success = status == 200
        status_code = status

        async def aiter_lines(self):
            yield "event: start"
            yield 'data: {"status": "streaming_started"}'
            if error:
                yield "event: error"
                yield 'data: {"type": "error", "message": "bad"}'
                return
            for i, ch in enumerate(chunks):
                yield "event: chunk"
                yield "data: " + json.dumps({"chunk_index": i, "audio": base64.b64encode(ch).decode(), "is_final": False})
            yield "event: complete"
            yield 'data: {"chunk_index": 9, "audio": "", "is_final": true}'

        async def aclose(self):
            pass

    class Client:
        def __init__(self, *a, **k):
            pass

        def build_request(self, method, url, **kw):
            SENT.append(kw["json"])
            return (method, url)

        async def send(self, req, stream=False):
            return Resp()

        async def aclose(self):
            pass
    return Client


real_client = server.httpx.AsyncClient
server.GNANI_TTS_API_KEY = "fake-tts"
server.httpx.AsyncClient = fake_tts()
r = c.post("/v1/tts/stream", headers=H, json={"text": "Loan ₹3,00,000", "language": "hi-en"})
assert r.status_code == 200 and r.headers["x-sample-rate"] == "24000"
assert r.content == b"\x01\x02\x03\x04\x05\x06", "whole 16-bit samples only; the odd byte is carried, then dropped"
assert SENT[-1]["text"] == "Loan teen lakh rupaye" and SENT[-1]["audio_config"]["container"] == "raw"
r2 = c.post("/v1/tts/stream", headers=H, json={"text": "again", "language": "en-IN"})
assert r2.status_code == 200, "the lock is released after a stream"
server.httpx.AsyncClient = fake_tts(status=429)
assert c.post("/v1/tts/stream", headers=H, json={"text": "x"}).status_code == 502
server.httpx.AsyncClient = fake_tts(error=True)
assert c.post("/v1/tts/stream", headers=H, json={"text": "x"}).content == b""
server.httpx.AsyncClient = fake_tts()
assert c.post("/v1/tts/stream", headers=H, json={"text": "x"}).status_code == 200, "errors release the lock"
assert c.post("/v1/tts/stream", json={"text": "x"}).status_code == 401
assert not server._TTS_LOCK.locked()
server.httpx.AsyncClient = real_client
print("ok - streaming TTS: raw PCM as it arrives, numbers spelled, one request at a time, errors release the lock")

# ---- STT relay: auth, no key, framing, events ----
UP = {"frames": [], "closed": False}


class FakeUpstream:
    def __init__(self):
        self.q = asyncio.Queue()
        self.q.put_nowait(json.dumps({"type": "connected", "config": {"sample_rate": 16000}}))

    async def send(self, data):
        if isinstance(data, bytes):
            UP["frames"].append(len(data))
            if sum(UP["frames"]) >= 4096:
                await self.q.put(json.dumps({"type": "speech_start"}))
                await self.q.put(json.dumps({"type": "transcript", "text": "teen lakh", "is_final": True}))
        else:
            UP.setdefault("text", []).append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.q.get()

    async def close(self):
        UP["closed"] = True


HEADERS = {}


async def fake_connect(url, additional_headers=None, **kw):
    HEADERS.update(additional_headers or {})
    return FakeUpstream()

real_connect = websockets.connect
websockets.connect = fake_connect
server.GNANI_STT_API_KEY = "fake-stt"
with c.websocket_connect("/ws/stt") as ws:
    ws.send_text(json.dumps({"key": "wrong", "language": "hi"}))
    assert ws.receive_json() == {"type": "error", "message": "bad webhook key"}
with c.websocket_connect("/ws/stt") as ws:
    ws.send_text(json.dumps({"key": "voice-test-key", "language": "en"}))
    assert ws.receive_json()["type"] == "connected"
    for _ in range(7):
        ws.send_bytes(b"\x00" * 640)                   # browser-sized chunks
    ws.send_text(json.dumps({"type": "commit"}))
    assert ws.receive_json()["type"] == "speech_start"
    t = ws.receive_json()
    assert t["type"] == "transcript" and t["text"] == "teen lakh"
assert UP["frames"] and set(UP["frames"]) == {1024}, "Gnani gets exactly 1,024-byte frames"
assert HEADERS["language_code"] == "en-IN" and HEADERS["x-api-key-id"] == "fake-stt" and HEADERS["x-sample-rate"] == "16000"
server.GNANI_STT_API_KEY = ""
with c.websocket_connect("/ws/stt") as ws:
    ws.send_text(json.dumps({"key": "voice-test-key", "language": "hi"}))
    assert ws.receive_json()["type"] == "error"
websockets.connect = real_connect
print("ok - live STT relay: webhook key required, Gnani key stays on the server, 1,024-byte frames, events relayed")

# ---- latency records feed the Evaluation Lab ----
for ms in (2800, 3100, 3500):
    assert c.post("/v1/metrics/latency", headers=H, json={"mode": "live", "speech_end_to_first_audio_ms": ms,
                                                          "transcript_to_reply_ms": ms - 1500}).status_code == 200
assert c.post("/v1/metrics/latency", json={"mode": "live"}).status_code == 401
assert c.post("/v1/metrics/latency", headers=H, json={"mode": "live", "transcript_to_reply_ms": -5}).status_code == 422
m = c.get("/v1/evaluation", headers=H).json()["measurements"]["latency"]["browser"]
assert m["turns"] == 3 and m["speech_end_to_first_audio_ms"] == {"median": 3100, "p90": 3500, "n": 3}, m
assert evaluation.measurements([])["latency"]["browser"] is None
print("ok - measured browser turns are stored and summarised (median, 90th percentile, count)")

# ---- parser regressions found by the extraction benchmark ----
assert norm.months("six months") == 6 and norm.months("18 mahine") == 18 and norm.months("2") == 24
assert cv.valid("past_loans", "never taken one") == 0 and cv.valid("other_loans_count", "koi nahi, ek bhi nahi") == 0
assert cv.valid("past_loans", "2 liye hain") == 2
assert dict((f, v) for f, v, _ in cv.fallback_candidates("do loan liye hain pehle", "past_loans")).get("past_loans")
assert [m.group(0) for m in cv.TENURE_PHRASE.finditer("aath hazaar mahina, 8,000 mahina")] == []
assert [m.group(0) for m in cv.TENURE_PHRASE.finditer("6 lakh for 2 years")] == ["2 years"]
assert cv.check({"literal_ok": set(), "answers": {}}, "other_loans_emi", "aath hazaar mahina",
                "aath hazaar mahina") == ("ok", 8000.0)
print("ok - parser regressions from the extraction benchmark stay fixed")


# ---- Gemini regressions found by the extraction benchmark (Gemini faked with its real mistakes) ----
def gemini_says(*answers):
    async def fake(*a, **k):
        return {"answers": [{"field": f, "value": v, "quote": q} for f, v, q in answers], "reply": "", "asking": ""}
    return fake


real_gemini = cv._gemini
env = {"api_key": "x", "model": "m"}
s, _ = cv.start("en")
cv._gemini = gemini_says(("loan_amount", "60000", "sixty thousand"))          # wrong field for the income answer
out, _ = asyncio.run(cv._extract(s, env, "sixty thousand", "monthly_income"))
assert [k for k, _, _ in out] == ["monthly_income"], out
cv._gemini = gemini_says(("monthly_income", "35000", "35 hazaar"), ("loan_amount", "30000", "30-35 hazaar"))
out, _ = asyncio.run(cv._extract(s, env, "30-35 hazaar", "monthly_income"))
inc = next(w for k, _, w in out if k == "monthly_income")
assert cv.check({"literal_ok": set(), "answers": {}}, "monthly_income", "35000", inc)[0] == "choose", out
assert "loan_amount" not in [k for k, _, _ in out], "one spoken amount answers one question"
cv._gemini = real_gemini
assert cv.check({"literal_ok": set(), "answers": {}}, "tenure_months", "6", "six months") == ("ok", 6)
print("ok - Gemini regressions: one amount answers one question, ranges are asked, 'six months' stays 6")

# ---- switching language mid-call (a live-site report: "can you talk in English please" in Devanagari) ----
t = c.post("/v1/converse", headers=H, json={"language": "hi"}).json()
sid = t["session_id"]
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "क्या कैन यू टॉक इन इंग्लिश प्लीज"}).json()
assert t["language"] == "en" and t["reply"] == "Sure, I'll speak in English. How much loan do you need?", t["reply"]
assert t["state"] == cv.COLLECTING and t["answers"] == {} and t["asking"] == "loan_amount"
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "3 lakh"}).json()
assert t["answers"]["loan_amount"] == 300000 and t["language"] == "en"
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "Hinglish mein bolo", "action": "lang:hi"}).json()
assert t["language"] == "hi" and t["reply"].startswith("Theek hai, ab main Hinglish mein baat karunga.")
assert "chukana" in t["reply"], "the same question again, in Hinglish"
assert t["answers"]["loan_amount"] == 300000, "switching language changes no answer"
for said in ("I work in an English school", "meri English teacher wali job hai", "my income is 40000 in english"):
    assert cv.language_request(said) is None, said
assert cv.language_request("हिंदी में बात करो") == "hi" and cv.language_request("speak english") == "en"
print("ok - 'talk in English' (either script) or the switch changes language and re-asks; answers stay")

print("All voice tests passed.")
