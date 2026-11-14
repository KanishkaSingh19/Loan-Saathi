"""A question mid-call is answered in the language it was asked in, and the call goes on where it was: the
state, the answers and the question Saathi was asking don't change, and a question never counts as a failed
answer. Fixed answers only (src/converse/faq.py): no approval claims, no lender names, the policy's own numbers.

Run with `python -m pytest tests/test_questions.py`.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("WEBHOOK_KEY", "questions-test-key")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["DATABASE_URL"] = ""

import converse as cv  # noqa: E402
import server  # noqa: E402
from converse import faq  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from policy import POLICY  # noqa: E402

QUESTIONS = [  # (question, topic, language it is in)
    ("What is an EMI?", "emi", "en"), ("EMI kya hota hai?", "emi", "hi"), ("ईएमआई क्या होता है? किस्त", "emi", "hi"),
    ("How much loan can I get?", "how_much", "en"), ("kitna loan milega?", "how_much", "hi"),
    ("मुझे कितना लोन मिलेगा?", "how_much", "hi"), ("Will my loan be approved?", "approval", "en"),
    ("kya loan pakka milega?", "approval", "hi"), ("Is this a bank?", "who", "en"), ("aap kaun ho?", "who", "hi"),
    ("What interest rate will I get?", "interest", "en"), ("byaaj kitna lagega?", "interest", "hi"),
    ("What documents do I need?", "documents", "en"), ("kaun se kaagaz chahiye?", "documents", "hi"),
    ("Will you check my CIBIL score?", "score", "en"), ("Is my data safe?", "privacy", "en"),
    ("Why do you need my age?", "why_ask", "en"), ("How long can I repay it?", "tenure", "en"),
    ("kitne saal mein chukana hoga?", "tenure", "hi"), ("What is FOIR?", "foir", "en"),
    ("processing fee kitni hogi?", "fees", "hi"), ("kitne din mein milega?", "time", "hi"),
]
NOT_QUESTIONS = ["40 hazaar", "2 saal", "haan sahi hai", "apna ghar hai", "bike ke liye", "I earn 50,000 a month",
                 "kirana dukaan chalata hoon", "nahi", "35", "do saal mein chukaunga", "do loan hain"]


@pytest.mark.parametrize("said, topic, lang", QUESTIONS)
def test_each_question_finds_its_answer(said, topic, lang):
    assert faq.is_question(said)
    assert faq.answer_question(said, lang)[0] == topic


@pytest.mark.parametrize("said", NOT_QUESTIONS)
def test_answers_are_not_mistaken_for_questions(said):
    assert not faq.is_question(said) and faq.answer_question(said) is None


def test_the_answers_never_claim_approval_or_name_a_lender_and_use_the_policy():
    text = " ".join(en + " " + hi for _, _, en, hi in faq.FAQ)
    assert not re.search(r"you are approved|loan approved|approved for|guaranteed|\bsanctioned\b", text, re.I)
    assert not re.search(r"\b(HDFC|ICICI|SBI|Axis|Kotak|Bajaj|Muthoot)\b", text)
    assert f"{POLICY['max_foir'] * 100:g}%" in text and f"{POLICY['emi_rate_low'] * 100:g}%" in text
    assert f"{POLICY['retention_days']} days" in text and "never asks your gender" in text


# ---- in the call ----
@pytest.fixture
def call(monkeypatch):
    monkeypatch.setattr(cv, "SHORT_CALL", False)
    server.GEMINI_API_KEY = server.GROQ_API_KEY = ""
    c = TestClient(server.app)
    h = {"X-Webhook-Key": os.environ["WEBHOOK_KEY"]}

    def start(lang):
        sid = c.post("/v1/converse", headers=h, json={"language": lang}).json()["session_id"]

        def say(text="", action=""):
            return c.post("/v1/converse", headers=h, json={"session_id": sid, "text": text, "action": action}).json()
        return say
    return start


def test_an_english_question_in_a_hinglish_call_is_answered_in_english_and_the_call_goes_on(call):
    say = call("hi")
    t = say("2 lakh")
    assert t["asking"] == "tenure_months" and t["language"] == "hi"
    t = say("What is an EMI?")
    assert t["language"] == "en" and t["reply"].startswith("An EMI is the fixed amount"), t["reply"]
    assert t["reply"].endswith("Now, over how many months or years would you like to repay it? If you're not sure, "
                               "say 'you decide'.") or "Now, over how many months" in t["reply"], t["reply"]
    assert t["asking"] == "tenure_months" and t["answers"] == {"loan_amount": 200000.0} and t["state"] == cv.COLLECTING
    t = say("2 years")
    assert t["answers"]["tenure_months"] == 24 and t["asking"] == "monthly_income"


def test_a_hinglish_question_in_an_english_call_is_answered_in_hinglish(call):
    say = call("en")
    say("2 lakh")
    t = say("EMI kya hota hai?")
    assert t["language"] == "hi" and t["reply"].startswith("EMI woh fix rakam") and "Ab aage:" in t["reply"], t
    t = say("मुझे कितना लोन मिलेगा?")
    assert t["language"] == "hi" and "50%" in t["reply"] and t["asking"] == "tenure_months", t


def test_questions_never_count_as_failed_answers(call):
    say = call("en")
    for q in ("What is the capital of France?", "Is this a bank?", "Who won the match?", "What is an EMI?"):
        t = say(q)
        assert t["state"] == cv.COLLECTING and t["asking"] == "loan_amount", t
    assert "loan advisor" in say("What is the capital of France?")["reply"]
    t = say("2 lakh")
    assert t["answers"]["loan_amount"] == 200000 and t["asking"] == "tenure_months"


def test_an_advisor_request_wins_over_answering_a_question(call):
    say = call("en")
    say("2 lakh")
    t = say("Can I get a longer tenure? I want to talk to a loan advisor")
    assert t["state"] == cv.HANDOFF and t["advisor_case"], t


def test_a_number_with_a_question_mark_is_still_an_answer(call):
    say = call("hi")
    for said in ("2 lakh", "2 saal", "40 hazaar", "kirana dukaan", "10 saal", "ek loan hai"):
        t = say(said)
    assert t["asking"] == "other_loans_emi", t
    t = say("EMI 3000 hai?")
    assert t["answers"]["other_loans_emi"] == 3000, t


def test_a_question_during_a_read_back_answers_then_repeats_the_read_back(call):
    say = call("en")
    t = say("40")                                                # a bare number is read back before storing
    assert t["pending"], t
    t = say("What is an EMI?")
    assert t["reply"].startswith("An EMI") and t["pending"] and "?" in t["reply"].split("Now,")[-1], t["reply"]


def test_after_the_call_a_question_is_answered_with_the_options_still_offered(call):
    say = call("en")
    for said in ("50,000", "1 year", "40,000", "kirana shop", "10 years", "no", "0", "no", "own house", "35"):
        t = say(said)
    t = say("", "confirm")
    assert t["state"] == cv.RESULT
    t = say("What is the maximum EMI I can afford?")            # about their own numbers: the simulator
    assert t["state"] == cv.WHAT_IF and "₹" in t["reply"], t
    t = say("no thanks")
    t = say("Will my loan be approved?")
    assert t["state"] == cv.COMPLETED and t["reply"].startswith("Saathi doesn't approve loans") and \
        "new check" in t["reply"], t["reply"]


def test_two_years_said_to_the_amount_question_is_the_tenure_not_two_rupees(call):
    say = call("en")
    t = say("2 years")
    assert t["answers"] == {"tenure_months": 24.0} and t["asking"] == "loan_amount", t
    assert not t["reply"].startswith("Sorry") and "at least" not in t["reply"], t["reply"]
