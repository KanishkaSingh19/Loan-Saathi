"""'What is the loan for?' (step 1 of a lender's process) and the documents to keep ready (the KYC and document
steps). The purpose is asked first, read from any sentence that states it, never mistaken for the caller's work,
and never used by the model or a rule: decisions are identical with or without it. The checklist fits the loan
and how the caller earns, and Saathi never asks for or keeps a document.

Run with `python -m pytest tests/test_loan_purpose.py`.
"""
import os
import random
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("WEBHOOK_KEY", "purpose-test-key")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["DATABASE_URL"] = ""

import converse as cv  # noqa: E402
import documents  # noqa: E402
import normalize as norm  # noqa: E402
import scoring  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.mark.parametrize("said, purpose", [
    ("bike ke liye", "vehicle"), ("nayi auto ke liye", "vehicle"), ("I want a car", "vehicle"), ("tractor", "vehicle"),
    ("beti ki padhai", "education"), ("college fees", "education"), ("पढ़ाई के लिए", "education"),
    ("dukaan ke liye", "business"), ("stock lena hai", "business"), ("दुकान के लिए", "business"),
    ("ghar ki marammat", "home"), ("makaan banana hai", "home"),
    ("shaadi", "personal"), ("ilaaj ke liye", "personal"), ("इलाज", "personal"),
    ("career growth", "other"), ("automatic", "other"), ("kuch aur", "other"),
])
def test_purpose_words_map_to_a_kind_of_loan(said, purpose):
    assert norm.loan_purpose(said) == purpose


# ---- the call ----
@pytest.fixture
def call(monkeypatch):
    monkeypatch.setattr(cv, "ASK_LOAN_PURPOSE", True)
    monkeypatch.setattr(cv, "SHORT_CALL", True)
    server.GEMINI_API_KEY = server.GROQ_API_KEY = ""
    c = TestClient(server.app)
    h = {"X-Webhook-Key": os.environ["WEBHOOK_KEY"]}
    first = c.post("/v1/converse", headers=h, json={"language": "hi"}).json()

    def say(text="", action=""):
        return c.post("/v1/converse", headers=h, json={"session_id": first["session_id"], "text": text,
                                                       "action": action}).json()
    say.first = first
    return say


def test_the_purpose_is_asked_first(call):
    assert call.first["asking"] == "loan_purpose" and "kis cheez ke liye" in call.first["reply"]


def test_purpose_and_amount_in_one_answer(call):
    t = call("bike ke liye 80 hazaar chahiye")
    assert norm.loan_purpose(t["answers"]["loan_purpose"]) == "vehicle" and t["answers"]["loan_amount"] == 80000
    assert t["asking"] == "tenure_months" and "1 se 5 saal" in t["reply"], t["reply"]      # the tenure hint


def test_an_opening_sentence_fills_purpose_amount_tenure_and_income_and_the_shop_is_not_the_work(call):
    t = call("Mujhe 50 hazaar ka loan chahiye 1 saal ke liye, dukaan ke liye. Mahine mein 30 hazaar kamaata hoon.")
    a = t["answers"]
    assert norm.loan_purpose(a["loan_purpose"]) == "business"
    assert (a["loan_amount"], a["tenure_months"], a["monthly_income"]) == (50000, 12, 30000)
    assert "work" not in a, a                                  # "dukaan ke liye" is what the loan is for


def test_filler_is_not_a_purpose_and_the_help_has_buttons(call):
    t = call("hmm")
    assert t["asking"] == "loan_purpose" and "loan_purpose" not in t["answers"]
    assert [b["label"] for b in t["buttons"]][:2] == ["Gaadi", "Padhai"], t["buttons"]
    t = call(t["buttons"][1]["text"], "answer")
    assert norm.loan_purpose(t["answers"]["loan_purpose"]) == "education"


def test_the_read_back_card_shows_the_purpose(call):
    for said in ("ghar ki marammat ke liye", "2 lakh", "3 saal", "40 hazaar", "koi loan nahi"):
        t = call(said)
    while t["state"] == cv.COLLECTING and t["asking"] not in ("done", "confirm"):
        t = call({"work": "naukri karta hoon", "years_in_work": "10 saal", "past_loans": "koi nahi",
                  "ever_refused": "nahi", "housing": "apna ghar", "age": "40"}.get(t["asking"], "nahi"))
    assert t["state"] == cv.VERIFYING, t
    row = next(r for r in t["summary"] if r["field"] == "loan_purpose")
    assert row["text"].startswith("Home")
    t = call("", "confirm")
    assert t["state"] == cv.RESULT and t["result"]["documents"]["hi"], t


