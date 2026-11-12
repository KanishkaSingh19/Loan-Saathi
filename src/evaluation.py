"""Evaluation Lab: the evidence behind Loan-Saathi, for an evaluator.

Nothing here is written by hand except the descriptions. Every number comes from a file the training
and validation scripts wrote (models/*.json), every test result from an actual run of the suites
(reports/evaluation_run.json, written by `run`), and every safeguard is checked to exist in the code
before it is listed. Where something isn't measured, the evidence says "Not currently measured."

    python -I -X utf8 src/evaluation.py run      # run every suite, record what passed (~5 min)
    GET /v1/evaluation                           # the evidence the dashboard's Evaluation Lab shows
"""
import importlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
RUN_FILE = ROOT / "reports" / "evaluation_run.json"
NOT_MEASURED = "Not currently measured."


def _read(name):
    p = MODELS / name
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _num(value, source, note=""):
    return {"value": value, "source": source, **({"note": note} if note else {})}


def _missing(note=""):
    return {"value": None, "text": NOT_MEASURED, **({"note": note} if note else {})}


# ---------- model ----------

def model_evidence():
    m, v, cmp, hist = _read("metrics.json"), _read("validation_report.json"), _read("comparison.json"), \
        _read("experiment_features.json")
    meta = _read("voice_model_meta.json") or {}
    out = {"purpose": ("A pre-screening risk estimate for a voice call: how often people with similar answers had "
                       "trouble repaying in past records. It is not a lending decision engine. Loan amounts come "
                       "from a fixed affordability rule, and a lender would verify documents before deciding.")}
    if not m:
        out["error"] = "models/metrics.json is missing."
        return out
    voice, full = m["voice"], m["full"]
    out["dataset"] = {"name": "Home Credit Default Risk (Kaggle)", "train_rows": m["n_train"],
                      "validation_rows": m["n_valid"], "calibration_rows": m.get("n_calibration"),
                      "default_rate": m["default_rate"], "source": "models/metrics.json"}
    out["voice"] = {
        "what": f"The model the call uses: {len(meta.get('features', []))} features a caller can answer.",
        "auc": _num(voice["auc"], "models/metrics.json"),
        "brier": _num(voice["calibration"]["brier"], "models/metrics.json",
                      "Raw predictions; an isotonic calibration map was tried and not kept."),
        "ece": _num(voice["calibration"]["ece"], "models/metrics.json"),
        "calibration_kept": voice.get("calibration_kept"),
        "deciles": voice["calibration"]["deciles"],
        "bands": voice["bands"],
        "monotone_up": voice.get("monotone_up", []),
        "previous_auc": _num(voice["previous_model"]["auc"], "models/metrics.json",
                             "Same model before the repayment-history questions were added."),
    }
    full_cmp = (cmp or {}).get("Full model", {}).get("results", {}).get("LightGBM", {})
    out["full"] = {
        "what": (f"Benchmark only, never used on a call: {full.get('n_features')} features including credit-bureau "
                 "scores and past applications a voice agent can't ask for."),
        "auc": _num(full["auc"], "models/metrics.json"),
        "brier": _missing("Not stored for the current full model. The earlier comparison run below has one."),
        "bands": full["bands"],
    }
    if full_cmp:
        out["full"]["brier_comparison_run"] = _num(full_cmp["brier"], "models/comparison.json",
                                                   "LightGBM in the earlier comparison run, not the current file.")
    if cmp:
        out["comparison"] = {
            "note": (f"Run on an earlier voice model ({cmp['Voice model']['n_features']} features, before the "
                     "repayment-history questions), so its AUCs are lower than the current model's. It is why "
                     "LightGBM was chosen; it was not repeated for the current model."),
            "source": "models/comparison.json",
            "tables": {name: [{"model": k, "auc": r["auc"], "brier": r["brier"], "log_loss": r.get("log_loss"),
                               "train_seconds": r.get("train_seconds"), "predict_one_ms": r.get("predict_one_ms")}
                              for k, r in section["results"].items()]
                       for name, section in cmp.items() if isinstance(section, dict) and "results" in section},
        }
    if hist:
        out["history_questions"] = {"baseline_auc": hist["baseline"], "with_all_auc": hist["all"]["auc"],
                                    "source": "models/experiment_features.json"}
    if v:
        out["behaviour"] = {
            "source": "models/validation_report.json",
            "synthetic_profiles": v["synthetic_profiles"],
            "debt_steps_checked": v["debt_monotonicity"]["steps_checked"],
            "debt_model_violations": v["debt_monotonicity"]["model_violations"],
            "debt_end_to_end_violations": v["debt_monotonicity"]["end_to_end_violations"],
            "history_checks": v["history_monotonicity"]["checks"],
            "history_violations": v["history_monotonicity"]["violations"],
            "band_flip_rate_income_5pct": v["band_flip_rate_income_5pct"],
            "gates": v["gates"], "gates_failed": v["gates_failed"],
            "not_measured": v["not_measured"],
        }
    out["indian_accuracy"] = _missing("No labelled Indian repayment outcomes are available.")
    return out


