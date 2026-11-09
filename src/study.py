"""User-study kit: persona cards, per-call metrics, survey storage and the researcher report.

Participants in a study speak from a persona card instead of sharing their own finances. That keeps the
study safe (no real financial data from vulnerable people) and gives us ground truth, so extraction
accuracy can be measured exactly. See STUDY.md for the protocol.

    metrics(session)      what one call measured (timings, accuracy, read-backs, corrections...)
    report(rows)          aggregates for the researcher view and CSV export
"""
import statistics

PERSONAS = [
    {"id": "P01", "title": "Kirana shop owner", "language": "hi",
     "card": "You run a kirana shop in Lucknow. You want ₹50,000 for 1 year to stock up before Diwali. "
             "You earn about ₹30,000 a month. You've run the shop for 15 years. No other loans. "
             "You live in your own house. You are 45.",
     "truth": {"loan_amount": 50000, "tenure_months": 12, "monthly_income": 30000, "years_in_work": 15,
               "other_loans_count": 0, "housing": "own", "age": 45}},
    {"id": "P02", "title": "Auto driver", "language": "hi",
     "card": "You drive an auto in Pune. You want ₹1.5 lakh over 2 years for a new auto. You earn about "
             "₹25,000 a month. Driving for 6 years. One running loan: EMI ₹3,000, ₹40,000 still owed. "
             "You live on rent. You are 32.",
     "truth": {"loan_amount": 150000, "tenure_months": 24, "monthly_income": 25000, "years_in_work": 6,
               "other_loans_count": 1, "other_loans_emi": 3000, "other_loans_outstanding": 40000,
               "housing": "rent", "age": 32}},
    {"id": "P03", "title": "Salaried clerk", "language": "en",
     "card": "You work as an office clerk. You want ₹3 lakh over 3 years for your sister's wedding. Salary "
             "₹40,000 a month. 8 years in the job. One loan: EMI ₹8,000, ₹1.5 lakh still owed. "
             "You live with family. You are 29.",
     "truth": {"loan_amount": 300000, "tenure_months": 36, "monthly_income": 40000, "years_in_work": 8,
               "other_loans_count": 1, "other_loans_emi": 8000, "other_loans_outstanding": 150000,
               "housing": "family", "age": 29}},
    {"id": "P04", "title": "Daily-wage worker", "language": "hi",
     "card": "You work on construction sites, about ₹700 a day, 26 days a month (about ₹18,000). You want "
             "₹30,000 for a medical bill; you don't know the tenure, let Saathi decide. 3 years of work. "
             "No other loans. You live on rent. You are 38.",
     "truth": {"loan_amount": 30000, "monthly_income": 18200, "years_in_work": 3, "other_loans_count": 0,
               "housing": "rent", "age": 38}},
    {"id": "P05", "title": "Heavy borrower", "language": "en",
     "card": "You run a small tailoring business. You want ₹1 lakh over 2 years. You earn ₹35,000 a month. "
             "10 years in business. You have 3 loans: total EMI ₹12,000, about ₹9 lakh still owed. "
             "Own house. You are 41.",
     "truth": {"loan_amount": 100000, "tenure_months": 24, "monthly_income": 35000, "years_in_work": 10,
               "other_loans_count": 3, "other_loans_emi": 12000, "other_loans_outstanding": 900000,
               "housing": "own", "age": 41}},
    {"id": "P06", "title": "Asks too much", "language": "hi",
     "card": "You sell vegetables. You want ₹4 lakh over 2 years to buy a small shop. You earn ₹22,000 a "
             "month. 12 years selling. No other loans. Live with family. You are 36.",
     "truth": {"loan_amount": 400000, "tenure_months": 24, "monthly_income": 22000, "years_in_work": 12,
               "other_loans_count": 0, "housing": "family", "age": 36}},
    {"id": "P07", "title": "Nurse", "language": "en",
     "card": "You are a nurse at a private hospital. You want ₹2 lakh over 4 years for a scooter and "
             "savings. Salary ₹45,000. 5 years in the job. No other loans. On rent. You are 27.",
     "truth": {"loan_amount": 200000, "tenure_months": 48, "monthly_income": 45000, "years_in_work": 5,
               "other_loans_count": 0, "housing": "rent", "age": 27}},
    {"id": "P08", "title": "Retired teacher", "language": "hi",
     "card": "You are a retired school teacher with a pension of ₹28,000 a month. You want ₹75,000 over "
             "18 months for home repairs. No other loans. Own house. You are 63.",
     "truth": {"loan_amount": 75000, "tenure_months": 18, "monthly_income": 28000, "other_loans_count": 0,
               "housing": "own", "age": 63}},
    {"id": "P09", "title": "Changes their mind (correction)", "language": "en",
     "card": "You are a delivery rider. FIRST say you earn ₹20,000 a month; when Saathi reads your details "
             "back, say your income is wrong: it's really ₹24,000. You want ₹60,000 over 1 year. 2 years "
             "of work. No other loans. Live with family. You are 24.",
     "truth": {"loan_amount": 60000, "tenure_months": 12, "monthly_income": 24000, "years_in_work": 2,
               "other_loans_count": 0, "housing": "family", "age": 24}},
    {"id": "P10", "title": "Hindi speaker (Devanagari)", "language": "hi",
     "card": "Speak in Hindi. आप सिलाई का काम करती हैं। आपको 80 हज़ार का लोन 2 साल के लिए चाहिए। "
             "महीने की कमाई 26 हज़ार। 9 साल से काम। कोई और लोन नहीं। किराये के घर में। उम्र 34.",
     "truth": {"loan_amount": 80000, "tenure_months": 24, "monthly_income": 26000, "years_in_work": 9,
               "other_loans_count": 0, "housing": "rent", "age": 34}},
    {"id": "P11", "title": "Government peon", "language": "hi",
     "card": "You are a peon in a government office. You want dedh lakh (₹1.5 lakh) over 3 years. Salary "
             "₹32,000. 11 years in the job. One loan: EMI ₹5,000, ₹60,000 still owed. Government "
             "quarters. You are 48.",
     "truth": {"loan_amount": 150000, "tenure_months": 36, "monthly_income": 32000, "years_in_work": 11,
               "other_loans_count": 1, "other_loans_emi": 5000, "other_loans_outstanding": 60000,
               "housing": "quarters", "age": 48}},
    {"id": "P12", "title": "Student with part-time job", "language": "en",
     "card": "You are a college student with a part-time job earning ₹12,000 a month. You want ₹25,000 "
             "over 1 year for a laptop. No other loans. Live with parents. You are 20.",
     "truth": {"loan_amount": 25000, "tenure_months": 12, "monthly_income": 12000, "other_loans_count": 0,
               "housing": "family", "age": 20}},
]
BY_ID = {p["id"]: p for p in PERSONAS}
NUMERIC_TRUTH = ("loan_amount", "tenure_months", "monthly_income", "years_in_work", "other_loans_count",
                 "other_loans_emi", "other_loans_outstanding", "age")