# ---- never a model or rule input ----
def test_the_purpose_never_reaches_the_model():
    assert "loan_purpose" not in scoring.FIELD_TO_COLUMN and "work_category" not in scoring.FIELD_TO_COLUMN
    a = server.build_applicant({"loan_amount": "1 lakh", "monthly_income": "30000", "loan_purpose": "bike ke liye"})
    assert a["loan_purpose"] == "vehicle" and "loan_type" not in a     # loan_type is the model's contract type


def test_decisions_are_identical_with_or_without_a_purpose():
    rng = random.Random(5)
    for _ in range(80):
        body = {"monthly_income": str(rng.choice([9000, 20000, 40000, 90000])),
                "loan_amount": str(rng.choice([30000, 150000, 400000, 900000])),
                "tenure_months": str(rng.choice([12, 24, 48])), "work": rng.choice(["kirana", "driver", "naukri", ""]),
                "other_loans_count": rng.choice(["0", "1"]), "other_loans_emi": rng.choice(["", "3000"]),
                "other_loans_outstanding": rng.choice(["", "80000"]), "age": rng.choice(["", "30", "55"])}
        base, _ = server.decide(server.build_applicant(body))
        for purpose in ("bike ke liye", "padhai", "dukaan ke liye", "ghar ki marammat", "shaadi"):
            r, _ = server.decide(server.build_applicant({**body, "loan_purpose": purpose}))
            for k in ("decision", "risk_band", "risk_percent", "offer_amount", "offer_emi", "policy_rule"):
                assert r.get(k) == base.get(k), (k, purpose, body)


# ---- the documents ----
def keys(purpose, work):
    return [d["key"] for d in documents.checklist(purpose, work)]


@pytest.mark.parametrize("purpose, work, must, must_not", [
    ("bike ke liye", "private naukri", ["salary_slips", "form16", "quotation"], ["itr", "admission"]),
    ("beti ki padhai", "kirana dukaan", ["itr", "business_proof", "admission", "marksheets"], ["salary_slips"]),
    ("dukaan ke liye", "sabzi bechta hoon", ["itr", "business_proof", "quotes"], ["form16"]),
    ("shaadi", "retired teacher", ["pension"], ["salary_slips", "quotation"]),
    ("tractor", "kheti karta hoon", ["land_records", "kcc", "quotation"], ["form16"]),
    ("ghar ki marammat", "auto chalata hoon", ["driving_licence", "property", "estimate"], []),
])
def test_the_checklist_fits_the_loan_and_how_they_earn(purpose, work, must, must_not):
    k = keys(purpose, work)
    assert k[:2] == ["pan", "aadhaar"], k                       # identity first, as a lender asks
    assert set(must) <= set(k) and not set(must_not) & set(k), k
    assert len(k) == len(set(k)), "no document listed twice"


def test_unknown_purpose_and_work_still_give_a_sensible_list():
    assert keys(None, None) == ["pan", "aadhaar", "photo", "bank_6m"]


def test_the_result_carries_the_checklist_and_saathi_says_it():
    a = server.build_applicant({"loan_amount": "80000", "monthly_income": "40000", "tenure_months": "24",
                                "work": "naukri karta hoon", "loan_purpose": "bike ke liye", "other_loans_count": "0"})
    r, _ = server.decide(a)
    assert r["decision"] in ("pre_approved", "needs_review")
    assert [d["key"] for d in r["documents"]["en"]] == keys("vehicle", "salaried")
    assert "quotation" in r["documents_text"] and "Salary slips".lower() in r["documents_text"].lower()
    assert "never asks for or keeps" in r["documents_note"]["en"]
    assert "Keep these documents ready" in r["reply_hint"] and "Form 16" in r["reply_hint"]
    said = cv._next_steps(r, "hi")
    assert said.startswith("Aage ke steps: Yeh documents taiyaar rakhiye: PAN card") and "screen par" in said


def test_saathi_never_asks_for_a_document_number():
    # The checklist names documents; it never asks for their numbers, and identifiers said aloud are masked.
    text = " ".join(d["text"] for p in ("vehicle", "education", "business", "home", "personal")
                    for w in documents.INCOME for d in documents.checklist(p, w))
    assert "number" not in text.lower()
    import pii
    assert "[pan]" in pii.mask("mera PAN ABCDE1234F hai")[0]
