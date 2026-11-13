"""The lending rules come from config/policy.yaml: every module reads the same values, a bad file stops
start-up with a message naming the key, and changing a rule there changes the decision.

Run with `python -m pytest tests/test_policy.py`.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import policy  # noqa: E402

RAW = yaml.safe_load((ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"))


def write(tmp_path, change):
    data = json.loads(json.dumps(RAW))
    change(data)
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_every_module_reads_the_policy_values():
    import assessment
    import converse
    import scoring
    import server
    import simulate
    P = policy.POLICY
    assert scoring.MAX_FOIR == P["max_foir"] == RAW["affordability"]["max_foir"]
    assert assessment.COMFORT_FOIR == simulate.COMFORT_FOIR == P["comfort_foir"]
    assert scoring.ANNUAL_RATE == P["annual_rate"] and scoring.DEFAULT_TENURE == converse.DEFAULT_TENURE
    assert server.DEBT_LIMIT_MONTHS == P["debt_limit_months"] == RAW["debt"]["limit_months"]
    assert converse.LIMITS == P["limits"] and simulate.MIN_TENURE == P["tenure_min"]
    assert converse.AGE_RANGE == (RAW["age"]["min"], RAW["age"]["max"])


def test_the_committed_policy_keeps_the_rules_as_they_were():
    P = policy.POLICY
    assert (P["max_foir"], P["comfort_foir"], P["annual_rate"], P["default_tenure"], P["debt_limit_months"]) == \
        (0.5, 0.4, 0.18, 24, 24)
    assert (P["tenure_min"], P["tenure_max"], P["age_min"], P["age_max"]) == (3, 120, 18, 100)
    assert P["limits"]["loan_amount"] == (1_000, 50_00_000, 10_00_00_000)


@pytest.mark.parametrize("change, message", [
    (lambda d: d["affordability"].pop("max_foir"), "missing affordability.max_foir"),
    (lambda d: d["affordability"].update(max_foir=5), "affordability.max_foir = 5"),
    (lambda d: d["affordability"].update(max_foir="half"), "must be a number"),
    (lambda d: d["affordability"].update(comfort_foir=0.6), "comfort_foir can't be above max_foir"),
    (lambda d: d["tenure_months"].update(min=200), "tenure_months needs min < max"),
    (lambda d: d["amounts"]["loan_amount"].update(read_back_above=10), "min < read_back_above"),
    (lambda d: d["debt"].update(limit_months=2.5), "whole number"),
])
def test_a_bad_policy_names_the_problem(tmp_path, change, message):
    with pytest.raises(policy.PolicyError, match=message.replace("(", r"\(").replace(")", r"\)")):
        policy.load(write(tmp_path, change))


def test_not_yaml_is_refused(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("affordability: [unclosed", encoding="utf-8")
    with pytest.raises(policy.PolicyError, match="not valid YAML"):
        policy.load(p)


PROBE = """
import json, sys
sys.path.insert(0, "src")
import server
a = server.build_applicant({"loan_amount": "3 lakh", "tenure_months": "3 saal", "monthly_income": "40000",
                            "other_loans_count": "1", "other_loans_emi": "8000", "other_loans_outstanding": "1.5 lakh",
                            "work": "kirana", "years_in_work": "10", "housing": "apna ghar", "age": "35"})
r, _ = server.decide(a)
print(json.dumps({"decision": r["decision"], "offer": r["offer_amount"]}))
"""


def _decide_with(policy_path):
    env = {**os.environ, "WEBHOOK_KEY": "policy-key", "DATABASE_URL": "",
           "DB_PATH": str(Path(tempfile.mkdtemp()) / "p.db")}
    if policy_path:
        env["POLICY_PATH"] = str(policy_path)
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", PROBE], cwd=ROOT, env=env, capture_output=True,
                       text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_changing_a_rule_in_the_file_changes_the_decision(tmp_path):
    base = _decide_with(None)
    strict = _decide_with(write(tmp_path, lambda d: d["affordability"].update(max_foir=0.25, comfort_foir=0.2)))
    assert base["decision"] in ("pre_approved", "needs_review", "counter_offer"), base
    assert strict["offer"] < base["offer"] or strict["decision"] == "not_eligible_now", (base, strict)
    owed = _decide_with(write(tmp_path, lambda d: d["debt"].update(limit_months=2)))   # ₹1.5L > 2 x ₹40k
    assert owed["decision"] == "not_eligible_now", owed


def test_a_bad_policy_stops_the_server_from_starting(tmp_path):
    env = {**os.environ, "POLICY_PATH": str(write(tmp_path, lambda d: d["age"].update(min=50, max=40)))}
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", "import sys; sys.path.insert(0, 'src'); import server"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "age needs min < max" in r.stderr
