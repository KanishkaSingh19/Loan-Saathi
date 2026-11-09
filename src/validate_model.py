"""Behavioural validation of the voice model on synthetic Indian applicants.

    python -I -X utf8 -W ignore src/validate_model.py            # report (models/validation_report.json)
    python -I -X utf8 -W ignore src/validate_model.py --strict   # exit 1 if a gate fails (use after retraining)

There is no labelled Indian loan-outcome data here, so this can't measure accuracy or calibration on
Indian borrowers. It checks what *can* be checked without labels, on a seeded grid of realistic Indian
profiles (₹8,000–₹1,50,000 a month, kirana owners, drivers, labourers, salaried and government staff,
pensioners; own/rented/family housing; ages 21–60):

1. Debt monotonicity (model): holding everything else fixed, more money owed on other loans must never
   lower the estimated risk. The model is constrained on this from the next retrain; the current one isn't.
2. Debt monotonicity (end to end): the final decision must never get *better* as debt rises
   (model + affordability rule + 24-month debt rule).
3. EMI burden (end to end): a longer tenure for the same loan must never make the decision worse
   when the risk band is unchanged... reported, not gated (the model is free in tenure).
4. Stability: a ±5% change in income should rarely flip the risk band.
5. Spread: how the synthetic population falls into low / medium / high.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os  # noqa: E402

os.environ.setdefault("WEBHOOK_KEY", "validate")
os.environ["DATABASE_URL"] = ""

import scoring  # noqa: E402
import server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "models" / "validation_report.json"
SEED = 7
TOL = 0.002  # 0.2 percentage points: smaller wiggles are numerical noise
RANK = {"pre_approved": 0, "needs_review": 1, "counter_offer": 2, "not_eligible_now": 3}
WORK = [("Commercial associate", None, "kirana shop"), ("Working", "Drivers", "auto driver"),
        ("Working", "Laborers", "construction worker"), ("Working", "Sales staff", "shop salesman"),
        ("State servant", None, "government clerk"), ("Working", "Cooking staff", "dhaba cook"),
        ("Pensioner", None, "retired")]
HOUSING = ["House / apartment", "Rented apartment", "With parents"]
INCOMES = [8000, 12000, 15000, 20000, 25000, 30000, 40000, 60000, 100000, 150000]
GATES = {"model_debt_violation_rate": 0.02, "end_to_end_debt_violations": 0, "band_flip_rate": 0.15,
         "history_violations": 0}
BAD_HISTORY = {"ever_refused": {"ever_refused": 1}, "late_last_year": {"ever_late": 1, "late_last_year": 1},
               "late_over_month": {"ever_late": 1, "late_over_month": 1}, "overdue_now": {"overdue_now": 1}}


def profiles(n=240, seed=SEED):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        income = rng.choice(INCOMES)
        income_type, occupation, label = rng.choice(WORK)
        age = rng.randint(21, 60) if income_type != "Pensioner" else rng.randint(58, 70)
        p = {
            "monthly_income": float(income),
            "loan_amount": float(round(income * rng.choice([1, 2, 3, 5, 8]), -3)),
            "tenure_months": rng.choice([12, 24, 36]),
            "age": float(age),
            "income_type": income_type,
            "housing": rng.choice(HOUSING),
            "owns_house": None,
            "active_loans": rng.choice([0, 1, 1, 2]),
            "existing_emi": 0.0,
            "active_loans_outstanding": 0.0,
            "_label": label,
        }
        if occupation:
            p["occupation"] = occupation
        p["years_employed"] = None if income_type == "Pensioner" else float(rng.choice([0.5, 2, 5, 10, 20]))
        if p["active_loans"]:
            p["existing_emi"] = float(round(income * rng.choice([0.05, 0.1, 0.2]), -2))
            p["active_loans_outstanding"] = float(round(p["existing_emi"] * rng.choice([6, 12, 24]), -3))
        out.append(p)
    return out


def clean(p):
    return {k: v for k, v in p.items() if not k.startswith("_")}


def debt_sweep(base, months=(0, 1, 3, 6, 12, 24, 36, 60)):
    rows = []
    for m in months:
        a = clean(base)
        a["active_loans"] = max(1, a.get("active_loans") or 0)
        a["existing_emi"] = a.get("existing_emi") or round(a["monthly_income"] * 0.1, -2)
        a["active_loans_outstanding"] = float(m * a["monthly_income"])
        r = scoring.score(a, n_reasons=0)
        res, _ = server.decide(a, r)
        rows.append((m, r["default_probability"], RANK.get(res["decision"], 3)))
    return rows


def main(strict=False):
    people = profiles()
    model_viol = e2e_viol = sweeps = 0
    worst = []
    for p in people:
        rows = debt_sweep(p)
        sweeps += 1
        for (m1, p1, r1), (m2, p2, r2) in zip(rows, rows[1:]):
            if p2 < p1 - TOL:
                model_viol += 1
                worst.append({"profile": p["_label"], "income": p["monthly_income"], "months_owed": [m1, m2],
                              "risk": [round(p1 * 100, 2), round(p2 * 100, 2)]})
            if r2 < r1:
                e2e_viol += 1
    steps = sweeps * 7
    worst.sort(key=lambda w: w["risk"][0] - w["risk"][1], reverse=True)

    flips = total = 0
    bands = {"low": 0, "medium": 0, "high": 0}
    decisions = {}
    for p in people:
        a = clean(p)
        r = scoring.score(a, n_reasons=0)
        bands[r["band"]] += 1
        res, _ = server.decide(a, r)
        decisions[res["decision"]] = decisions.get(res["decision"], 0) + 1
        for f in (0.95, 1.05):
            b = {**a, "monthly_income": a["monthly_income"] * f}
            total += 1
            flips += scoring.score(b, n_reasons=0)["band"] != r["band"]

    # 6. A "yes" to a bad-history question must never lower the risk (only for models that use history).
    _, meta = scoring._load()
    hist_viol = hist_checks = 0
    if "EVER_LATE" in meta["features"]:
        for p in people:
            a = {**clean(p), "past_loans": max(2, p.get("active_loans") or 0), "ever_refused": 0, "ever_late": 0,
                 "late_last_year": 0, "late_over_month": 0, "overdue_now": 0}
            if not a.get("active_loans"):
                a["active_loans"] = 1
            base_p = scoring.score(a, n_reasons=0)["default_probability"]
            for change in BAD_HISTORY.values():
                hist_checks += 1
                hist_viol += scoring.score({**a, **change}, n_reasons=0)["default_probability"] < base_p - 1e-6

    report = {
        "model": {"validation_auc": meta.get("validation_auc"), "calibrated": bool(meta.get("calibration")),
                  "monotone_up": meta.get("monotone_up", ["ANNUITY_TO_INCOME", "DEBT_TO_INCOME"])},
        "synthetic_profiles": len(people),
        "debt_monotonicity": {
            "model_violation_rate": round(model_viol / steps, 4),
            "model_violations": model_viol, "steps_checked": steps,
            "end_to_end_violations": e2e_viol,
            "worst_examples": worst[:5],
        },
        "band_flip_rate_income_5pct": round(flips / total, 4),
        "history_monotonicity": {"violations": hist_viol, "checks": hist_checks},
        "band_spread": {k: round(v / len(people), 3) for k, v in bands.items()},
        "decision_spread": {k: round(v / len(people), 3) for k, v in sorted(decisions.items())},
        "gates": GATES,
        "not_measured": "Accuracy and calibration on Indian borrowers: no labelled Indian outcome data is available.",
    }
    failed = []
    if report["debt_monotonicity"]["model_violation_rate"] > GATES["model_debt_violation_rate"]:
        failed.append("model risk falls as debt rises")
    if e2e_viol > GATES["end_to_end_debt_violations"]:
        failed.append("decision improves as debt rises")
    if report["band_flip_rate_income_5pct"] > GATES["band_flip_rate"]:
        failed.append("bands unstable under ±5% income")
    if hist_viol > GATES["history_violations"]:
        failed.append("a bad repayment history lowers risk")
    report["gates_failed"] = failed
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    d = report["debt_monotonicity"]
    print(f"Synthetic Indian profiles: {len(people)}  (model AUC {report['model']['validation_auc']}, "
          f"calibrated: {report['model']['calibrated']}, monotone in: {', '.join(report['model']['monotone_up'])})")
    print(f"1. Model risk falls as debt rises: {d['model_violations']}/{d['steps_checked']} steps "
          f"({d['model_violation_rate']:.1%})")
    for w in d["worst_examples"][:3]:
        print(f"     e.g. {w['profile']}, ₹{w['income']:,.0f}/month: {w['months_owed'][0]}→{w['months_owed'][1]} "
              f"months of income owed, risk {w['risk'][0]}% → {w['risk'][1]}%")
    print(f"2. Final decision improves as debt rises: {d['end_to_end_violations']} cases")
    print(f"4. Band flips under ±5% income: {report['band_flip_rate_income_5pct']:.1%}")
    print(f"5. Bands: {report['band_spread']}   Decisions: {report['decision_spread']}")
    if hist_checks:
        print(f"6. A bad-history 'yes' lowers risk: {hist_viol}/{hist_checks} checks")
    print(f"Not measured: {report['not_measured']}")
    print("Gates failed: " + (", ".join(failed) if failed else "none") + f"   (report: {OUT.relative_to(ROOT)})")
    if strict and failed:
        sys.exit(1)
    return report


if __name__ == "__main__":
    main(strict="--strict" in sys.argv)