# ---------- safety architecture: each safeguard is checked to exist before it is listed ----------

SAFEGUARDS = [
    {"name": "Quote-gated extraction", "where": ("converse", "quoted"),
     "what": "Gemini must return the caller's exact words for every value it extracts; a value it can't quote "
             "is dropped."},
    {"name": "Deterministic parsing", "where": ("normalize", "number"),
     "what": "Amounts (lakh, crore, hazaar, Devanagari), tenures and yes/no answers are parsed by our own code, "
             "not by the language model."},
    {"name": "Validation and read-backs", "where": ("converse", "check"),
     "what": "Negative, out-of-range and impossible values are refused with a reason; unclear or very large "
             "amounts are read back; two different values are asked about."},
    {"name": "User confirmation before assessment", "where": ("converse", "require_confirmed"),
     "what": "Nothing is assessed until the caller confirms the profile Saathi read back."},
    {"name": "Deterministic affordability rule", "where": ("scoring", "affordable_loan"),
     "what": "All EMIs must stay within 50% of income (FOIR). Loan amounts come from this rule, not the model."},
    {"name": "Debt-over-limit rule", "where": ("server", "DEBT_LIMIT_MONTHS"),
     "what": "Owing more than 24 months of income on other loans means 'not right now', whatever the model "
             "says."},
    {"name": "Language-model fallback", "where": ("converse", "LLM_DEADLINE_S"),
     "what": "Groq first, then Gemini, under one hard deadline per use; if neither answers in time, Saathi's own "
             "parser and scripted questions carry the call. A turn waits on the LLM at most once."},
    {"name": "Identifiers masked before any LLM", "where": ("pii", "mask"),
     "what": "Phone, Aadhaar, PAN, IFSC, account and card numbers, email, date of birth, PIN code and a name the "
             "caller gives are masked before any text reaches Groq or Gemini; amounts and counts pass unchanged. "
             "A quote containing a masked span is never stored."},
    {"name": "Assessment failure recovery", "where": ("converse", "_enter_recovery"),
     "what": "If the assessment fails (for example the risk model can't load), the call goes to error recovery; "
             "no result is made up."},
    {"name": "No LLM-controlled loan decision", "where": ("server", "decide"),
     "what": "The decision is server.decide(): the risk model plus fixed rules. Gemini never chooses a state, a "
             "number or a decision, and its replies can't contain amounts the caller didn't say."},
    {"name": "Closed by default", "where": ("server", "_check_key"),
     "what": "Without a WEBHOOK_KEY every /v1 route refuses; callers' income and debts are never served openly."},
    {"name": "Per-call tokens for public callers", "where": ("server", "_owned_session"),
     "what": "Anyone can start a call, but each call has its own secret token: a visitor reaches only their own "
             "call and result. Lists of other callers need the staff key. Per-network limits bound API spend."},
]


def safeguards():
    out = []
    for g in SAFEGUARDS:
        mod, attr = g["where"]
        try:
            present = hasattr(importlib.import_module(mod), attr)
        except Exception:  # noqa: BLE001
            present = False
        out.append({"name": g["name"], "what": g["what"], "where": f"src/{mod}.py: {attr}", "verified": present})
    return out


# ---------- test coverage: categories -> real checks in the suites ----------
# Each check names a suite and text from one of its "ok" lines; it counts as passed only if a recorded run
# of that suite printed that line and the suite exited cleanly.

