"""How long a call is, and whether the short call changes any decision.

    python -I -X utf8 src/bench_call_length.py            # demo applicants + personas + a seeded random sweep

Each applicant answers whatever Saathi asks, one field per turn, through the real /v1/converse (parser only,
no LLM), and confirms the read-back. The same applicant runs twice: the full fixed question order
(SHORT_CALL off) and the short call (rule questions first; model-only questions only while an answer could
still move the risk band). Counted: caller turns until the result, including the confirmation. Compared:
decision, risk band and offer. Results go to reports/call_length.json.
"""
import json
import os
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.update(WEBHOOK_KEY="len-key", DATABASE_URL="", DB_PATH=str(Path(tempfile.mkdtemp()) / "len.db"),
                  GEMINI_API_KEY="", GROQ_DISABLED="1", ADVISOR_WEBHOOK_URL="")

import converse as cv  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

OUT = ROOT / "reports" / "call_length.json"
H = {"X-Webhook-Key": "len-key"}

# The dashboard's three demo applicants (dashboard/src/constants.js SAMPLES) and the twelve user-study
# personas (src/study_dryrun.py), one answer per question. Questions they don't cover get a plain default.
DEMO = {
    "Demo: kirana owner": {"loan_amount": "50,000", "tenure_months": "1 saal", "monthly_income": "30 hazaar",
                           "work": "kirana ki dukaan", "years_in_work": "15 saal", "other_loans_count": "0",
                           "housing": "apna ghar", "age": "45"},
    "Demo: asks too much": {"loan_amount": "3 lakh", "tenure_months": "2 saal", "monthly_income": "25 hazaar",
                            "work": "private company me sales", "years_in_work": "6 saal",
                            "other_loans_count": "1", "other_loans_emi": "2000",
                            "other_loans_outstanding": "40 hazaar", "past_loans": "2", "housing": "kiraye ka ghar",
                            "age": "34"},
    "Demo: heavy debt": {"loan_amount": "2 lakh", "tenure_months": "2 saal", "monthly_income": "15000",
                         "work": "construction mazdoor", "years_in_work": "6 mahine", "other_loans_count": "3",
                         "other_loans_emi": "4000", "other_loans_outstanding": "1.5 lakh", "past_loans": "4",
                         "housing": "rent", "age": "22"},
}
PERSONAS = {
    "P01": {"loan_amount": "50 hazaar", "tenure_months": "1 saal", "monthly_income": "30 hazaar",
            "work": "kirana ki dukaan hai", "years_in_work": "15 saal se", "other_loans_count": "koi loan nahi",
            "housing": "apna ghar hai", "age": "45 saal"},
    "P02": {"loan_amount": "dedh lakh", "tenure_months": "2 saal", "monthly_income": "25 hazaar",
            "work": "auto chalata hoon", "years_in_work": "6 saal", "other_loans_count": "haan ek loan hai",
            "other_loans_emi": "3 hazaar ki kist", "other_loans_outstanding": "40 hazaar baaki hai",
            "past_loans": "2", "housing": "kiraye pe", "age": "32"},
    "P03": {"loan_amount": "3 lakh", "tenure_months": "3 years", "monthly_income": "40,000",
            "work": "I work as an office clerk", "years_in_work": "8 years", "other_loans_count": "one loan",
            "other_loans_emi": "8,000 EMI", "other_loans_outstanding": "1.5 lakh still owed", "past_loans": "2",
            "housing": "with my family", "age": "29"},
    "P04": {"loan_amount": "30 hazaar", "tenure_months": "pata nahi, aap bataiye", "monthly_income": "18,200",
            "work": "construction mazdoor", "years_in_work": "3 saal", "other_loans_count": "nahi",
            "housing": "kiraye ka kamra", "age": "38"},
    "P05": {"loan_amount": "1 lakh", "tenure_months": "2 years", "monthly_income": "35,000",
            "work": "tailoring business", "years_in_work": "10 years", "other_loans_count": "3 loans",
            "other_loans_emi": "total EMI 12,000", "other_loans_outstanding": "about 9 lakh still owed",
            "past_loans": "5", "housing": "own house", "age": "41"},
    "P06": {"loan_amount": "4 lakh", "tenure_months": "2 saal", "monthly_income": "22 hazaar",
            "work": "sabzi bechta hoon", "years_in_work": "12 saal", "other_loans_count": "nahi",
            "housing": "parivaar ke saath", "age": "36"},
    "P07": {"loan_amount": "2 lakh", "tenure_months": "4 years", "monthly_income": "45,000",
            "work": "nurse at a private hospital", "years_in_work": "5 years", "other_loans_count": "no loans",
            "housing": "on rent", "age": "27"},
    "P08": {"loan_amount": "75 hazaar", "tenure_months": "18 mahine", "monthly_income": "28 hazaar",
            "work": "retired teacher hoon", "other_loans_count": "koi loan nahi", "housing": "apna ghar",
            "age": "63 saal"},
    "P09": {"loan_amount": "60,000", "tenure_months": "1 year", "monthly_income": "20,000",
            "work": "delivery rider", "years_in_work": "2 years", "other_loans_count": "no",
            "housing": "with family", "age": "24"},
    "P10": {"loan_amount": "80 हज़ार", "tenure_months": "2 साल", "monthly_income": "26 हज़ार",
            "work": "सिलाई का काम", "years_in_work": "9 साल से", "other_loans_count": "कोई लोन नहीं",
            "housing": "किराये के घर में", "age": "34 साल"},
    "P11": {"loan_amount": "dedh lakh", "tenure_months": "teen saal", "monthly_income": "32 hazaar",
            "work": "sarkari daftar mein chaprasi", "years_in_work": "11 saal", "other_loans_count": "ek loan",
            "other_loans_emi": "5 hazaar EMI", "other_loans_outstanding": "60 hazaar baaki", "past_loans": "2",
            "housing": "sarkari quarter", "age": "48"},
    "P12": {"loan_amount": "25,000", "tenure_months": "1 year", "monthly_income": "12,000",
            "work": "student with a part-time job", "other_loans_count": "no", "housing": "with my parents",
            "age": "20"},
}
DEFAULTS = {"past_loans": "0", "ever_refused": "nahi", "ever_late": "nahi", "late_last_year": "nahi",
            "late_over_month": "nahi", "overdue_now": "nahi", "tenure_months": "2 saal", "years_in_work": "5 saal",
            "housing": "apna ghar", "age": "35", "work": "private naukri"}


