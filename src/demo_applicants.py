"""Score a few contrasting sample applicants. Run: python -I -X utf8 src/demo_applicants.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring  # noqa: E402

BASE = dict(age=34, years_employed=6, income_type="Working", occupation="Sales staff",
            education="Secondary / secondary special", housing="House / apartment",
            children=2, family_members=4, owns_car=False, owns_house=True)

CASES = {
    "Salaried, ₹3L loan": dict(BASE, monthly_income=25000, loan_amount=300000, tenure_months=24,
                               active_loans=1, active_loans_outstanding=40000),
    "Kirana owner, ₹50k loan": dict(BASE, age=45, years_employed=15, income_type="Commercial associate",
                                    occupation=None, monthly_income=30000, loan_amount=50000,
                                    tenure_months=12, active_loans=0),
    "Young, indebted": dict(age=22, years_employed=0.5, income_type="Working", occupation="Laborers",
                            education="Lower secondary", housing="Rented apartment", children=0,
                            family_members=1, owns_car=False, owns_house=False, monthly_income=15000,
                            loan_amount=200000, tenure_months=24, active_loans=3,
                            active_loans_outstanding=150000),
}

if __name__ == "__main__":
    for name, a in CASES.items():
        a = {k: v for k, v in a.items() if v is not None}
        r = scoring.score(a)
        print(f"== {name}: risk {r['default_probability']:.1%} -> {r['band']} {r['warnings'] or ''}")
        for sign, key in (("+", "raises_risk"), ("-", "lowers_risk")):
            for i in r[key]:
                print(f"   {sign} {i['label']}: {i['value']} (typical {i['typical']}) {i['impact']:+.1%}")
        print("   affordable:", scoring.affordable_loan(a))