CATALOG = [
    ("Financial extraction", [
        ("Lakh / crore amounts", [("test_confirmation.py", "lakh/crore values parse correctly"),
                                  ("test_states.py", "lakh / crore / Hindi / Hinglish number parsing"),
                                  ("tests/test_normalize.py", None)]),
        ("Ambiguous amounts", [("test_confirmation.py", "ambiguous amounts are asked about"),
                               ("test_states.py", "ambiguous numbers are read back"),
                               ("test_simulate.py", "ambiguous and bare amounts asked about")]),
        ("Invalid values", [("test_confirmation.py", "invalid values are refused"),
                            ("test_safety.py", "negative, out-of-range and impossible values are refused"),
                            ("test_simulate.py", "invalid amounts are refused")]),
        ("Contradictory answers", [("test_confirmation.py", "contradictory income"),
                                   ("test_states.py", "contradicting income and debt answers are asked about"),
                                   ("test_safety.py", "contradictions are asked about")]),
        ("Bugs found by the extraction benchmark", [("test_voice.py", "parser regressions from the extraction benchmark"),
                                                    ("test_voice.py", "Gemini regressions")]),
        ("Code-mixed speech", [("test_advisor.py", "mujhe 3 lakh ka loan chahiye"),
                               ("test_history.py", "yes/no and counts in English, Hinglish and Devanagari")]),
    ]),
    ("Safety", [
        ("Excessive debt", [("test_safety.py", "debt over 24 months of income"),
                            ("test_model.py", "risk never falls as debt rises"),
                            ("test_simulate.py", "debt rule, paused and incomplete assessments")]),
        ("Missing critical information", [("test_confirmation.py", "missing required field"),
                                          ("test_states.py", "a missing required field leads to error recovery")]),
        ("Assessment before confirmation", [("test_confirmation.py", "assessment before confirmation is impossible"),
                                            ("test_safety.py", "no assessment without the applicant's confirmation"),
                                            ("test_states.py", "assessment refused before confirmation")]),
        ("Model unavailable", [("test_evaluation.py", "risk model unavailable"),
                               ("test_states.py", "assessment failure -> error recovery -> retry")]),
        ("No LLM-controlled decision", [("test_safety.py", "Gemini can't skip questions, invent answers"),
                                        ("test_states.py", "Gemini intent fallback for the summary")]),
        ("No approval claims or lender names", [("test_safety.py", "no approval claims anywhere"),
                                                ("test_safety.py", "no lender names")]),
        ("Protected attributes", [("test_safety.py", "gender and marital status never used")]),
        ("Public calls reach only their own data", [("test_access.py", "only their own token reaches their call"),
                                                    ("test_access.py", "still need the staff key"),
                                                    ("test_access.py", "per-network call limits")]),
        ("Affordability rule", [("test_safety.py", "every suggested amount keeps all EMIs within 50%")]),
    ]),
    ("Voice", [
        ("Live streaming speech-to-text", [("test_voice.py", "live STT relay")]),
        ("Streaming replies (speech starts on the first chunk)", [("test_voice.py", "streaming TTS")]),
        ("Numbers spoken naturally", [("test_voice.py", "numbers are spoken the way people say them")]),
        ("Switching language mid-call", [("test_voice.py", "'talk in English' (either script)")]),
        ("Latency recorded from real calls", [("test_voice.py", "measured browser turns are stored")]),
        ("Speech-to-text failure", [("test_evaluation.py", "speech-to-text failures"),
                                    ("test_advisor.py", "clear recovery: repeat prompts, server glitches")]),
        ("Language-model fallback", [("test_safety.py", "a slow Gemini is cut off at the deadline"),
                                     ("tests/test_llm.py", None)]),
        ("Identifiers masked before the language model", [("tests/test_pii.py", None)]),
        ("Misunderstood answers", [("test_advisor.py", "recovery offers the advisor"),
                                   ("test_states.py", "live-run regressions")]),
        ("WhatsApp: text, buttons and voice notes", [("test_whatsapp.py", "a full pre-screen over WhatsApp"),
                                                      ("test_whatsapp.py", "voice notes"),
                                                      ("test_whatsapp.py", "webhook verification and Meta's signature")]),
        ("Daily advisor digest", [("test_whatsapp.py", "daily advisor digest")]),
        ("Speech recognition accuracy on real callers", None),
        ("Text-to-speech quality", None),
    ]),
]


def _suite_text(name):
    p = ROOT / (name if name.startswith("tests/") else f"src/{name}")
    return p.read_text(encoding="utf-8") if p.exists() else ""


