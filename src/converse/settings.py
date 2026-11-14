"""Switches and LLM settings (tests and the server change some of them at run time)."""

import os

import llm as llm_mod


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TIMEOUT = 8.0  # transport limit for one Gemini HTTP call (the router's deadline is shorter)
# One hard deadline per LLM use in a turn (Groq, then Gemini); past it, the deterministic path answers.
LLM_DEADLINE_S = float(os.environ.get("LLM_DEADLINE_S") or 3.0)
PROMPT_VERSION = "2026-11-12"  # part of the LLM cache key: bump when a prompt or schema changes
_LLM_CACHE = llm_mod.ResponseCache()


ASK_HISTORY = True  # the repayment-history questions; older test suites switch them off to keep their flows
# "What is the loan for?" (step 1 of a lender's process): asked first, never read by the model or a rule; it
# picks the tenure hint and the documents to keep ready. Scripted suites written before it switch it off.
ASK_LOAN_PURPOSE = os.environ.get("ASK_LOAN_PURPOSE", "1").strip() != "0"
# Short call: the questions the decision rules read come first (loan, tenure, income, other loans); the ones
# only the risk model reads (work, years, history, housing, age) are asked only while some answer to them
# could still move the risk band. Older scripted suites switch it off to keep the full fixed order.
SHORT_CALL = os.environ.get("SHORT_CALL", "1").strip() != "0"
RULE_FIELDS = ("loan_amount", "tenure_months", "monthly_income", "other_loans_count", "other_loans_emi",
               "other_loans_outstanding")
# Follow-ups and the question they follow ("how many years in this work?" needs the work first).
PARENT = {"years_in_work": "work", "ever_late": "past_loans", "late_last_year": "ever_late",
          "late_over_month": "ever_late", "overdue_now": "past_loans"}
# Answers a caller could give to each model-only question, spanning its range (and "not answered").
PROBES = {
    "work": ["kirana dukaan", "private naukri", "driver", "kheti", "dihadi mazdoori", "sarkari naukri", "retired",
             "berozgar", "student", "teacher", "security guard"],
    "years_in_work": ["3 mahine", "1 saal", "3 saal", "8 saal", "20 saal"],
    "past_loans": ["0", "1", "3", "8"],
    "ever_refused": ["haan", "nahi"], "ever_late": ["haan", "nahi"], "late_last_year": ["haan", "nahi"],
    "late_over_month": ["haan", "nahi"], "overdue_now": ["haan", "nahi"],
    "housing": ["apna ghar", "kiraye pe", "parivaar ke saath", "sarkari quarter"],
    "age": ["18", "21", "26", "35", "45", "58", "70"],
}
