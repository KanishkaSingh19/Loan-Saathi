"""Extraction accuracy: hand-written answers, each with its correct outcome, through the real call path.

    python -I -X utf8 src/bench_extraction.py            # Saathi's own parser (no Gemini)
    python -I -X utf8 src/bench_extraction.py --gemini   # with Gemini, if GEMINI_API_KEY is set

Each case starts a call whose earlier answers are already filled in and which is asking the case's
question, sends the answer once through /v1/converse, and compares what happened (value stored,
read back, asked which, or not stored) with the expected outcome in tests/extraction_cases.json.
The cases were written by the developers, not taken from real callers. Results go to
reports/extraction_benchmark[_gemini].json and the Evaluation Lab.
"""
import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEMINI = "--gemini" in sys.argv
sys.path.insert(0, str(ROOT / "src"))
os.environ["WEBHOOK_KEY"] = "bench-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "bench.db")
os.environ["DATABASE_URL"] = ""
if not GEMINI:
    os.environ["GEMINI_API_KEY"] = ""
# Groq never answers here: "parser" must mean the parser alone, and "--gemini" measures Gemini alone.
os.environ["GROQ_DISABLED"] = "1"

import converse as cv  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PREFILL = {"loan_amount": 300000, "tenure_months": 24, "monthly_income": 40000, "work": "kirana shop",
           "years_in_work": 10, "other_loans_count": 1, "other_loans_emi": 8000, "other_loans_outstanding": 150000,
           "past_loans": 2, "ever_refused": 0, "ever_late": 0, "late_last_year": 0, "late_over_month": 0,
           "overdue_now": 0, "housing": "own house"}


def outcome(t, field):
    if field in t["answers"]:
        return t["answers"][field]
    p = next((p for p in (t.get("pending") or []) if p.get("field") == field), None) if isinstance(
        t.get("pending"), list) else (t["pending"] if t.get("pending") and t["pending"].get("field") == field else None)
    if p:
        return "read_back" if p["kind"] == "confirm" else "choose" if p["kind"] == "choose" else p["kind"]
    return "not_stored"


def correct(got, expect):
    options = expect if isinstance(expect, list) else [expect]
    for e in options:
        if isinstance(e, str) and e.startswith("contains:"):
            if isinstance(got, str) and e.split(":", 1)[1] in got.lower():
                return True
        elif isinstance(e, (int, float)) and isinstance(got, (int, float)) and abs(got - e) < 0.5:
            return True
        elif got == e:
            return True
    return False


def main():
    if GEMINI and not server.GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY is not set: run without --gemini, or set the key.")
    cases = json.loads((ROOT / "tests" / "extraction_cases.json").read_text(encoding="utf-8"))
    c = TestClient(server.app)
    rows = []
    def ask(case):
        s, _ = cv.start(case["lang"])
        for k in cv.KEYS:
            if k == case["field"]:
                break
            if k in PREFILL and not cv._skip(k, s["answers"]):
                cv._set(s, k, PREFILL[k], str(PREFILL[k]))
        s["asking"] = case["field"]
        return c.post("/v1/converse", headers={"X-Webhook-Key": "bench-key"},
                      json={"session_id": s["id"], "text": case["said"]}).json()

    for case in cases["cases"]:
        t = ask(case)
        for _ in range(3 if GEMINI else 0):      # Gemini busy or slow: retry, so the run measures Gemini
            if t.get("llm") == "gemini":
                break
            time.sleep(12)
            t = ask(case)
        got = outcome(t, case["field"])
        ok = correct(got, case["expect"])
        rows.append({**case, "got": got, "correct": ok, "llm": t.get("llm", "")})
        print(f"{'ok ' if ok else 'BAD'} {case['field']:<24} {case['said']!r:<36} -> {got!r}"
              f" (expected {case['expect']!r})", flush=True)
        if GEMINI:
            time.sleep(4)  # stay inside the free tier's rate limit
    by_kind = Counter()
    right_kind = Counter()
    for r in rows:
        group = r["kind"].split(":")[0].split(" (")[0]
        by_kind[group] += 1
        right_kind[group] += r["correct"]
    report = {
        "ts": time.time(), "mode": "gemini" if GEMINI else "parser",
        "about": cases["about"], "cases": len(rows), "correct": sum(r["correct"] for r in rows),
        "gemini_turns": sum(1 for r in rows if r["llm"] == "gemini"),
        "by_kind": {k: {"cases": by_kind[k], "correct": right_kind[k]} for k in sorted(by_kind)},
        "wrong": [r for r in rows if not r["correct"]], "rows": rows,
    }
    out = ROOT / "reports" / f"extraction_benchmark{'_gemini' if GEMINI else ''}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n{report['correct']} of {report['cases']} correct ({report['mode']}); "
          f"Gemini answered {report['gemini_turns']} turns")


if __name__ == "__main__":
    main()