def coverage(run=None):
    suites = (run or {}).get("suites", {})
    groups = []
    for title, items in CATALOG:
        rows = []
        for label, checks in items:
            if checks is None:
                rows.append({"label": label, "status": "not_measured", "text": NOT_MEASURED, "checks": []})
                continue
            out = []
            for suite, match in checks:
                rec = suites.get(suite)
                if rec is None:
                    state = "not_run"
                elif not rec["passed"]:
                    state = "failed"
                elif match is None:
                    state = "passed"
                else:
                    state = "passed" if any(match in line for line in rec["ok_lines"]) else "not_found"
                out.append({"suite": suite, "check": match or f"{rec['tests'] if rec else 'all'} unit tests",
                            "state": state})
            states = {c["state"] for c in out}
            status = "failed" if "failed" in states or "not_found" in states else \
                "not_run" if "not_run" in states else "passed"
            rows.append({"label": label, "status": status, "checks": out})
        groups.append({"title": title, "items": rows})
    return groups


# ---------- failure cases and limitations (each tied to where it is recorded) ----------

def failure_cases():
    v = _read("validation_report.json") or {}
    debt = v.get("debt_monotonicity", {})
    return [
        {"title": "The risk model rated more debt as safer",
         "found": "Live testing",
         "what": "A caller owing ₹36.5 lakh on a ₹40,000 monthly income scored lower risk (6.1%) than the same "
                 "caller with no debt (8.2%). In the Home Credit data, large bureau debts went with wealthier "
                 "borrowers, so the model learned 'more debt, safer'.",
         "measured_before": "On 240 synthetic applicants, risk fell as debt rose in 520 of 1,680 checks and the "
                            "final decision improved with more debt in 38 cases (README.md; that run's report "
                            "was not kept).",
         "fix": "A hard rule on top of the model: more than 24 months of income owed means 'not right now', "
                "whatever the model says. Later the model was retrained monotone in debt.",
         "measured_after": (f"{debt.get('model_violations')} of {debt.get('steps_checked')} checks, "
                            f"{debt.get('end_to_end_violations')} decision changes "
                            "(models/validation_report.json).") if debt else NOT_MEASURED,
         "still": "The rule stays: the model's risk levels off at high debt."},
        {"title": "A bigger loan could look lower-risk in the simulator",
         "found": "Simulator design (guard in simulate.compare)",
         "what": "The model isn't constrained in loan size, so re-scoring a larger loan can give a lower risk band, "
                 "which would present borrowing more as safer.",
         "fix": "The simulator never shows a bigger loan as lower risk: it says 'not recalculated' and keeps the "
                "original estimate.",
         "measured_after": "Covered by src/test_simulate.py.", "still": ""},
        {"title": "Two spoken amounts were silently reduced to the first",
         "found": "Found while building the What if? screen",
         "what": "'Agar 2 ya 3 lakh loon' simulated ₹2 lakh without asking; a bare '40' skipped to next steps.",
         "fix": "Both are now asked about; nothing is simulated until one amount is clear.",
         "measured_after": "Covered by src/test_simulate.py.", "still": ""},
        {"title": "The extraction benchmark found parser bugs on its first run",
         "found": "Extraction benchmark (tests/extraction_cases.json, parser only)",
         "what": "'six months' was stored as 72 months; 'never taken one' as 1 past loan; 'do loan liye hain pehle' "
                 "(two loans before) went to the running-loans field; 'aath hazaar mahina' was read back "
                 "needlessly because 'hazaar mahina' looked like a tenure.",
         "measured_before": "59 of 64 on the first run (one miss was our own wrong expectation: 'sixty thousand' "
                            "is read back on purpose because -ty words are often misheard).",
         "fix": "Month words keep months as months; a 'never/none' answer with no digits is zero; 'N loan' answers "
                "the question asked; the tenure pattern skips amounts.",
         "measured_after": "64 of 64 on the same cases. These are the cases that found the bugs, so this is not "
                           "an independent accuracy figure.",
         "still": "The cases are hand-written, not real callers' speech."},
        {"title": "With Gemini, a spoken range was stored as one amount",
         "found": "Extraction benchmark with Gemini",
         "what": "For '30-35 hazaar' Gemini quoted only '35 hazaar' and also claimed '30-35 hazaar' as the loan "
                 "amount; the range check then set ₹30,000 aside as 'the loan' and stored ₹35,000 as the income. "
                 "Gemini also gave 'sixty thousand', said to the income question, to the loan, and sent a bare "
                 "'6' for 'six months' (read as 6 years).",
         "measured_before": "60 of 64 with Gemini answering 60 turns (reports/extraction_benchmark_gemini.json "
                            "at the time; not kept).",
         "fix": "One spoken amount answers one question (the field asked wins); a range in our parser's span is "
                "always checked even if Gemini quotes one end; the caller's own unit words win over Gemini's "
                "number.",
         "measured_after": "See Measurements: the current Gemini run and how many turns Gemini answered.",
         "still": "Gemini's free tier was often overloaded; turns it doesn't answer fall back to the parser."},
        {"title": "Loan size looked predictive, but it was the lender's own risk view",
         "found": "Model development",
         "what": "Loan size divided by EMI mostly reflected the interest rate the lender charged, which a voice agent "
                 "can't know.",
         "fix": "Removed from the voice model. Loan amounts come from the affordability rule, not the model.",
         "measured_after": "", "still": ""},
    ]


