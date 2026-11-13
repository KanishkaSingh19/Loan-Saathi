"""The short call: the questions the decision rules read come first, and a question only the risk model reads
is asked only while some answer to it could still move the risk band. Skipped questions are recorded as not
needed, a changed rule answer brings them back, and a follow-up never comes before its question.

Run with `python -m pytest tests/test_short_call.py`.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("WEBHOOK_KEY", "sc-test-key")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["DATABASE_URL"] = ""

import converse as cv  # noqa: E402
import scoring  # noqa: E402
import server  # noqa: E402


@pytest.fixture(autouse=True)
def short(monkeypatch):
    monkeypatch.setattr(cv, "SHORT_CALL", True)


def session(answers):
    s, _ = cv.start("hi")
    s["_build_applicant"] = server.build_applicant
    for k, v in answers.items():
        cv._set(s, k, v, str(v))
    return s


RULES = {"loan_amount": 100000, "tenure_months": 24, "monthly_income": 30000, "other_loans_count": 0}


def test_rule_questions_come_first():
    s = session({"loan_amount": 100000})
    order = []
    for _ in range(6):
        k = cv.next_field(s)
        if k not in cv.RULE_FIELDS:
            break
        order.append(k)
        cv._set(s, k, {"tenure_months": 24, "monthly_income": 30000, "other_loans_count": 1,
                       "other_loans_emi": 2000, "other_loans_outstanding": 20000}[k], "x")
    assert order == ["tenure_months", "monthly_income", "other_loans_count", "other_loans_emi",
                     "other_loans_outstanding"]


def test_model_questions_stop_once_no_answer_can_move_the_band():
    # A small loan on a high income: after a few answers nothing left can move the band, so Saathi stops.
    s = session({"loan_amount": 50000, "tenure_months": 12, "monthly_income": 150000, "other_loans_count": 0})
    asked = []
    while (k := cv.next_field(s)) != "done" and len(asked) < 20:
        asked.append(k)
        cv._set(s, k, {"work": "private naukri", "years_in_work": 5, "past_loans": 0, "ever_refused": 0,
                       "housing": "apna ghar", "age": 35}.get(k, 0), "x")
    assert s.get("not_needed") and len(asked) < 10, (asked, s.get("not_needed"))
    if s.get("not_needed"):
        build = server.build_applicant
        base = {k: str(v) for k, v in s["answers"].items()}
        bands = {scoring.band_of(scoring.risk_probability(build({**base, k: v})))
                 for k in s["not_needed"] for v in cv.PROBES[k]}
        assert len(bands | {scoring.band_of(scoring.risk_probability(build(base)))}) == 1, bands
        assert set(s["not_needed"]) <= s["skipped"]


def test_a_follow_up_never_comes_before_its_question():
    s = session(RULES)
    k = cv.next_field(s)
    assert k != "years_in_work" or "work" in s["answers"], k
    seen = []
    while (k := cv.next_field(s)) != "done" and len(seen) < 20:
        parent = cv.PARENT.get(k)
        assert parent is None or parent in s["answers"] or parent in s["skipped"] or cv._skip(parent, s["answers"]), \
            (k, parent, seen)
        seen.append(k)
        cv._set(s, k, {"work": "driver", "years_in_work": 2, "past_loans": 2, "ever_late": 1,
                       "housing": "kiraye pe", "age": 30}.get(k, 1), "x")


def test_changing_a_rule_answer_brings_skipped_questions_back():
    s = session(RULES)
    s["not_needed"] = ["housing", "age"]
    s["skipped"].update({"housing", "age"})
    cv._set(s, "monthly_income", 9000, "9 hazaar")
    assert s["not_needed"] == [] and not {"housing", "age"} & s["skipped"]


def test_without_a_scorer_every_question_is_asked():
    s, _ = cv.start("hi")
    for k, v in RULES.items():
        cv._set(s, k, v, str(v))
    assert cv.next_field(s) == "work"           # no _build_applicant: no probe, the usual order


def test_a_failing_probe_asks_everything(monkeypatch):
    s = session(RULES)
    monkeypatch.setattr(scoring, "risk_probabilities", lambda apps: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cv.next_field(s) == "work" and not s.get("not_needed")


def test_full_order_when_switched_off(monkeypatch):
    monkeypatch.setattr(cv, "SHORT_CALL", False)
    s = session({"loan_amount": 100000, "tenure_months": 24, "monthly_income": 30000})
    assert cv.next_field(s) == "work"           # the old order: work before the other-loans questions


def test_the_web_call_reports_what_was_not_needed():
    from fastapi.testclient import TestClient
    server.GEMINI_API_KEY = server.GROQ_API_KEY = ""
    c = TestClient(server.app)
    h = {"X-Webhook-Key": server.WEBHOOK_KEY}
    sid = c.post("/v1/converse", headers=h, json={"language": "en"}).json()["session_id"]
    t = None
    for _ in range(25):
        t = c.post("/v1/converse", headers=h, json={"session_id": sid, "text": {
            "loan_amount": "50,000", "tenure_months": "1 year", "monthly_income": "1,50,000",
            "other_loans_count": "no", "work": "government job", "years_in_work": "20 years", "past_loans": "2",
            "housing": "own house", "age": "45"}.get((t or {}).get("asking"), "no")}).json() if t else \
            c.post("/v1/converse", headers=h, json={"session_id": sid, "text": "50,000"}).json()
        if t["state"] == cv.VERIFYING:
            break
    assert t["state"] == cv.VERIFYING, t
    assert isinstance(t["not_needed"], list) and set(t["not_needed"]) <= set(t["skipped"])
    t = c.post("/v1/converse", headers=h, json={"session_id": sid, "text": "yes", "action": "confirm"}).json()
    assert t["state"] == cv.RESULT and t["result"]["decision"], t
