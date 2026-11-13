"""The lending policy from config/policy.yaml (POLICY_PATH overrides the location), checked at load.

Modules keep their own constant names (scoring.MAX_FOIR, server.DEBT_LIMIT_MONTHS, converse.LIMITS ...) and
read their values from here, so the rules live in one file and the code reads exactly as before. A missing or
out-of-range value stops start-up with a message naming the key: a typo must never quietly change a rule.
"""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config" / "policy.yaml"


class PolicyError(ValueError):
    pass


def _get(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise PolicyError(f"config/policy.yaml: missing {path}")
        cur = cur[part]
    return cur


def _number(d, path, lo, hi, integer=False, nullable=False):
    v = _get(d, path)
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PolicyError(f"config/policy.yaml: {path} must be a number, got {v!r}")
    if not lo <= v <= hi:
        raise PolicyError(f"config/policy.yaml: {path} = {v} is outside {lo}..{hi}")
    if integer and v != int(v):
        raise PolicyError(f"config/policy.yaml: {path} must be a whole number, got {v}")
    return int(v) if integer else float(v)


def load(path=None):
    """The validated policy as a flat dict of plain values."""
    p = Path(path or os.environ.get("POLICY_PATH") or DEFAULT_PATH)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise PolicyError(f"can't read the policy file {p}: {e}") from None
    except yaml.YAMLError as e:
        raise PolicyError(f"{p} is not valid YAML: {e}") from None
    if not isinstance(raw, dict):
        raise PolicyError(f"{p} must be a mapping of sections")
    out = {
        "max_foir": _number(raw, "affordability.max_foir", 0.05, 0.9),
        "comfort_foir": _number(raw, "affordability.comfort_foir", 0.05, 0.9),
        "annual_rate": _number(raw, "affordability.assumed_annual_rate", 0.0, 0.6),
        "default_tenure": _number(raw, "affordability.default_tenure_months", 3, 120, integer=True),
        "debt_limit_months": _number(raw, "debt.limit_months", 1, 240, integer=True),
        "tenure_min": _number(raw, "tenure_months.min", 1, 600, integer=True),
        "tenure_max": _number(raw, "tenure_months.max", 1, 600, integer=True),
        "age_min": _number(raw, "age.min", 16, 100, integer=True),
        "age_max": _number(raw, "age.max", 18, 130, integer=True),
        "higher_factor": _number(raw, "simulator.higher_factor", 1.0, 10.0),
        "retention_days": _number(raw, "retention.days", 1, 3650, integer=True),
        "emi_rate_low": _number(raw, "emi_rate_range.low", 0.0, 0.6),
        "emi_rate_high": _number(raw, "emi_rate_range.high", 0.0, 0.6),
        "offer_flag_rate_above": _number(raw, "offer_check.flag_rate_above", 0.05, 1.0),
    }
    limits = {}
    for field in ("loan_amount", "monthly_income", "other_loans_emi", "other_loans_outstanding"):
        base = f"amounts.{field}"
        lo = _number(raw, f"{base}.min", 0, 1e12, integer=True)
        read_back = _number(raw, f"{base}.read_back_above", 0, 1e13, integer=True, nullable=True)
        refuse = _number(raw, f"{base}.refuse_above", 1, 1e13, integer=True)
        if not lo < refuse or (read_back is not None and not lo <= read_back <= refuse):
            raise PolicyError(f"config/policy.yaml: {base} needs min < read_back_above <= refuse_above")
        limits[field] = (lo, read_back, refuse)
    out["limits"] = limits
    if out["comfort_foir"] > out["max_foir"]:
        raise PolicyError("config/policy.yaml: affordability.comfort_foir can't be above max_foir")
    if out["tenure_min"] >= out["tenure_max"] or not out["tenure_min"] <= out["default_tenure"] <= out["tenure_max"]:
        raise PolicyError("config/policy.yaml: tenure_months needs min < max, and the default tenure inside them")
    if not out["emi_rate_low"] <= out["annual_rate"] <= out["emi_rate_high"] or out["emi_rate_low"] >= out["emi_rate_high"]:
        raise PolicyError("config/policy.yaml: emi_rate_range needs low < high, with assumed_annual_rate inside it")
    if out["age_min"] >= out["age_max"]:
        raise PolicyError("config/policy.yaml: age needs min < max")
    return out


POLICY = load()


def months_text(n, lang="en"):
    """'3 months' / '10 years' for a refusal message."""
    if n % 12 == 0 and n >= 12:
        y = n // 12
        return f"{y} saal" if lang == "hi" else f"{y} year{'s' if y != 1 else ''}"
    return f"{n} mahine" if lang == "hi" else f"{n} months"