LIMITATIONS = [
    {"title": "Home Credit data is not Indian lending data",
     "detail": "The model learned from an international consumer-credit dataset. Amounts were rescaled so its "
               "median income maps to ₹25,000 a month; ratios are unaffected, but borrower behaviour may differ."},
    {"title": "Accuracy on Indian borrowers is not measured",
     "detail": "There is no labelled Indian repayment data. The synthetic-profile checks test behaviour (for "
               "example, more debt never lowers risk), not accuracy."},
    {"title": "Borrower information is self-reported",
     "detail": "Income, work, existing loans and repayment history are what the caller says. No income proof, "
               "employment check or credit-bureau report is used."},
    {"title": "This is a pre-screening prototype",
     "detail": "It estimates whether a loan looks affordable and how risky similar profiles were. It doesn't "
               "approve loans and has no lender behind it."},
    {"title": "Final lending decisions require verification",
     "detail": "A lender must verify identity, income and credit history (e.g. Aadhaar, PAN, bank statements, "
               "bureau report) before any decision."},
    {"title": "Latency was measured with a synthetic caller on one machine",
     "detail": "The benchmark voices the caller with Gnani TTS and runs next to the server. Real phones, networks "
               "and speech will add delay; real browser turns are listed separately once people use the call."},
    {"title": "Speech recognition quality is not measured here",
     "detail": "Speech-to-text and text-to-speech come from Gnani. The tests check how failures are handled, not "
               "recognition accuracy on real callers."},
]


# ---------- measurements: latency and extraction accuracy (each from a run that wrote a file) ----------

REPORTS = ROOT / "reports"


def _report(name):
    p = REPORTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _stats(values):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    mid = len(v) // 2
    median = v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2
    return {"median": round(median), "p90": v[min(len(v) - 1, int(round(0.9 * (len(v) - 1))))], "n": len(v)}


def test_env():
    """The suites' environment: no live Groq calls (they are slow, rate-limited and not deterministic). Set
    LIVE_LLM_TESTS=1 to let suites use the real GROQ_API_KEY from .env."""
    env = dict(os.environ)
    if not env.get("LIVE_LLM_TESTS"):
        env["GROQ_DISABLED"] = "1"
    return env


def measurements(live=None):
    """Latency (scripted benchmark and real browser turns) and extraction accuracy (parser and Gemini)."""
    stages = [("speech_end_to_first_audio_ms", "Caller stops → first audio of the reply"),
              ("speech_end_to_transcript_ms", "Caller stops → final transcript (includes the 0.6 s pause window)"),
              ("transcript_to_reply_ms", "Transcript → Saathi's reply"),
              ("reply_to_first_audio_ms", "Reply → first audio (streaming TTS)")]
    bench = _report("latency_benchmark.json")
    latency = {"stages": [{"key": k, "label": label} for k, label in stages]}
    if bench:
        latency["benchmark"] = {"ts": bench["ts"], "turns": bench["turns"], "method": bench["method"],
                                "summary": bench["summary"], "source": "reports/latency_benchmark.json",
                                "gemini_turns": sum(1 for r in bench["rows"] if r.get("llm") == "gemini"),
                                "llm_turns": {p: sum(1 for r in bench["rows"] if r.get("llm") == p)
                                              for p in sorted({r.get("llm") or "fallback" for r in bench["rows"]})}}
    stall = _report("stall_benchmark.json")
    if stall:  # the language model hanging (mocked): how long a turn waits before the parser answers
        latency["stall"] = {label: {"turn_ms": r["turn_ms"], "ts": r["ts"]} for label, r in stall.items()}
        latency["stall_source"] = "reports/stall_benchmark.json (src/bench_stall.py)"
    live = [r for r in (live or []) if r.get("mode") == "live"]
    latency["browser"] = ({k: _stats(r.get(k) for r in live) for k, _ in stages} | {"turns": len(live)}
                          if live else None)
    extraction = {}
    for mode, name in (("parser", "extraction_benchmark.json"), ("gemini", "extraction_benchmark_gemini.json")):
        r = _report(name)
        if r:
            extraction[mode] = {"ts": r["ts"], "cases": r["cases"], "correct": r["correct"], "by_kind": r["by_kind"],
                                "gemini_turns": r.get("gemini_turns", 0), "source": f"reports/{name}",
                                "wrong": [{"field": w["field"], "said": w["said"], "got": w["got"],
                                           "expect": w["expect"]} for w in r["wrong"]]}
    cases_file = ROOT / "tests" / "extraction_cases.json"
    extraction["about"] = (json.loads(cases_file.read_text(encoding="utf-8"))["about"]
                           if cases_file.exists() else "")
    return {"latency": latency, "extraction": extraction,
            "real_users": _missing("No study with real first-time borrowers has been run yet (STUDY.md has the kit).")}


