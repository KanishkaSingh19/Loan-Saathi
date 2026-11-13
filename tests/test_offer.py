"""'Check an offer you got': the real yearly rate (fees included), total cost, whether the EMI fits, and flags
for a rate far above config/policy.yaml's threshold or a broken 50% rule. Plain words, labelled an estimate,
never a lender's name. Through src/offer.py, POST /v1/offer-check and the call ("mujhe ek offer mila hai").

Run with `python -m pytest tests/test_offer.py`.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("WEBHOOK_KEY", "offer-test-key")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["DATABASE_URL"] = ""

import converse as cv  # noqa: E402
import offer  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from policy import POLICY  # noqa: E402

LENDERS = re.compile(r"HDFC|ICICI|SBI|Axis|Kotak|Bajaj|Muthoot|Paytm", re.I)


# ---- the maths ----
@pytest.mark.parametrize("principal, rate", [(100000, 0.12), (250000, 0.18), (50000, 0.30), (500000, 0.105)])
def test_the_implied_rate_inverts_the_emi_formula(principal, rate):
    n, r = 36, rate / 12
    emi = principal * r / (1 - (1 + r) ** -n)
    assert abs(offer.implied_yearly_rate(principal, emi, n) - rate) < 1e-6


def test_a_fee_raises_the_real_rate_and_the_total_cost():
    no_fee = offer.check(200000, 24, 10000)
    fee = offer.check(200000, 24, 10000, processing_fee=6000)
    assert fee["effective_rate"] > no_fee["effective_rate"] == fee["rate_without_fee"]
    assert fee["total_paid"] == no_fee["total_paid"] == 240000
    assert fee["total_cost"] == no_fee["total_cost"] + 6000 and fee["money_received"] == 194000


def test_a_flat_rate_quote_is_shown_to_hide_a_higher_real_rate():
    r = offer.check(100000, 12, 9333, quoted_rate=0.12)        # "12% flat": interest on the full amount all year
    assert 0.20 < r["effective_rate"] < 0.23
    assert [f["code"] for f in r["flags"]] == ["quoted_rate_lower"] and r["verdict"] == "check_carefully"


def test_far_above_the_threshold_and_the_50_rule_are_flagged():
    r = offer.check(300000, 24, 18000, monthly_income=30000, existing_emi=2000)
    codes = [f["code"] for f in r["flags"]]
    assert r["effective_rate"] > POLICY["offer_flag_rate_above"] and "rate_far_above" in codes
    assert "breaks_50_rule" in codes and r["affordability"]["max_emi"] == 13000
    ok = offer.check(150000, 36, 5400, monthly_income=40000)
    assert ok["verdict"] == "looks_reasonable" and not ok["flags"] and ok["affordability"]["fits"]


def test_numbers_that_cant_be_a_loan_are_said_plainly():
    r = offer.check(200000, 24, 5000)                            # 24 x 5,000 < 2 lakh
    assert r["verdict"] == "numbers_dont_add_up" and r["effective_rate"] is None
    assert "don't add up" in r["summary"]["en"]


@pytest.mark.parametrize("args, message", [
    ((None, 24, 1000), "loan amount"), ((100000, None, 5000), "tenure"), ((100000, 24, None), "EMI"),
    ((500, 24, 100), "at least"), ((100000, 600, 1000), "between"), ((100000, 24, 200000), "smaller than the loan"),
])
def test_bad_or_missing_numbers_say_what_to_fix(args, message):
    with pytest.raises(offer.OfferError, match=message):
        offer.check(*args)


def test_results_are_labelled_estimates_and_never_name_a_lender():
    r = offer.check(*[offer.parse("HDFC Bank ne offer diya 2 lakh, 24 mahine, EMI 11,000")[k]
                      for k in ("loan_amount", "tenure_months", "emi")])
    text = str(r)
    assert "Estimate" in r["label"] and "andaaza" in r["label_hi"].lower()
    assert r["summary"]["en"].startswith("Estimate") and r["summary"]["hi"].startswith("Andaaza")
    assert not LENDERS.search(text) and "approv" not in text.lower()


# ---- reading the offer ----
@pytest.mark.parametrize("said, want", [
    ("mujhe ek offer mila hai 2 lakh ka, 24 mahine, EMI 11,000, processing fee 2000",
     {"loan_amount": 200000, "tenure_months": 24, "emi": 11000, "processing_fee": 2000}),
    ("I got an offer of 1.5 lakh for 3 years with EMI 5,600", {"loan_amount": 150000, "tenure_months": 36, "emi": 5600}),
    ("offer 50 hazaar, 12 mahine ki kist 4800", {"loan_amount": 50000, "tenure_months": 12, "emi": 4800}),
    ("1 lakh 12 months 12% flat EMI 9,333", {"loan_amount": 100000, "tenure_months": 12, "emi": 9333,
                                             "quoted_rate": 0.12}),
    ("ऑफ़र मिला है 3 लाख का, 2 साल, किस्त 15,000", {"loan_amount": 300000, "tenure_months": 24, "emi": 15000}),
])
def test_offer_sentences_are_read(said, want):
    got = offer.parse(said)
    for k, v in want.items():
        assert got[k] is not None and abs(got[k] - v) < 1e-6, (k, got)


# ---- the API ----
def client():
    server.GEMINI_API_KEY = server.GROQ_API_KEY = ""
    return TestClient(server.app)


H = {"X-Webhook-Key": os.environ["WEBHOOK_KEY"]}


def test_api_reads_spoken_values_and_says_it_in_the_callers_language():
    r = client().post("/v1/offer-check", headers=H, json={"loan_amount": "2 lakh", "tenure_months": "2 saal",
                                                           "emi": "11,000", "processing_fee": "2000",
                                                           "monthly_income": "40 hazaar", "language": "hi"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["offer"]["loan_amount"] == 200000 and j["offer"]["tenure_months"] == 24 and j["affordability"]["known"]
    assert j["reply"].startswith("Andaaza")


def test_api_takes_a_sentence_and_refuses_an_incomplete_offer():
    c = client()
    ok = c.post("/v1/offer-check", headers=H, json={"text": "I got an offer of 1.5 lakh for 3 years with EMI 5,600"})
    assert ok.status_code == 200 and ok.json()["offer"]["emi"] == 5600
    bad = c.post("/v1/offer-check", headers=H, json={"text": "I got an offer of 1.5 lakh"})
    assert bad.status_code == 422 and "tenure" in bad.json()["detail"] and "EMI" in bad.json()["detail"]


def test_api_needs_a_call_or_the_staff_key_and_uses_the_callers_own_income():
    c = client()
    assert c.post("/v1/offer-check", json={"text": "2 lakh, 2 saal, EMI 11000"}).status_code == 401
    start = c.post("/v1/converse", json={"language": "en"}).json()
    call = {"X-Call-Session": start["session_id"], "X-Call-Token": start["call_token"]}
    for said in ("2 lakh", "2 years", "30,000"):
        c.post("/v1/converse", headers=call, json={"session_id": start["session_id"], "text": said})
    r = c.post("/v1/offer-check", headers=call, json={"text": "2 lakh, 2 saal, EMI 16,000"}).json()
    assert r["affordability"]["known"] and r["affordability"]["share"] == round(16000 / 30000, 4)


# ---- in the call ----
@pytest.fixture
def call(monkeypatch):
    monkeypatch.setattr(cv, "SHORT_CALL", False)
    c = client()
    sid = c.post("/v1/converse", headers=H, json={"language": "hi"}).json()["session_id"]

    def say(text="", action=""):
        return c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text, "action": action}).json()
    return say


def test_saying_you_got_an_offer_collects_it_checks_it_and_goes_back(call):
    call("2 lakh")                                               # answering the loan question
    t = call("mujhe ek offer mila hai")
    assert t["asking"] == "offer" and "EMI" in t["reply"], t
    t = call("3 lakh ka, 2 saal")
    assert t["asking"] == "offer" and "EMI" in t["reply"] and "rakam" not in t["reply"], t   # only what's missing
    t = call("kist 18000")
    assert t["offer_result"]["verdict"] == "check_carefully" and "38%" in t["reply"], t
    assert t["asking"] == "tenure_months" and "chukana chahenge" in t["reply"], t        # back where we were
    assert t["answers"] == {"loan_amount": 200000.0} and t["state"] == cv.COLLECTING     # profile untouched


def test_a_whole_offer_in_one_sentence_is_answered_at_once(call):
    t = call("ek offer mila hai 2 lakh, 24 mahine, EMI 11,000")
    assert t["offer_result"] and t["asking"] == "loan_amount" and "loan_amount" not in t["answers"], t


def test_the_offer_check_can_be_dropped_or_left_for_an_advisor(call):
    call("mujhe ek offer mila hai")
    t = call("rehne do")
    assert t["asking"] == "loan_amount" and not t["offer_result"], t
    call("mujhe ek offer mila hai")
    t = call("", "handoff")
    assert t["state"] == cv.HANDOFF and t["advisor_case"], t


def test_an_offer_can_be_checked_after_the_call_and_counter_offer_words_dont_trigger_it(call):
    for said in ("50 hazaar", "1 saal", "40 hazaar", "kirana dukaan", "10 saal", "nahi", "0", "nahi", "apna ghar",
                 "35"):
        t = call(said)
    t = call("", "confirm")
    assert t["state"] == cv.RESULT, t
    t = call("haan offer theek hai")                          # about Saathi's own result, not a lender's offer
    assert not t["offer_result"], t
    t = call("no thanks")
    assert t["state"] == cv.COMPLETED
    t = call("mujhe ek offer mila hai 2 lakh, 24 mahine, EMI 11,000")
    assert t["offer_result"] and t["offer_result"]["affordability"]["known"] and t["state"] == cv.COMPLETED, t


def test_the_flag_threshold_comes_from_the_policy_file(tmp_path):
    import subprocess
    import yaml
    data = yaml.safe_load((ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"))
    data["offer_check"]["flag_rate_above"] = 0.40
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    code = ("import sys; sys.path.insert(0, 'src'); import offer; "
            "print([f['code'] for f in offer.check(300000, 24, 18000)['flags']])")
    out = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env={**os.environ, "POLICY_PATH": str(p)}, timeout=120)
    assert out.stdout.strip().splitlines()[-1] == "[]", out.stderr[-500:]      # 38% is under a 40% threshold
