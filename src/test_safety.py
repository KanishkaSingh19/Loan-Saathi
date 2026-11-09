"""Safety boundaries of the whole product, checked end to end.

    python -I -X utf8 -W ignore src/test_safety.py

1. never claims approval   2. no fabricated lender offers   3. no protected attributes
4. the LLM never decides   5. numbers are validated        6. confirmation is required
7. affordability is enforced   8. excessive debt is handled   9. contradictions are asked about
"""
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "safety-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""

import converse as cv  # noqa: E402
import features  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
c = TestClient(server.app)
H = {"X-Webhook-Key": "safety-test-key"}

# Everything an applicant can see or hear: the server's wording, the dashboard, and the phone agent's prompt.
USER_FACING = ([ROOT / "src" / f for f in ("converse.py", "assessment.py", "simulate.py", "advisor.py", "server.py")]
               + [p for p in (ROOT / "dashboard" / "src").rglob("*") if p.suffix in (".js", ".jsx")
                  and ".test." not in p.name]
               + [ROOT / "agent" / "system_prompt.md"])
BANNED = re.compile(r"loan approved|you are approved|you're approved|approved for|guaranteed (loan|eligibility)|"
                    r"instant approval|\bpre-approved\b|\bsanctioned\b(?! or)", re.I)
LENDERS = re.compile(r"\b(HDFC|ICICI|SBI|State Bank|Axis Bank|Kotak|Bajaj Finance|Muthoot|Paytm|"
                     r"IDFC|Yes Bank|Bandhan|Manappuram)\b")


def scan(pattern, allow=()):
    hits = []
    for path in USER_FACING:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and not any(a in line for a in allow):
                hits.append(f"{path.name}:{n}: {line.strip()[:100]}")
    return hits


# 1. never claims approval (the phone agent's prompt is allowed to *forbid* the word)
hits = scan(BANNED, allow=('Never say "approved"',))
assert not hits, hits
print("ok 1 - no approval claims anywhere an applicant can see or hear")

# 2. no lender names, no lender offers
assert not scan(LENDERS), scan(LENDERS)
print("ok 2 - no lender names")

# 3. protected attributes are never model inputs or reasons
assert {"CODE_GENDER", "NAME_FAMILY_STATUS"} <= set(features.EXCLUDED)
assert not {"CODE_GENDER", "NAME_FAMILY_STATUS"} & set(features.VOICE_FEATURES)
applicant = server.build_applicant({"monthly_income": "30000", "loan_amount": "1 lakh", "gender": "female",
                                    "marital_status": "married", "age": "45"})
assert "gender" not in applicant and "marital_status" not in applicant
result, _ = server.decide(applicant)
why = " ".join(i["text"] for i in result["assessment"]["why"]["helping"] + result["assessment"]["why"]["risk"]).lower() + " " + result["reply_hint"].lower()
assert not re.search(r"gender|female|married|marital|widow|\byour age\b|\bage\b", why), why
print("ok 3 - gender and marital status never used; age never given as a reason")

# 4. the LLM never decides: a Gemini that claims everything is still checked and confirmed by our code
server.GEMINI_API_KEY = "fake"
script = []


async def fake(api_key, model, system, user, schema):
    return script.pop(0)

