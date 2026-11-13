"""The repayment-history questions: funnel, parsing, missing answers, model effect, explanations.

    python -I -X utf8 -W ignore tests/suites/test_history.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
os.environ.setdefault("GROQ_DISABLED", "1")  # no live Groq calls from a suite (LIVE_LLM_TESTS=1 run aside)
os.environ["WEBHOOK_KEY"] = "history-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""
os.environ["GEMINI_API_KEY"] = ""

os.environ["SHORT_CALL"] = "0"  # this suite scripts the full, fixed question order
import converse as cv  # noqa: E402
import scoring  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

assert cv.ASK_HISTORY, "production asks the history questions"
c = TestClient(server.app)
H = {"X-Webhook-Key": "history-test-key"}


def new(lang="en"):
    return c.post("/v1/converse", headers=H, json={"language": lang}).json()["session_id"]


def say(sid, text="", action=""):
    r = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text, "action": action})
    assert r.status_code == 200, r.text
    return r.json()


def to_history(sid, loans="no"):
    for text in ("1 lakh", "2 years", "30,000", "kirana shop", "10 years", loans):
        t = say(sid, text)
    return t


# ---- a first-time borrower hears only two questions ----
sid = new()
t = to_history(sid, "no")
assert t["asking"] == "past_loans", t
t = say(sid, "none, this is my first loan")
assert t["answers"]["past_loans"] == 0 and t["asking"] == "ever_refused", t
t = say(sid, "no")
assert t["answers"]["ever_refused"] == 0 and t["asking"] == "housing", t   # no lateness/overdue questions
print("ok - first-time borrower: 2 history questions")

# ---- someone with loans: the funnel ----
sid = new()
for text in ("1 lakh", "2 years", "30,000", "auto driver", "6 years", "1", "3,000", "40,000"):
    t = say(sid, text)
assert t["asking"] == "past_loans", t
t = say(sid, "3")
t = say(sid, "no")
assert t["asking"] == "ever_late", t
t = say(sid, "haan")
assert t["asking"] == "late_last_year", t
t = say(sid, "nahi")
assert t["asking"] == "late_over_month", t
t = say(sid, "no")
assert t["asking"] == "overdue_now", t                                     # has a running loan
t = say(sid, "no")
assert t["asking"] == "housing", t
assert {k: t["answers"][k] for k in cv.HISTORY} == {"past_loans": 3, "ever_refused": 0, "ever_late": 1,
                                                    "late_last_year": 0, "late_over_month": 0, "overdue_now": 0}
sid = new()
for text in ("1 lakh", "2 years", "30,000", "auto driver", "6 years", "1", "3,000", "40,000", "2", "no"):
    t = say(sid, text)
t = say(sid, "never")                                                      # never late: no follow-ups
assert t["answers"]["ever_late"] == 0 and t["asking"] == "overdue_now", t
print("ok - with loans: lateness funnel, follow-ups only after a 'yes', overdue only with running loans")

# ---- "don't know" stays unknown, never "no" ----
sid = new()
to_history(sid, "no")
say(sid, "2")
t = say(sid, "I don't know")
assert "ever_refused" in t["skipped"] and "ever_refused" not in t["answers"], t
applicant = server.build_applicant({"monthly_income": "30000", "loan_amount": "1 lakh", "past_loans": "2"})
assert applicant.get("ever_refused") is None and applicant.get("ever_late") is None  # left out = unknown
print("ok - 'I don't know' is kept as unknown (the model has a learned 'not answered' branch)")

# ---- parsing: Hinglish, Devanagari, numbers ----
assert cv.valid("past_loans", "pehli baar") == 0 and cv.valid("past_loans", "कोई नहीं") == 0
assert cv.valid("past_loans", "teen") == 3 and cv.valid("past_loans", "999") is None
assert cv.valid("ever_late", "हाँ") == 1 and cv.valid("ever_late", "नहीं") == 0
assert cv.valid("ever_refused", "haan ek baar") == 1 and cv.DONT_KNOW.search("kuch pata nahi kya")
assert cv.valid("ever_late", "blue") is None
print("ok - yes/no and counts in English, Hinglish and Devanagari")

# ---- build_applicant keeps the answers consistent ----
a = server.build_applicant({"monthly_income": "30000", "loan_amount": "1 lakh", "other_loans_count": "0",
                            "past_loans": "0"})
assert a["ever_late"] == 0 and a["late_last_year"] == 0 and a["overdue_now"] == 0    # never borrowed
a = server.build_applicant({"monthly_income": "30000", "loan_amount": "1 lakh", "other_loans_count": "2",
                            "past_loans": "0", "ever_late": "no"})
assert a["past_loans"] == 2 and a["late_last_year"] == 0                              # running loans count
print("ok - consistent applicant: never borrowed means never late; running loans are past loans")

# ---- the model uses the answers, in the right direction ----
_, meta = scoring._load()
assert all(f in meta["features"] for f in ("PAST_LOANS", "EVER_LATE", "LATE_LAST_YEAR")), "retrain needed"
base = server.build_applicant({"monthly_income": "30000", "loan_amount": "1 lakh", "tenure_months": "24",
                               "work": "auto driver", "years_in_work": "6", "other_loans_count": "1",
                               "other_loans_emi": "3000", "other_loans_outstanding": "40000", "age": "32",
                               "housing": "rent", "past_loans": "3", "ever_refused": "no", "ever_late": "no",
                               "overdue_now": "no"})
clean = scoring.score(base)["default_probability"]
late = scoring.score({**base, "ever_late": 1, "late_last_year": 1})["default_probability"]
overdue = scoring.score({**base, "overdue_now": 1})["default_probability"]
refused = scoring.score({**base, "ever_refused": 1})["default_probability"]
unknown = scoring.score({**base, "ever_late": None, "late_last_year": None, "late_over_month": None})
assert late > clean and overdue >= clean and refused >= clean, (clean, late, overdue, refused)
print(f"ok - risk rises with a bad history: clean {clean:.1%}, late last year {late:.1%}, "
      f"overdue now {overdue:.1%}, refused before {refused:.1%}, unknown {unknown['default_probability']:.1%}")

# ---- explanations and completeness speak about it plainly ----
res, _ = server.decide({**base, "ever_late": 1, "late_last_year": 1})
texts = " ".join(i["text"] for i in res["assessment"]["why"]["risk"]).lower()
assert "late" in texts, res["assessment"]["why"]
assert "paying every emi on time" in res["tips"].lower()
labels = {i["label"]: i["state"] for i in res["assessment"]["completeness"]}
assert labels["Repayment history"] == "known" and labels["Credit bureau report unavailable"] == "unavailable"
print("ok - 'an EMI was paid late in the last 12 months' and the tip appear; completeness lists the history")

# ---- a full call with history reaches a result ----
sid = new()
for text in ("1 lakh", "2 years", "30,000", "auto driver", "6 years", "1", "3,000", "40,000", "3", "no",
             "yes", "yes", "no", "no", "rent", "32"):
    t = say(sid, text)
assert t["state"] == cv.VERIFYING, t
rows = {r["field"]: r["text"] for r in t["summary"]}
assert rows["ever_late"] == "Yes" and rows["late_last_year"] == "Yes" and rows["past_loans"] == "3"
t = say(sid, "yes", "confirm")
assert t["state"] == cv.RESULT and t["result"]["assessment"], t
case = c.post("/v1/advisor-cases", headers=H, json={"session_id": sid}).json()
prof = {r["label"]: r["text"] for r in case["financial_profile"]}
assert prof["Ever paid an EMI late"] == "Yes" and prof["Loans taken before"] == "3"
print("ok - full call with history: read-back shows it, assessment runs, advisor case includes it")

print("All history tests passed.")
