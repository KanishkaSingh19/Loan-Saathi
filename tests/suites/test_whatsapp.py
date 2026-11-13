"""WhatsApp channel and the daily advisor digest (Meta's Graph API and Gnani are faked; no network).

    python -I -X utf8 -W ignore tests/suites/test_whatsapp.py
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
os.environ.setdefault("GROQ_DISABLED", "1")  # no live Groq calls from a suite (LIVE_LLM_TESTS=1 run aside)
os.environ.update({"WEBHOOK_KEY": "wa-test-key", "DB_PATH": str(Path(tempfile.mkdtemp()) / "test.db"),
                   "DATABASE_URL": "", "GEMINI_API_KEY": "", "ADVISOR_WEBHOOK_URL": "",
                   "WHATSAPP_TOKEN": "fake-token", "WHATSAPP_PHONE_NUMBER_ID": "123", "WHATSAPP_VERIFY_TOKEN": "verify-me",
                   "WHATSAPP_APP_SECRET": "app-secret"})

os.environ["SHORT_CALL"] = "0"  # this suite scripts the full, fixed question order
import converse as cv  # noqa: E402
import server  # noqa: E402
import whatsapp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

cv.ASK_HISTORY = False
c = TestClient(server.app)
H = {"X-Webhook-Key": "wa-test-key"}
SENT, CALLS = [], []
PHONE = "919876543210"


class Graph:
    """Fake httpx.AsyncClient for Graph API + Gnani calls made while handling a message."""
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, data=None, files=None, headers=None):
        CALLS.append(url)
        R = server.httpx.Response
        if url.endswith("/messages"):
            SENT.append(json)
            return R(200, json={"messages": [{"id": "wamid.out"}]})
        if url.endswith("/media"):
            assert files["file"][2] == "audio/ogg"
            return R(200, json={"id": "media-out"})
        if "stt/v3" in url:
            assert files["audio_file"][0].endswith(".ogg")
            return R(200, json={"success": True, "transcript": "teen lakh"})
        if "tts/inference" in url:
            assert json["audio_config"]["container"] == "ogg"
            return R(200, content=b"OggS-fake-voice")
        return R(404)

    async def get(self, url, headers=None):
        CALLS.append(url)
        if url.endswith("/media-in"):
            return server.httpx.Response(200, json={"url": "https://lookaside.example/voice"})
        if url.endswith("/voice"):
            return server.httpx.Response(200, content=b"OggS-caller-voice")
        return server.httpx.Response(404)


server.httpx.AsyncClient = Graph
server.GNANI_STT_API_KEY = server.GNANI_TTS_API_KEY = "fake-gnani"
n = 0


def deliver(*messages, secret="app-secret"):
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "messaging_product": "whatsapp", "messages": list(messages)}}]}]}
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return c.post("/v1/whatsapp/webhook", content=raw, headers={"x-hub-signature-256": sig,
                                                                "content-type": "application/json"})


def text(body, mid=None):
    global n
    n += 1
    return {"from": PHONE, "id": mid or f"wamid.{n}", "type": "text", "text": {"body": body}}


def button(action, title):
    global n
    n += 1
    return {"from": PHONE, "id": f"wamid.{n}", "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": action, "title": title}}}


def last_text():
    m = SENT[-1]
    return m["text"]["body"] if m["type"] == "text" else m["interactive"]["body"]["text"]


# ---- verification and signatures ----
r = c.get("/v1/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
assert r.status_code == 200 and r.text == "42"
assert c.get("/v1/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                             "hub.challenge": "42"}).status_code == 403
assert deliver(text("hi"), secret="wrong").status_code == 401 and not SENT
assert c.post("/v1/whatsapp/webhook", content=b"{}").status_code == 401
token, server.WHATSAPP_TOKEN = server.WHATSAPP_TOKEN, ""
assert deliver(text("hi")).status_code == 503, "off unless fully configured"
server.WHATSAPP_TOKEN = token
print("ok - webhook verification and Meta's signature are required; off unless configured")

# ---- a conversation over text ----
assert deliver(text("Mujhe 3 lakh ka loan chahiye")).status_code == 200
assert len(SENT) == 3 and "Loan Saathi" in SENT[0]["text"]["body"], SENT   # greeting, consent, next question
consent = SENT[1]["interactive"]
assert [b["reply"]["id"] for b in consent["action"]["buttons"]] == ["consent:yes", "consent:no"]
assert all(m["to"] == PHONE for m in SENT)
key = whatsapp.thread_key(PHONE)
assert not server._run("SELECT id FROM wa_threads", fetch=True), "nothing stored before the caller agrees"
assert not server._run("SELECT id FROM sessions", fetch=True)
deliver(button("consent:yes", "Haan, save karo"))
assert "30 din" in last_text()
rows = server._run("SELECT id, payload FROM wa_threads", fetch=True)
assert rows and rows[0][0] == key and PHONE not in json.dumps(rows), "threads are stored by a hashed key"
sid = json.loads(rows[0][1])["session_id"]
assert cv.SESSIONS[sid]["answers"]["loan_amount"] == 300000 and cv.SESSIONS[sid]["channel"] == "whatsapp"
before = len(SENT)
deliver(text("2 saal", mid="wamid.same"))
deliver(text("2 saal", mid="wamid.same"))                        # Meta retries: answered once
assert len(SENT) == before + 1 and cv.SESSIONS[sid]["answers"]["tenure_months"] == 24
for said in ("40,000", "kirana shop", "10 saal", "1", "8,000", "1.5 lakh", "apna ghar", "35"):
    deliver(text(said))
s = cv.SESSIONS[sid]
assert s["state"] == cv.VERIFYING, s["state"]
m = SENT[-1]
assert m["type"] == "interactive" and m["interactive"]["type"] == "button"
assert [b["reply"]["id"] for b in m["interactive"]["action"]["buttons"]] == ["confirm", "change"]
deliver(button("confirm", "Confirm"))
assert cv.SESSIONS[sid]["state"] == cv.RESULT and cv.SESSIONS[sid]["result"]["assessment"]
assert "approved" not in last_text().lower() and "₹" in last_text(), last_text()
print("ok - a full pre-screen over WhatsApp: same conversation, buttons map to the web call's actions")

# ---- voice notes both ways ----
CALLS.clear()
before = len(SENT)
global_phone = "919000000001"
deliver({"from": global_phone, "id": "wamid.voice", "type": "audio", "audio": {"id": "media-in", "mime_type": "audio/ogg"}})
assert any("stt/v3" in u for u in CALLS) and any("tts/inference" in u for u in CALLS)
audio = [m for m in SENT[before:] if m["type"] == "audio"]
assert audio and audio[0]["audio"]["id"] == "media-out" and audio[0]["to"] == global_phone
s2 = next(x for x in cv.SESSIONS.values() if x.get("channel") == "whatsapp" and x["id"] != sid)
assert s2["answers"].get("loan_amount") == 300000, "the voice note's transcript was the answer"
print("ok - voice notes: OGG to Gnani STT, the answer as a Gnani voice note plus text")

# ---- unsupported messages, outgoing formats ----
before = len(SENT)
deliver({"from": PHONE, "id": "wamid.img", "type": "image", "image": {"id": "x"}})
assert len(SENT) == before + 1 and "text" in SENT[-1]["type"]
many = whatsapp.outgoing(PHONE, "Pick one", [{"label": f"Option number {i} is long", "action": f"choose:{i}"}
                                              for i in range(5)])
assert many[0]["interactive"]["type"] == "list" and len(many[0]["interactive"]["action"]["sections"][0]["rows"]) == 5
assert all(len(r["title"]) <= 24 for r in many[0]["interactive"]["action"]["sections"][0]["rows"])
three = whatsapp.outgoing(PHONE, "Ok?", [{"label": "Talk to a loan advisor", "action": "handoff"}])
assert len(three[0]["interactive"]["action"]["buttons"][0]["reply"]["title"]) <= 20
assert whatsapp.guess_language("Hello, I need a loan") == "en" and whatsapp.guess_language("mujhe loan chahiye") == "hi"
print("ok - unsupported messages get a plain answer; buttons and lists fit WhatsApp's limits")

# ---- daily advisor digest ----
deliver(text("loan advisor se baat karni hai"))
r = c.post("/v1/advisor-cases", headers=H, json={"session_id": sid, "question": "Longer tenure?"}).json()
d = c.post("/v1/advisor-digest", headers=H).json()
assert d["configured"] is False and d["delivered"] is False and d["open"] >= 1
assert r["id"] in d["text"] and "Longer tenure?" in d["text"]
c.patch(f"/v1/advisor-cases/{r['id']}", headers=H, json={"status": "closed", "advisor": "A", "note": ""})
d2 = c.post("/v1/advisor-digest", headers=H).json()
assert r["id"] not in d2["text"], "closed cases leave the digest"
posted = []
server.ADVISOR_WEBHOOK_URL = "https://hooks.example/advisors"
real_post = server.httpx.post
server.httpx.post = lambda url, json=None, timeout=None: (posted.append(json), server.httpx.Response(200))[1]
d3 = c.post("/v1/advisor-digest", headers=H).json()
assert d3["configured"] and d3["delivered"] and posted and posted[-1]["text"] == d3["text"]
server.httpx.post = real_post
assert c.post("/v1/advisor-digest").status_code == 401
print("ok - daily advisor digest: open cases only, delivered only when an advisor channel confirms")

print("All WhatsApp and digest tests passed.")