cv._gemini = fake
sid = c.post("/v1/converse", headers=H, json={"language": "en"}).json()["session_id"]
script[:] = [{"answers": [{"field": k, "value": v, "quote": v} for k, v in (
    ("loan_amount", "300000"), ("monthly_income", "40000"))] + [
    {"field": "age", "value": "30", "quote": "thirty"}],                       # not said: dropped
    "asking": "done", "reply": "Congratulations, you are approved for ₹10,00,000!", "language": "en"}]
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "300000 loan, I earn 40000"}).json()
assert t["state"] == cv.COLLECTING and "approved" not in t["reply"] and "age" not in t["answers"], t
assert t["result"] is None
# Its questions can't bring in numbers the caller never said (a live test produced "most people borrow
# between twenty thousand and one lakh"); the scripted question is used instead.
sid = c.post("/v1/converse", headers=H, json={"language": "en"}).json()["session_id"]
script[:] = [{"answers": [], "asking": "loan_amount", "language": "en",
              "reply": "No problem. Most people borrow between twenty thousand and one lakh. How much do you need?"}]
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "I don't know"}).json()
assert "twenty" not in t["reply"] and "loan" in t["reply"], t["reply"]
script[:] = [{"answers": [{"field": "loan_amount", "value": "300000", "quote": "3 lakh"}], "asking": "tenure_months",
              "language": "en", "reply": "Got it, 3 lakh. Most people pick 24 months. How long would you like?"}]
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "3 lakh"}).json()
assert "24 months" not in t["reply"], t["reply"]
script[:] = [{"answers": [{"field": "tenure_months", "value": "36", "quote": "3 years"}], "asking": "monthly_income",
              "language": "en", "reply": "Got it, 3 lakh over 3 years. How much do you earn in a month?"}]
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "3 years"}).json()
assert t["reply"].startswith("Got it, 3 lakh over 3 years"), t["reply"]   # echoing the caller is fine
server.GEMINI_API_KEY = ""
print("ok 4 - Gemini can't skip questions, invent answers or numbers, or announce a decision")

# 5. numbers are validated
assert cv.check({"literal_ok": set()}, "monthly_income", "-40000", "-40000")[1] == "negative"
assert cv.check({"literal_ok": set()}, "tenure_months", "50 saal", "50 saal")[1] == "tenure_range"
assert cv.check({"literal_ok": set()}, "loan_amount", "50 crore", "50 crore")[1] == "too_large"
r = c.post("/v1/simulate", headers=H, json={"prescreen_id": "x", "loan_amount": -1})
assert r.status_code in (404, 422)
print("ok 5 - negative, out-of-range and impossible values are refused")

# 6. confirmation is required before any assessment
s, _ = cv.start("en")
try:
    cv.require_confirmed(s)
    raise AssertionError("assessment without confirmation")
except cv.NotConfirmed:
    pass
print("ok 6 - no assessment without the applicant's confirmation")

# 7. affordability is enforced: the suggested amount never breaks the 50% rule
for income, emi, loan in ((25000, 2000, "3 lakh"), (40000, 8000, "5 lakh"), (15000, 0, "2 lakh")):
    res, _ = server.decide(server.build_applicant({"monthly_income": str(income), "loan_amount": loan,
                                                   "other_loans_count": "1" if emi else "0",
                                                   "other_loans_emi": str(emi)}))
    if res["offer_amount"]:
        assert emi + res["offer_emi"] <= 0.5 * income + 1, res
print("ok 7 - every suggested amount keeps all EMIs within 50% of income")

# 8. excessive debt is handled, including in the simulator
res, _ = server.decide(server.build_applicant({"monthly_income": "40000", "loan_amount": "50,000",
                                               "other_loans_count": "4", "other_loans_outstanding": "36 lakh",
                                               "other_loans_emi": "9000"}))
assert res["decision"] == "not_eligible_now" and res["policy_rule"] == "debt_over_limit"
print("ok 8 - debt over 24 months of income means not eligible right now")

# 9. contradictions are asked about, never overwritten
sid = c.post("/v1/converse", headers=H, json={"language": "en"}).json()["session_id"]
for text in ("3 lakh", "3 years", "40,000", "kirana shop"):
    c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text})
t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": "actually I earn 60,000"}).json()
assert t["answers"]["monthly_income"] == 40000 and t["pending"]["kind"] == "choose", t
res, _ = server.decide(server.build_applicant({"monthly_income": "20000", "loan_amount": "1 lakh",
                                               "other_loans_count": "0", "other_loans_emi": "5000"}))
assert res["decision"] == "need_more_info"
print("ok 9 - contradictions are asked about (web call) or pause the assessment (phone)")

print("All safety boundary checks passed.")
