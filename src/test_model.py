"""Model plumbing: calibration, the debt monotonicity guarantee, and the interim debt floor.

    python -I -X utf8 -W ignore src/test_model.py

(The full behavioural check is src/validate_model.py, which takes a couple of minutes.)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring  # noqa: E402

model, meta = scoring._load()
base = {"monthly_income": 30000.0, "loan_amount": 100000.0, "tenure_months": 24, "age": 35.0,
        "income_type": "Working", "occupation": "Drivers", "housing": "Rented apartment",
        "years_employed": 5.0, "active_loans": 2, "existing_emi": 3000.0}

# 1. The live model is constrained in debt, so more debt never lowers risk.
assert "BUREAU_ACTIVE_DEBT" in meta["monotone_up"], meta.get("monotone_up")
risks = [scoring.score({**base, "active_loans_outstanding": 30000.0 * m}, n_reasons=0)["default_probability"]
         for m in (0, 1, 3, 6, 12, 24, 48)]
assert all(b >= a - 1e-9 for a, b in zip(risks, risks[1:])), risks
print("ok - live model: risk never falls as debt rises", [round(r * 100, 2) for r in risks])

# 2. With a constrained model the interim floor is off (it would only cost time).
r = scoring.score({**base, "active_loans_outstanding": 500000.0}, n_reasons=0)
assert r["debt_floor_applied"] is False
# ...and for an older, unconstrained model it is on and never lowers risk.
saved = meta["monotone_up"]
meta["monotone_up"] = ["ANNUITY_TO_INCOME", "DEBT_TO_INCOME"]
try:
    for owed in (0.0, 50000.0, 500000.0, 3000000.0):
        a = {**base, "active_loans_outstanding": owed}
        raw = float(model.predict(scoring._to_row(a)[0])[0])
        r = scoring.score(a, n_reasons=0)
        assert r["default_probability"] >= round(raw, 4) - 1e-4, (owed, raw, r)
finally:
    meta["monotone_up"] = saved
print("ok - interim debt floor: on only for unconstrained models, never lowers risk")

# 3. Calibration: identity when training didn't keep a map; a stored map is applied piecewise-linearly.
p = np.array([0.01, 0.1, 0.4])
assert meta.get("calibration") is None and np.allclose(scoring._calibrate(p), p)
meta["calibration"] = {"x": [0.0, 0.1, 1.0], "y": [0.0, 0.2, 1.0]}
try:
    assert np.allclose(scoring._calibrate(p), [0.02, 0.2, 0.2 + 0.3 / 0.9 * 0.8])
finally:
    meta["calibration"] = None
print("ok - calibration map applied when present, identity when training didn't keep one")

print("All model tests passed.")
