"""EMIs are shown as a range across config/policy.yaml's interest range (14-24% a year) in the decision, the
assessment, the simulator and what Saathi says; every rule (the 50% limit, the offer amount) still uses the
single assumed rate, so no decision changes.

Run with `python -m pytest tests/test_emi_range.py`.
"""
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("WEBHOOK_KEY", "emi-test-key")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["DATABASE_URL"] = ""

import scoring  # noqa: E402
import server  # noqa: E402
import simulate  # noqa: E402
from policy import POLICY  # noqa: E402

BODY = {"monthly_income": "40000", "loan_amount": "1 lakh", "tenure_months": "24", "age": "35", "work": "kirana",
        "years_in_work": "10", "housing": "apna ghar", "other_loans_count": "0"}


def test_the_range_brackets_the_assumed_rate_and_comes_from_the_policy():
    lo, hi = scoring.emi_range(100000, 24)
    mid = scoring.estimate_emi(100000, 24)
    assert lo < mid < hi
    assert (scoring.EMI_RATE_LOW, scoring.EMI_RATE_HIGH) == (POLICY["emi_rate_low"], POLICY["emi_rate_high"])
    assert abs(lo - scoring.estimate_emi(100000, 24, POLICY["emi_rate_low"])) < 1e-9
    assert scoring.emi_range_text(100000, 24) == f"{scoring.inr(lo)}–{scoring.inr(hi)}"
    assert scoring.emi_range_text(100000, 24, "hi", spoken=True) == f"{scoring.inr(lo)} se {scoring.inr(hi)}"


def test_the_decision_carries_the_range_and_its_numbers_are_unchanged():
    result, _ = server.decide(server.build_applicant(BODY))
    afford = scoring.affordable_loan(server.build_applicant(BODY))
    assert result["offer_emi"] == int(afford["emi"])                    # the rule's EMI, at the assumed rate
    assert result["offer_emi_low"] < result["offer_emi"] < result["offer_emi_high"]
    assert result["offer_emi_range_text"] == scoring.emi_range_text(result["offer_amount"], result["tenure_months"])
    for v in (result["offer_emi_low"], result["offer_emi_high"]):
        assert scoring.inr(v) in result["reply_hint"]                      # the agent may say the range


def test_decisions_and_offers_match_the_single_rate_rules_on_random_applicants():
    rng = random.Random(11)
    for _ in range(60):
        body = {**BODY, "monthly_income": str(rng.choice([9000, 18000, 30000, 60000])),
                "loan_amount": str(rng.choice([30000, 100000, 300000, 700000])),
                "tenure_months": str(rng.choice([6, 12, 24, 36, 60])), "other_loans_count": "1",
                "other_loans_emi": str(rng.choice([0, 2000, 6000])), "other_loans_outstanding": "50000"}
        a = server.build_applicant(body)
        result, _ = server.decide(a)
        afford = scoring.affordable_loan(a)
        if result["decision"] in ("pre_approved", "needs_review", "counter_offer"):
            assert result["offer_amount"] == int(afford["loan_amount"] if result["decision"] == "counter_offer"
                                                 else a["loan_amount"])
            assert result["offer_emi"] == int(afford["emi"])


def test_assessment_and_simulator_show_the_range_with_its_source():
    a = server.build_applicant(BODY)
    result, _ = server.decide(a)
    emi = result["assessment"]["requested_emi"]
    assert emi["low"] < emi["value"] < emi["high"] and "–" in emi["text"] and emi["source"] == "calc_range"
    assert "14–24%" in emi["source_text"] and "18%" in emi["source_text"]
    assert scoring.inr(emi["low"]) in result["assessment"]["spoken"]["en"] and " to " in result["assessment"]["spoken"]["en"]
    sim = simulate.run(a, 150000, 36, server.decide)
    assert sim["emi"]["value"] == round(scoring.estimate_emi(150000, 36))
    assert sim["emi"]["low"] < sim["emi"]["value"] < sim["emi"]["high"] and sim["emi"]["text"].endswith("/month")
    said = simulate.spoken(sim, "hi")
    assert f"{scoring.inr(sim['emi']['low'])} se {scoring.inr(sim['emi']['high'])}" in said


def test_a_different_range_in_the_policy_file_changes_only_the_range(tmp_path):
    data = yaml.safe_load((ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"))
    data["emi_rate_range"] = {"low": 0.10, "high": 0.30}
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    code = ("import sys, json; sys.path.insert(0, 'src'); import server; "
            f"r, _ = server.decide(server.build_applicant({BODY!r})); "
            "print(json.dumps([r['decision'], r['offer_amount'], r['offer_emi'], r['offer_emi_low'], r['offer_emi_high']]))")
    env = {**os.environ, "WEBHOOK_KEY": "k", "DATABASE_URL": "", "DB_PATH": str(tmp_path / "x.db")}
    out = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env={**env, "POLICY_PATH": str(p)}, timeout=180)
    wide = __import__("json").loads(out.stdout.strip().splitlines()[-1])
    base, _ = server.decide(server.build_applicant(BODY))
    assert wide[:3] == [base["decision"], base["offer_amount"], base["offer_emi"]]   # the decision is the same
    assert wide[3] < base["offer_emi_low"] and wide[4] > base["offer_emi_high"]       # only the range is wider


def test_the_spoken_result_says_the_range():
    import converse as cv
    result, _ = server.decide(server.build_applicant(BODY))
    en, hi = cv._fallback_result(result, "en"), cv._fallback_result(result, "hi")
    lo, hi_ = scoring.inr(result["offer_emi_low"]), scoring.inr(result["offer_emi_high"])
    assert f"{lo} to {hi_}" in en and f"{lo} se {hi_}" in hi


def test_an_llm_phrased_result_must_say_the_range_or_the_script_is_used():
    import converse as cv
    result, _ = server.decide(server.build_applicant(BODY))
    amt, lo, hi = result["offer_amount_text"], scoring.inr(result["offer_emi_low"]), scoring.inr(result["offer_emi_high"])
    one_figure = f"Your {amt} loan over 24 months looks possible, with an EMI of about {result['offer_emi_text']}."
    ranged = f"Your {amt} loan over 24 months looks possible, with an EMI of about {lo} to {hi}."
    assert not cv._amounts_ok(one_figure, result)          # refused: the scripted reply (with the range) is used
    assert cv._amounts_ok(ranged, result)
    assert "never as one figure" in cv.RESULT_PROMPT
