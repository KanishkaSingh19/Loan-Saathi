"""Send messy, voice-style requests to the webhook in-process. Run: python -I -X utf8 src/test_webhook.py"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "webhook-test-key"  # the server refuses to run without one
# Never write test callers into the live dashboard's database.
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""  # never write test callers into the real (Neon) database
from fastapi.testclient import TestClient  # noqa: E402

import normalize as norm  # noqa: E402
import server  # noqa: E402

server.WEBHOOK_KEY = "webhook-test-key"
client = TestClient(server.app, headers={"X-Webhook-Key": "webhook-test-key"})

CASES = {
    "Kirana owner, small loan (Hinglish strings)": {
        "monthly_income": "30 hazaar", "loan_amount": "50,000", "tenure_months": "12", "age": "45 saal",
        "work": "kirana ki dukaan hai", "years_in_work": "15", "education": "10th pass",
        "housing": "apna ghar", "family_members": "5", "children": "3", "owns_vehicle": "haan",
        "other_loans_count": "0",
    },
    "Salaried, asks too much": {
        "monthly_income": 25000, "loan_amount": "3 lakh", "tenure_months": "2 saal", "age": 34,
        "work": "private company me sales ka kaam", "years_in_work": 6, "education": "graduate",
        "housing": "kiraye ka ghar", "family_members": 4, "children": 2, "owns_vehicle": "nahi",
        "other_loans_count": 1, "other_loans_outstanding": "40 hazaar", "other_loans_emi": "2000",
    },
    "Young labourer, heavy debt": {
        "monthly_income": "15000", "loan_amount": "2 lakh", "tenure_months": 24, "age": 22,
        "work": "construction mazdoor", "years_in_work": "6 mahine", "education": "8th",
        "housing": "rent", "family_members": 1, "children": 0, "owns_vehicle": "no",
        "other_loans_count": 3, "other_loans_outstanding": "1.5 lakh",
    },
    "Missing income, unfilled placeholders": {
        "monthly_income": "{{monthly_income}}", "loan_amount": "1 lakh", "work": "", "age": "null",
    },
}

if __name__ == "__main__":
    assert norm.number("2.5 lakh") == 250000 and norm.number("dedh lakh") == 150000
    assert norm.number("25k") == 25000 and norm.number("₹1,20,000") == 120000
    assert norm.number("{{x}}") is None and norm.boolean("haan ji") is True
    assert norm.years("6 mahine") == 0.5 and norm.months("2 saal") == 24 and norm.months("18") == 18
    assert server.inr(150000) == "₹1,50,000" and server.inr(12345678) == "₹1,23,45,678"
    assert client.get("/health").json() == {"ok": True}
    assert TestClient(server.app).post("/v1/prescreen", json=CASES["Kirana owner, small loan (Hinglish strings)"]).status_code == 401
    for name, body in CASES.items():
        r = client.post("/v1/prescreen", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        print(f"== {name}\n   decision={out['decision']} band={out['risk_band']} risk={out['risk_percent']}% "
              f"offer={out.get('offer_amount_text')} emi={out.get('offer_emi_text')}\n   hint: {out['reply_hint']}")
    r = client.post("/v1/post-call", json={"callId": "test", "transcript": "hello"})
    assert r.json() == {"ok": True}
    n = len(client.get("/v1/applications").json()["prescreens"])
    print(f"\nAll good. {n} pre-screens stored.")


# Debt rule: owing more than 24 months of income means "not eligible now", whatever the model says
# (it scores large bureau debts as safer, a training-data artifact).
import server  # noqa: E402

base = {"loan_amount": "1 lakh", "monthly_income": "40 hazaar", "work": "bus driver", "years_in_work": "2",
        "other_loans_count": "4", "other_loans_emi": "15000", "housing": "rented", "age": "42",
        # a complete, clean repayment history (the model is cautious about unanswered history)
        "past_loans": "5", "ever_refused": "no", "ever_late": "no", "overdue_now": "no"}
r, _ = server.decide(server.build_applicant({**base, "other_loans_outstanding": "36,51,000"}))
assert r["decision"] == "not_eligible_now" and r["policy_rule"] == "debt_over_limit", r
assert "more than 2 years of income" in r["reply_hint"] and "paying down existing loans" in r["tips"], r
r, _ = server.decide(server.build_applicant({**base, "other_loans_outstanding": "9,60,000"}))
assert r["policy_rule"] == "" and r["decision"] != "not_eligible_now", r
print("Debt rule OK.")
