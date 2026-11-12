"""The recovery loop. One failed answer: the question again with its options or examples spelled out (and
buttons on the web call). Two: an optional question is offered a skip or the advisor; a required one goes to
error recovery (try again or the advisor). Buttons send ordinary words, so the parser, quote and read-back
still apply. Parser only, through the real web call.

Run with `python -m pytest tests/test_recovery_loop.py`.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("WEBHOOK_KEY", "rl-test-key")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["DATABASE_URL"] = ""

import converse as cv  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

BEFORE_HOUSING = {"loan_amount": 300000, "tenure_months": 24, "monthly_income": 40000, "work": "kirana dukaan",
                  "years_in_work": 6, "other_loans_count": 0, "past_loans": 0, "ever_refused": 0}


class Call:
    def __init__(self, lang="hi", asking="housing", prefill=BEFORE_HOUSING):
        server.GEMINI_API_KEY = server.GROQ_API_KEY = ""
        self.c = TestClient(server.app)
        s, _ = cv.start(lang)
        for k in cv.KEYS:
            if k == asking:
                break
            if k in prefill:
                cv._set(s, k, prefill[k], str(prefill[k]))
        s["asking"] = asking
        self.sid = s["id"]

    def say(self, text="", action=""):
        return self.c.post("/v1/converse", headers={"X-Webhook-Key": server.WEBHOOK_KEY},
                           json={"session_id": self.sid, "text": text, "action": action}).json()


def test_nahi_to_housing_first_spells_out_the_options_with_buttons():
    t = Call().say("nahi")
    assert t["asking"] == "housing" and t["state"] == cv.COLLECTING, t
    assert "apna ghar, kiraye ka ghar, ya parivaar ke saath" in t["reply"], t["reply"]
    assert [b["label"] for b in t["buttons"]] == ["Apna ghar", "Kiraye pe", "Parivaar ke saath"]
    assert all(b["action"] == "answer" for b in t["buttons"])


def test_a_help_button_answers_like_speech_with_the_quote_kept():
    call = Call()
    t = call.say("nahi")
    b = t["buttons"][1]
    t = call.say(b["text"], b["action"])
    assert t["answers"]["housing"] == "Kiraye pe" and t["fields"]["housing"]["said"] == "Kiraye pe", t
    assert t["asking"] == "age", t


def test_second_failure_on_an_optional_question_offers_skip_or_advisor():
    call = Call()
    call.say("nahi")
    t = call.say("nahi")
    assert t["asking"] == "housing" and "chhod dein" in t["reply"].lower() and "loan advisor" in t["reply"], t
    assert [b["action"] for b in t["buttons"]] == ["skip", "retry", "handoff"]


@pytest.mark.parametrize("text, action", [("", "skip"), ("haan, chhod do", ""), ("skip karo", "")])
def test_skip_moves_on_and_leaves_the_field_unanswered(text, action):
    call = Call()
    call.say("nahi")
    call.say("nahi")
    t = call.say(text, action)
    assert t["asking"] == "age" and "housing" in t["skipped"] and "housing" not in t["answers"], t


def test_no_to_the_skip_offer_asks_again_with_the_options():
    call = Call()
    call.say("nahi")
    call.say("nahi")
    t = call.say("nahi")
    assert t["asking"] == "housing" and "apna ghar" in t["reply"] and len(t["buttons"]) == 3, t
    t = call.say("apna ghar hai")
    assert t["answers"]["housing"] == "apna ghar hai" and t["asking"] == "age", t


def test_an_answer_after_the_skip_offer_is_still_taken():
    call = Call()
    call.say("nahi")
    call.say("nahi")
    t = call.say("parivaar ke saath rehta hoon")
    assert t["answers"]["housing"] == "parivaar ke saath rehta hoon" and t["asking"] == "age", t


def test_advisor_from_the_skip_offer_saves_a_case():
    call = Call()
    call.say("nahi")
    call.say("nahi")
    t = call.say("", "handoff")
    assert t["state"] == cv.HANDOFF and t["advisor_case"], t


def test_work_help_buttons_map_to_categories():
    call = Call(lang="en", asking="work")
    t = call.say("hmm")
    assert t["asking"] == "work" and "salaried job" in t["reply"], t
    labels = [b["label"] for b in t["buttons"]]
    assert labels[:3] == ["Own shop / business", "Salaried job", "Driver"], labels
    t = call.say(t["buttons"][2]["text"], "answer")
    assert t["answers"]["work"] == "I am a driver" and t["asking"] == "years_in_work", t
    row = next(r for r in t["summary"] if r["field"] == "work")
    assert row["category"] == "driver"


def test_required_field_goes_to_recovery_after_two_failures_with_buttons():
    call = Call(lang="en", asking="loan_amount", prefill={})
    t = call.say("pata nahi")
    assert t["state"] == cv.COLLECTING and "lakh" in t["reply"], t   # examples spelled out
    t = call.say("pata nahi")
    assert t["state"] == cv.RECOVERY and "loan advisor" in t["reply"], t
    assert [b["action"] for b in t["buttons"]] == ["yes", "handoff"]
    t = call.say("Yes, let's try again", "yes")
    assert t["state"] == cv.COLLECTING and t["asking"] == "loan_amount", t


def test_a_precise_refusal_is_not_a_skip_offer():
    call = Call(lang="en", asking="age", prefill={**BEFORE_HOUSING, "housing": "apna ghar"})
    t = call.say("15")
    assert "18 to 100" in t["reply"] and "skip" not in t["reply"].lower(), t
    t = call.say("12")
    assert "18 to 100" in t["reply"] and t["buttons"] == [], t   # the reason, again; no skip menu


def test_the_skip_offer_is_made_once_per_question():
    call = Call()
    call.say("nahi")
    call.say("nahi")
    call.say("", "retry")
    t = call.say("pata nahi")
    assert "chhod dein" not in t["reply"].lower(), t    # third failure: skipped quietly (MAX_TRIES), no loop
    assert t["asking"] == "age" and "housing" in t["skipped"], t
