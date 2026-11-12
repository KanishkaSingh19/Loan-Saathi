"""After the call: no dead end. Once the call is over Saathi offers the what-if simulator (when there is a
result), the loan advisor, or a new check, as words and as buttons, and handles each reply. Reopening only
ever goes to the what-if simulator: nothing is collected or assessed again in an ended call.

Run with `python -m pytest tests/test_after_call.py`.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("WEBHOOK_KEY", "ac-test-key")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["DATABASE_URL"] = ""

import converse as cv  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PROFILE = ["3 lakh", "3 years", "40,000", "kirana shop", "10 years", "1", "8,000", "1.5 lakh", "own house", "35"]


@pytest.fixture
def call(monkeypatch):
    monkeypatch.setattr(cv, "ASK_HISTORY", False)
    server.GEMINI_API_KEY = server.GROQ_API_KEY = ""
    c = TestClient(server.app)
    h = {"X-Webhook-Key": server.WEBHOOK_KEY}
    sid = c.post("/v1/converse", headers=h, json={"language": "en"}).json()["session_id"]

    def say(text="", action=""):
        r = c.post("/v1/converse", headers=h, json={"session_id": sid, "text": text, "action": action})
        assert r.status_code == 200, r.text
        return r.json()
    return say


def to_result(say):
    t = None
    for text in PROFILE:
        t = say(text)
    assert t["state"] == cv.VERIFYING, t
    t = say("", "confirm")
    assert t["state"] == cv.RESULT and t["result"], t
    return t


def to_completed(say):
    to_result(say)
    t = say("no, thanks")
    assert t["state"] == cv.COMPLETED and t["done"], t
    return t


def test_goodbye_names_the_three_ways_on_with_buttons(call):
    t = to_completed(call)
    assert "different amount" in t["reply"] and "loan advisor" in t["reply"] and "new check" in t["reply"], t
    assert [b["action"] for b in t["buttons"]] == ["whatif", "handoff", "new_call"]


def test_what_if_button_reopens_the_simulator(call):
    to_completed(call)
    t = call("", "whatif")
    assert t["state"] == cv.WHAT_IF and not t["done"] and "2 lakh" in t["reply"], t
    t = call("what if I take 2 lakh?")
    assert t["state"] == cv.WHAT_IF and t["simulation"]["simulation"]["loan_amount"]["value"] == 200000, t
    assert "COMPLETED -> WHAT_IF" in t["trail"]


def test_a_spoken_what_if_after_the_call_is_answered_directly(call):
    to_completed(call)
    t = call("what if I repay over 5 years?")
    assert t["state"] == cv.WHAT_IF and t["simulation"]["simulation"]["tenure"]["value"] == 60, t


def test_advisor_after_the_call_saves_a_case_and_then_only_a_new_check_is_offered(call):
    to_completed(call)
    t = call("", "handoff")
    assert t["advisor_case"], t
    t = call("hello?")
    assert "new check" in t["reply"] and [b["action"] for b in t["buttons"]] == ["new_call"], t


@pytest.mark.parametrize("text, action", [("", "new_call"), ("start a new check", ""), ("naya check karna hai", "")])
def test_new_check_is_flagged_for_the_call_screen(call, text, action):
    to_completed(call)
    t = call(text, action)
    assert t["new_call"] is True and t["state"] == cv.COMPLETED, t


def test_anything_else_after_the_call_repeats_the_options_not_a_dead_end(call):
    to_completed(call)
    t = call("ok")
    assert t["reply"].startswith("Your pre-screening is complete.") and "new check" in t["reply"], t
    assert len(t["buttons"]) == 3


def test_ended_call_without_a_result_offers_only_advisor_or_new_check(call):
    call("pata nahi")
    t = call("pata nahi")
    assert t["state"] == cv.RECOVERY, t
    t = call("no")
    assert t["state"] == cv.COMPLETED and "different amount" not in t["reply"], t
    assert [b["action"] for b in t["buttons"]] == ["handoff", "new_call"]
    t = call("what if I take 2 lakh?")       # no result to simulate: nothing reopens
    assert t["state"] == cv.COMPLETED and t["simulation"] is None, t


def test_no_answer_is_collected_or_assessed_after_the_call(call):
    to_completed(call)
    before = call("ok")
    t = call("my income is 90,000")
    assert t["answers"] == before["answers"] and t["result"] == before["result"], t
