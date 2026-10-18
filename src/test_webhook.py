"""Send messy, voice-style requests to the webhook in-process. Run: python -I -X utf8 src/test_webhook.py"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = ""  # test without auth
from fastapi.testclient import TestClient  # noqa: E402

import normalize as norm  # noqa: E402
import server  # noqa: E402

server.WEBHOOK_KEY = ""
client = TestClient(server.app)

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
