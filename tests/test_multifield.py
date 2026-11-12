"""One message can answer several questions: every field the caller states is filled, whatever was asked,
and Saathi never asks again for something just said. Parser only (no LLM), through the real web call.

Run with `python -m pytest tests/test_multifield.py`.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("WEBHOOK_KEY", "mf-test-key")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["DATABASE_URL"] = ""

import converse as cv  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PREFILL = {"loan_amount": 300000, "tenure_months": 24, "monthly_income": 40000}


def answer(asked, said, lang="hi", prefill=None):
    """Start a call that is asking `asked` (earlier answers filled), send `said`, return the reply state."""
    server.GEMINI_API_KEY = server.GROQ_API_KEY = ""   # the deterministic parser on its own
    c = TestClient(server.app)
    s, _ = cv.start(lang)
    for k in cv.KEYS:
        if k == asked:
            break
        v = (prefill if prefill is not None else PREFILL).get(k)
        if v is not None:
            cv._set(s, k, v, str(v))
    s["asking"] = asked
    return c.post("/v1/converse", headers={"X-Webhook-Key": server.WEBHOOK_KEY},
                  json={"session_id": s["id"], "text": said}).json()


CASES = [
    # (asked, said, expected answers (subset), language)
    ("work", "kirana dukaan chalata hoon 6 saal se, ek bike loan hai 3000 EMI, 40 hazaar baaki hai",
     {"years_in_work": 6, "other_loans_count": 1, "other_loans_emi": 3000, "other_loans_outstanding": 40000,
      "work": "~kirana"}, "hi"),
    ("work", "main auto chalata hoon, 8 saal ho gaye", {"years_in_work": 8, "work": "~auto"}, "hi"),
    ("work", "sabzi bechta hoon, 10 saal se", {"years_in_work": 10, "work": "~sabzi"}, "hi"),
    ("work", "I drive a taxi for 7 years and I have two loans with EMI 9,000",
     {"years_in_work": 7, "other_loans_count": 2, "other_loans_emi": 9000, "work": "~taxi"}, "en"),
    ("loan_amount", "mujhe 2 lakh chahiye 3 saal ke liye, salary 35 hazaar hai",
     {"loan_amount": 200000, "tenure_months": 36, "monthly_income": 35000}, "hi"),
    ("loan_amount", "50 hazaar ka loan chahiye, ek scooty loan pehle se chal raha hai 2500 EMI",
     {"loan_amount": 50000, "other_loans_count": 1, "other_loans_emi": 2500}, "hi"),
    ("monthly_income", "I earn 50,000 a month and work as a driver for 5 years",
     {"monthly_income": 50000, "years_in_work": 5, "work": "~driver"}, "en"),
    ("monthly_income", "40 hazaar kamata hoon, koi loan nahi hai", {"monthly_income": 40000, "other_loans_count": 0},
     "hi"),
    ("other_loans_count", "do loan hain, kul EMI 6000, baaki 1.2 lakh",
     {"other_loans_count": 2, "other_loans_emi": 6000, "other_loans_outstanding": 120000}, "hi"),
    ("other_loans_count", "एक बाइक लोन है, 3000 की किस्त", {"other_loans_count": 1, "other_loans_emi": 3000}, "hi"),
    ("other_loans_emi", "3000 EMI hai aur 50 hazaar baaki", {"other_loans_emi": 3000, "other_loans_outstanding": 50000},
     "hi"),
    ("tenure_months", "2 saal mein chuka dunga", {"tenure_months": 24}, "hi"),
]


@pytest.mark.parametrize("asked, said, want, lang", CASES, ids=[c[1][:40] for c in CASES])
def test_every_stated_field_is_filled(asked, said, want, lang):
    t = answer(asked, said, lang)
    got = t["answers"]
    for k, v in want.items():
        assert k in got, (k, got, t["reply"])
        if isinstance(v, str) and v.startswith("~"):
            assert v[1:].lower() in str(got[k]).lower(), (k, got[k])
        else:
            assert abs(float(got[k]) - v) < 0.5, (k, got[k], v)
    assert t["asking"] not in want, f"asked again for {t['asking']} right after it was said: {t['reply']}"


@pytest.mark.parametrize("asked, said, not_filled", [
    ("tenure_months", "2 saal mein chuka dunga", "years_in_work"),     # a repayment period is not years in work
    ("loan_amount", "mujhe ek loan chahiye 1 lakh ka", "other_loans_count"),  # the loan they want
    ("loan_amount", "3 lakh ka loan chahiye", "other_loans_count"),            # an amount, not a count
    ("monthly_income", "35 hazaar salary hai", "work"),                        # money, not a job description
    ("loan_amount", "I need 1 lakh for 2 years", "years_in_work"),             # a repayment period
    ("other_loans_count", "ek loan hai, 2 saal se chal raha hai", "years_in_work"),  # how long a loan has run
])
def test_no_field_is_filled_from_the_wrong_words(asked, said, not_filled):
    t = answer(asked, said, "hi")
    assert not_filled not in t["answers"] or not_filled in PREFILL, (not_filled, t["answers"])


def test_the_reported_call_moves_on_to_the_next_unanswered_question():
    t = answer("work", "kirana dukaan chalata hoon 6 saal se, ek bike loan hai 3000 EMI, 40 hazaar baaki hai")
    assert t["asking"] == "past_loans", (t["asking"], t["reply"])
    assert "kitne saal se" not in t["reply"] and "koi aur loan chal raha" not in t["reply"].lower()