def evidence(live=None):
    run = json.loads(RUN_FILE.read_text(encoding="utf-8")) if RUN_FILE.exists() else None
    summary = None
    if run:
        suites = run["suites"].values()
        summary = {"ts": run["ts"], "commit": run.get("commit", ""), "python": run.get("python", ""),
                   "suites": len(run["suites"]), "suites_passed": sum(1 for s in suites if s["passed"]),
                   "checks_passed": sum(len(s["ok_lines"]) + (s.get("tests") or 0) for s in suites if s["passed"]),
                   "skipped": run.get("skipped", [])}
    return {"model": model_evidence(), "safeguards": safeguards(), "coverage": coverage(run),
            "measurements": measurements(live),
            "run": summary, "failure_cases": failure_cases(), "limitations": LIMITATIONS,
            "not_measured_text": NOT_MEASURED}


# ---------- recording a real run ----------

OK_LINE = re.compile(r"^ok\b.*")


def _git_commit():
    """The commit the suites ran on, marked when the working tree had uncommitted changes to tracked files."""
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no", "--", ".",
                                ":(exclude)reports"], cwd=ROOT,  # its own output doesn't count
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return f"{head} + uncommitted changes" if head and dirty else head
    except Exception:  # noqa: BLE001
        return ""


def run(out=RUN_FILE):
    suites, skipped = {}, []
    for p in sorted((ROOT / "src").glob("test_*.py")):
        if p.name == "test_postgres.py" and not os.environ.get("DATABASE_URL", "").strip():
            skipped.append({"suite": p.name, "why": "needs a real DATABASE_URL"})
            continue
        t0 = time.time()
        r = subprocess.run([sys.executable, "-I", "-X", "utf8", "-W", "ignore", str(p)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=900, cwd=ROOT, env=test_env())
        lines = [ln.strip() for ln in r.stdout.splitlines() if OK_LINE.match(ln.strip())]
        suites[p.name] = {"passed": r.returncode == 0, "ok_lines": lines, "seconds": round(time.time() - t0, 1)}
        print(f"{'PASS' if r.returncode == 0 else 'FAIL'} {p.name} ({len(lines)} checks)", flush=True)
    # The pytest files in tests/ (test_suites.py only re-runs the src/ suites above, so it is skipped here).
    for p in sorted((ROOT / "tests").glob("test_*.py")):
        if p.name == "test_suites.py":
            continue
        name = f"tests/{p.name}"
        t0 = time.time()
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:warnings", name], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600, cwd=ROOT, env=test_env())
        m = re.search(r"(\d+) passed", r.stdout)
        suites[name] = {"passed": r.returncode == 0, "ok_lines": [], "tests": int(m.group(1)) if m else 0,
                        "seconds": round(time.time() - t0, 1)}
        print(f"{'PASS' if r.returncode == 0 else 'FAIL'} {name}", flush=True)
    record = {"ts": time.time(), "commit": _git_commit(), "python": platform.python_version(),
              "suites": suites, "skipped": skipped}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    return record


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if sys.argv[1:] == ["run"]:
        rec = run()
        bad = [k for k, v in rec["suites"].items() if not v["passed"]]
        print(f"Recorded {len(rec['suites'])} suites in {RUN_FILE.relative_to(ROOT)}; failed: {bad or 'none'}")
        sys.exit(1 if bad else 0)
    print(json.dumps(evidence(), indent=1, ensure_ascii=False)[:4000])