def random_applicants(n, seed=7):
    rng = random.Random(seed)
    out = {}
    for i in range(n):
        income = rng.choice([8000, 12000, 15000, 20000, 25000, 30000, 40000, 55000, 80000, 120000])
        loans = rng.choice([0, 0, 0, 1, 1, 2, 3])
        a = {"loan_amount": str(rng.choice([25000, 50000, 100000, 150000, 200000, 300000, 500000, 800000])),
             "tenure_months": rng.choice(["1 saal", "2 saal", "3 saal", "5 saal"]),
             "monthly_income": str(income), "other_loans_count": str(loans),
             "work": rng.choice(cv.PROBES["work"]), "years_in_work": rng.choice(cv.PROBES["years_in_work"]),
             "housing": rng.choice(cv.PROBES["housing"]), "age": rng.choice(["22", "28", "35", "44", "55", "62"])}
        if loans:
            a["other_loans_emi"] = str(int(income * rng.choice([0.05, 0.15, 0.3, 0.45])))
            a["other_loans_outstanding"] = str(int(income * rng.choice([1, 4, 10, 20, 30])))
            a["past_loans"] = str(loans + rng.choice([0, 1, 3]))
        else:
            a["past_loans"] = rng.choice(["0", "0", "1", "3"])
        for k in ("ever_refused", "ever_late", "late_last_year", "late_over_month", "overdue_now"):
            a[k] = rng.choice(["nahi", "nahi", "nahi", "haan"])
        out[f"R{i:03d}"] = a
    return out


def run_call(c, answers, short):
    cv.SHORT_CALL = short
    t = c.post("/v1/converse", headers=H, json={"language": "hi"}).json()
    sid, turns, asked = t["session_id"], 0, []
    while turns < 60:
        if t["state"] == cv.RESULT or t["done"]:
            break
        if t["state"] == cv.VERIFYING:
            say, action = "Haan, sab sahi hai", "confirm"
        elif t.get("pending"):
            say, action = "Haan", "yes"
        else:
            k = t["asking"]
            asked.append(k)
            say, action = answers.get(k) or DEFAULTS.get(k, "nahi"), ""
        t = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": say, "action": action}).json()
        turns += 1
    r = t.get("result") or {}
    return {"turns": turns, "asked": asked, "not_needed": t.get("not_needed", []), "state": t["state"],
            "decision": r.get("decision"), "band": r.get("risk_band"), "offer": r.get("offer_amount"),
            "risk_percent": r.get("risk_percent")}


def main():
    c = TestClient(server.app)
    groups = {"demo": DEMO, "personas": PERSONAS, "random": random_applicants(200)}
    rows, summary = [], {}
    for group, people in groups.items():
        full_t, short_t, same_dec, same_band, same_offer = [], [], 0, 0, 0
        for name, answers in people.items():
            full, short = run_call(c, answers, False), run_call(c, answers, True)
            full_t.append(full["turns"])
            short_t.append(short["turns"])
            same_dec += full["decision"] == short["decision"]
            same_band += full["band"] == short["band"]
            same_offer += full["offer"] == short["offer"]
            rows.append({"group": group, "name": name, "full": full, "short": short})
        n = len(people)
        summary[group] = {"applicants": n, "full_turns_median": statistics.median(full_t),
                          "short_turns_median": statistics.median(short_t),
                          "full_turns_mean": round(statistics.mean(full_t), 2),
                          "short_turns_mean": round(statistics.mean(short_t), 2),
                          "same_decision": same_dec, "same_band": same_band, "same_offer": same_offer}
        print(f"{group:9} n={n:3}  turns full {statistics.median(full_t)} -> short {statistics.median(short_t)} "
              f"(mean {statistics.mean(full_t):.1f} -> {statistics.mean(short_t):.1f}); same decision {same_dec}/{n}, "
              f"band {same_band}/{n}, offer {same_offer}/{n}", flush=True)
    cv.SHORT_CALL = True
    diffs = [r for r in rows if r["full"]["decision"] != r["short"]["decision"]]
    for r in diffs[:10]:
        print("DIFF", r["name"], r["full"]["decision"], "->", r["short"]["decision"], "not asked:",
              r["short"]["not_needed"])
    OUT.write_text(json.dumps({"ts": time.time(), "method": __doc__.strip().split("\n\n")[1], "summary": summary,
                               "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
