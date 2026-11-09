"""Evaluation Lab evidence, plus the two failure paths it reports that no other suite covered.

    python -I -X utf8 -W ignore src/test_evaluation.py

1. The risk model can't load: the web call recovers without a result; the phone pre-screen fails
   closed (an error, no decision, nothing stored).
2. Speech-to-text fails upstream: a clean 502, never a made-up transcript or a stack trace.
3. The evidence itself: every number equals the file it cites, every listed check exists in its suite,
   every safeguard exists in the code, and unmeasured items carry no numbers.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "eval-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""

import converse as cv  # noqa: E402
import evaluation as ev  # noqa: E402
import scoring  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(server.app)
loose = TestClient(server.app, raise_server_exceptions=False)
H = {"X-Webhook-Key": "eval-test-key"}
ROOT = Path(__file__).resolve().parent.parent

# ---- 1. the risk model is unavailable ----
ANSWERS = {"loan_amount": "1 lakh", "tenure_months": "2 years", "monthly_income": "40,000", "work": "kirana shop",
           "years_in_work": "10 years", "other_loans_count": "0", "past_loans": "0", "ever_refused": "no",
           "housing": "own house", "age": "35"}
sid = c.post("/v1/converse", headers=H, json={"language": "en"}).json()["session_id"]
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "1 lakh"}).json()
for _ in range(20):
    if t["state"] != cv.COLLECTING:
        break
    t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": ANSWERS[t["asking"]]}).json()
assert t["state"] == cv.VERIFYING, t["state"]


def missing_model(*a, **k):
    raise FileNotFoundError("models/voice_model.txt")


before = len(c.get("/v1/applications", headers=H).json()["prescreens"])
with patch.object(scoring, "_load", missing_model):
    t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "yes", "action": "confirm"}).json()
    assert t["state"] == cv.RECOVERY and t["result"] is None and t["prescreen_id"] is None, t
    assert "Traceback" not in t["reply"] and "voice_model" not in t["reply"], t["reply"]
    r = loose.post("/v1/prescreen", headers=H, json={"monthly_income": "40000", "loan_amount": "1 lakh"})
    assert r.status_code == 500 and "decision" not in r.text, r.text
after = len(c.get("/v1/applications", headers=H).json()["prescreens"])
assert after == before, "nothing is stored without a decision"
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "yes"}).json()   # model back: retry
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "yes", "action": "confirm"}).json()
assert t["state"] == cv.RESULT and t["result"]["assessment"], t["state"]
print("ok - risk model unavailable: the web call recovers without a result; the phone pre-screen fails closed")

# ---- 2. speech-to-text fails upstream ----


def fake_client(kind):
    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            if kind == "timeout":
                raise server.httpx.ConnectTimeout("down")
            if kind == "http500":
                return server.httpx.Response(500, json={"error": "boom"})
            if kind == "not_json":
                return server.httpx.Response(200, content=b"<html>")
            return server.httpx.Response(200, json={"success": False})
    return Client


server.GNANI_STT_API_KEY = "test-stt-key"
for kind in ("timeout", "http500", "not_json", "no_transcript"):
    with patch.object(server.httpx, "AsyncClient", fake_client(kind)):
        r = c.post("/v1/stt", headers=H, files={"audio_file": ("a.wav", b"RIFF....", "audio/wav")},
                   data={"language_code": "hi-IN"})
    assert r.status_code == 502 and "transcript" not in r.json() and "Traceback" not in r.text, (kind, r.text)
r = c.post("/v1/stt", headers=H, files={"audio_file": ("a.wav", b"", "audio/wav")})
assert r.status_code == 400
print("ok - speech-to-text failures: timeout, upstream error, bad or empty response give a clean 502, "
      "never a transcript")

# ---- 3. the evidence is what the files and the code say ----
e = ev.evidence()
metrics = json.loads((ROOT / "models" / "metrics.json").read_text(encoding="utf-8"))
m = e["model"]
assert m["voice"]["auc"]["value"] == metrics["voice"]["auc"]
assert m["voice"]["brier"]["value"] == metrics["voice"]["calibration"]["brier"]
assert m["full"]["auc"]["value"] == metrics["full"]["auc"]
assert m["full"]["brier"]["value"] is None and m["full"]["brier"]["text"] == ev.NOT_MEASURED
assert m["indian_accuracy"]["text"] == ev.NOT_MEASURED
assert "not a lending decision" in m["purpose"]
report = json.loads((ROOT / "models" / "validation_report.json").read_text(encoding="utf-8"))
assert m["behaviour"]["debt_steps_checked"] == report["debt_monotonicity"]["steps_checked"]
print("ok - model numbers equal the files they cite; unmeasured ones say so")

assert e["safeguards"] and all(g["verified"] for g in e["safeguards"]), \
    [g["name"] for g in e["safeguards"] if not g["verified"]]
print("ok - every listed safeguard exists in the code")

for title, items in ev.CATALOG:
    for label, checks in items:
        for suite, match in checks or []:
            text = ev._suite_text(suite)
            assert text, (label, suite)
            assert match is None or match in text, (label, suite, match)
nm = [i for g in e["coverage"] for i in g["items"] if i["status"] == "not_measured"]
assert nm and all(i["text"] == ev.NOT_MEASURED and not i["checks"] for i in nm)
fake_run = {"suites": {"test_safety.py": {"passed": False, "ok_lines": []}}}
debt = next(i for g in ev.coverage(fake_run) for i in g["items"] if i["label"] == "Excessive debt")
assert debt["status"] == "failed", "a failed or missing suite is never shown as passed"
print("ok - every listed check exists in its suite; failed or unrun suites never show as passed")

blob = json.dumps(e, ensure_ascii=False).lower()
for bad in ("approved", "guarantee", "accuracy of 9", "99%"):
    assert bad not in blob, bad
print("All evaluation tests passed.")