HOUSING_KEYS = {"own": ("own", "apna", "apne", "khud"), "rent": ("rent", "kiray", "किराय", "pg"),
                "family": ("family", "parivaar", "parivar", "parents", "maa", "papa", "परिवार"),
                "quarters": ("quarter", "sarkari", "government")}


def public_personas():
    return [{"id": p["id"], "title": p["title"], "language": p["language"], "card": p["card"]} for p in PERSONAS]


def _match(field, got, want):
    if got is None:
        return False
    if field == "housing":
        return any(k in str(got).lower() for k in HOUSING_KEYS.get(want, ()))
    try:
        got, want = float(got), float(want)
    except (TypeError, ValueError):
        return False
    tol = 0.05 if field == "monthly_income" else 0.01  # daily x 26 rounding
    return abs(got - want) <= max(0.5, tol * abs(want))


def _accuracy(values, truth):
    fields = {f: _match(f, values.get(f), v) for f, v in truth.items()}
    return (sum(fields.values()) / len(fields) if fields else None), fields


def _when(s, state):
    t0 = s["trail"][0]["ts"] if s.get("trail") else None
    hit = next((t["ts"] for t in s.get("trail", []) if t["to"] == state), None)
    return round(hit - t0, 1) if hit and t0 else None


def metrics(s):
    """Everything one study call measured. Uses the persona's card as ground truth."""
    study = s.get("study") or {}
    persona = BY_ID.get(study.get("persona"))
    truth = persona["truth"] if persona else {}
    first_acc, first_fields = _accuracy(s.get("first_values") or {}, truth)
    final_acc, final_fields = _accuracy(s.get("answers") or {}, truth)
    llm = s.get("llm") or []
    corrections = sum(1 for t in s.get("trail", []) if t["to"] == "CORRECTION_REQUIRED")
    return {
        "session_id": s["id"],
        "participant": study.get("participant", ""),
        "persona": study.get("persona", ""),
        "synthetic": bool(study.get("synthetic")),
        "language": s.get("lang"),
        "reached_result": any(t["to"] == "SHOWING_RESULT" for t in s.get("trail", [])),
        "seconds_to_summary": _when(s, "VERIFYING_INFORMATION"),
        "seconds_to_result": _when(s, "SHOWING_RESULT"),
        "caller_turns": sum(1 for who, _ in s.get("history", []) if who == "Caller"),
        "first_pass_accuracy": None if first_acc is None else round(first_acc, 3),
        "final_accuracy": None if final_acc is None else round(final_acc, 3),
        "wrong_first_pass": sorted(f for f, ok in first_fields.items() if not ok),
        "wrong_final": sorted(f for f, ok in final_fields.items() if not ok),
        "readbacks": s.get("readbacks", 0),
        "corrections": corrections,
        "recoveries": s.get("recoveries", 0),
        "gemini_share": round(llm.count("gemini") / len(llm), 3) if llm else None,
        "handoff": s.get("state") == "HUMAN_HANDOFF" or bool(s.get("advisor_case")),
        "decision": (s.get("result") or {}).get("decision"),
    }


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def report(rows):
    """rows: [{"metrics": {...}, "survey": {...} or None}]. Synthetic runs are reported separately."""
    def block(rs):
        m = [r["metrics"] for r in rs]
        sv = [r["survey"] for r in rs if r.get("survey")]
        per_field = {}
        for x in m:
            persona = BY_ID.get(x["persona"])
            for f in (persona["truth"] if persona else {}):
                ok = f not in x["wrong_first_pass"]
                per_field.setdefault(f, []).append(ok)
        return {
            "calls": len(m),
            "reached_result": sum(1 for x in m if x["reached_result"]),
            "median_seconds_to_result": _median([x["seconds_to_result"] for x in m]),
            "median_seconds_to_summary": _median([x["seconds_to_summary"] for x in m]),
            "median_caller_turns": _median([x["caller_turns"] for x in m]),
            "first_pass_accuracy": _mean([x["first_pass_accuracy"] for x in m]),
            "final_accuracy": _mean([x["final_accuracy"] for x in m]),
            "field_first_pass_accuracy": {f: round(sum(v) / len(v), 3) for f, v in sorted(per_field.items())},
            "readbacks_per_call": _mean([x["readbacks"] for x in m]),
            "corrections_per_call": _mean([x["corrections"] for x in m]),
            "recoveries_per_call": _mean([x["recoveries"] for x in m]),
            "gemini_share": _mean([x["gemini_share"] for x in m]),
            "handoffs": sum(1 for x in m if x["handoff"]),
            "surveys": len(sv),
            "ease_1to5": _mean([s.get("ease") for s in sv]),
            "trust_1to5": _mean([s.get("trust") for s in sv]),
            "understood_result": {k: sum(1 for s in sv if s.get("understood") == k) for k in ("yes", "partly", "no")},
            "stt_failures_reported": sum(int(s.get("stt_failures") or 0) for s in sv),
        }
    real = [r for r in rows if not r["metrics"]["synthetic"]]
    synth = [r for r in rows if r["metrics"]["synthetic"]]
    return {"participants": block(real), "synthetic_dry_runs": block(synth),
            "note": "Synthetic dry runs test the pipeline with scripted personas; they are not user results."}


CSV_FIELDS = ["session_id", "participant", "persona", "synthetic", "language", "reached_result",
              "seconds_to_summary", "seconds_to_result", "caller_turns", "first_pass_accuracy", "final_accuracy",
              "wrong_first_pass", "wrong_final", "readbacks", "corrections", "recoveries", "gemini_share",
              "handoff", "decision", "ease", "understood", "trust", "stt_failures", "comment"]
