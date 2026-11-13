# Design decisions

Why Loan-Saathi is built the way it is. Each entry gives the context, what was decided, what was turned down,
and where the decision is enforced, so a later change can check it is not undoing one by accident. Modelling
decisions marked **(model)** are also in CLAUDE.md ("don't undo without discussing").

Figures quoted here come from scripts in the repository; the source is named each time.

---

## Who decides

### 1. The language model never decides anything
**Context.** An LLM is good at reading messy Hinglish and bad at being consistent. In early live tests Gemini
invented ages and housing the caller never mentioned.
**Decision.** The LLM only *reads* (extracts candidate answers) and *phrases* (words a reply). The question
order, every validation, the risk score, the affordability rule and the decision are deterministic code:
`server.decide()` is the model plus fixed rules.
**Turned down.** Letting the LLM run the dialogue ("agentic" flow): untestable, and a wrong turn could
change a lending outcome.
**Enforced.** The state machine in `src/converse/states.py` (`TRANSITIONS`; `transition()` raises on anything else);
`test_safety.py` ("LLM can't decide"); the LLM's result reply is dropped if it contains an amount that is
not in the decision (`_amounts_ok`).

### 2. Every extracted value quotes the caller
**Decision.** An LLM answer is kept only with an exact quote of the caller's words (`converse.quoted`);
otherwise it is dropped and logged. The quote is stored as evidence beside the value (`s["said"]`).
**Why.** It turns "the model thinks you said" into "you said", and stops invented values.
**Enforced.** `quoted()` in `_extract`; `tests/test_pii.py` also checks a quote containing a masked span is
never stored.

### 3. Nothing is assessed before the caller confirms the read-back
**Decision.** Values stay "validated" until the caller says yes to "Here's what I understood"; only then are
they "confirmed". `require_confirmed()` guards the assessment; risky values (bare numbers, ranges, -ty
words, large amounts) are read back before they are even stored.
**Enforced.** `test_confirmation.py`, `test_states.py` (forcing the state to CONFIRMED still raises).

### 4. No approval claims
**Decision.** Wording says "eligible for further review" / "pre-screening, not a final approval", never
"approved". Decision codes (`pre_approved` ...) stay unchanged for Agent Builder, but applicant-facing text
never uses them. People are referred to a "loan advisor", never an "officer".
**Enforced.** `test_safety.py` wording scan.

## The model (see CLAUDE.md for detail)

### 5. No gender or marital status (model)
`CODE_GENDER` and `NAME_FAMILY_STATUS` are excluded from both models, for fairness; Saathi never asks.

### 6. No loan term or raw annuity in the voice model (model)
Credit ÷ annuity mostly encodes the rate the lender priced in (its own risk view); a voice agent can't know
it.

### 7. Monotone in burden and debt (model)
Risk can only rise with EMI-to-income and debt-to-income. The earlier model scored huge debts as *safer*
(a confounding artefact); since retraining, 0 of 1,680 synthetic checks fail (`models/validation_report.json`).

### 8. Explanations are what-if effects, and only some are spoken (model)
Each reason is "swap in a typical value and re-predict", not SHAP (its signs misled on constrained
features). Only factors in `scoring.SPOKEN` are said; loan size and income level are confounded and stay on
the dashboard. **Age is never spoken as a reason.**

### 9. Loan amounts come from a rule, not the model (model)
The offer is the largest loan that keeps all EMIs within 50% of income (FOIR). A hard rule on top: owing
more than 24 months of income on other loans means "not right now", whatever the model says.

## Rules and configuration

### 10. The lending rules live in one file
**Context.** The FOIR limit, comfort level, debt limit, assumed interest, accepted ranges and amount limits
were constants in four modules, two of them duplicated.
**Decision.** `config/policy.yaml`, loaded and checked by `src/policy.py` at start-up; a missing or
out-of-range value stops the server with a message naming the key. Modules keep their constant names, read
from the policy.
**Not moved.** The risk-band thresholds (<5% low, <12% medium): they are chosen at training time and stored
with the model (`models/voice_model_meta.json`), so they always match the model in use.
**Enforced.** `tests/test_policy.py` (every module reads the same values; a stricter FOIR or debt limit in the
file changes the decision; a bad file stops start-up).

## The conversation

### 11. Language models in order, then our own parser
**Decision.** Groq first, then Gemini, then the deterministic Hindi/English parser, under one 3-second
deadline per use, one retry on invalid JSON, a response cache, and at most one LLM call per turn
(`src/llm/`, `converse._llm`). The provider is recorded for every turn (`llm_trail`).
**Why.** The parser alone scores 64/64 on the dev set and 51/54 on the held-out set, so the call never
depends on an LLM being up. A hung provider now costs a turn 3.0 s instead of 8.0 s
(`reports/stall_benchmark.json`). Groq's free tier allows 8,000 tokens a minute per model, a few extraction
turns, so two Groq models are configured (`GROQ_MODEL`), each with its own limit.
**Measured.** Caller stops → first audio of the reply: median 2.17 s with Groq vs 3.09 s with Gemini
(`reports/latency_benchmark.json`, scripted 13-turn call on one machine).

### 12. Personal identifiers never reach an LLM
**Decision.** `src/pii.py` masks phone, Aadhaar, PAN, IFSC, account and card numbers, email, date of birth,
a PIN code and a name the caller gives, in `converse._llm` (every provider) and once at the start of
extraction (so our parser never reads a phone number as an amount, and stored quotes carry no
identifiers). Amounts, counts and years pass unchanged.
**Turned down.** Masking only in the LLM request: the parser and the stored evidence would still see the
numbers.
**Enforced.** `tests/test_pii.py`.

### 13. One message can answer several questions
"kirana dukaan 6 saal se, ek bike loan hai 3000 EMI" fills work, years, loan count and EMI, whatever was
asked; Saathi never asks again for something just said. A side mention never *replaces* a free-text answer
already given (a housing answer "sarkari quarter" once overwrote the work; corrections go through the
correction flow). **Enforced.** `tests/test_multifield.py`.

### 14. A short call, with the same decisions
**Decision.** The questions the rules read come first (loan, tenure, income, other loans). A question only
the risk model reads (work, years, history, housing, age) is asked only while some answer to it could still
move the risk band: every possible answer to the remaining questions is scored in one batch (~0.1 s), plus
all-riskiest and all-safest together. Skipped questions are listed as `not_needed`; changing a rule answer
brings them back; a follow-up never comes before its question; if scoring fails, everything is asked.
**Measured.** `src/bench_call_length.py`: mean caller turns to the result 13.71 → 12.35 on 200 seeded random
applicants (12.17 → 10.83 on the 12 study personas), with the same decision, band and offer for all 215
applicants (`reports/call_length.json`).
**Trade-off.** The explanation can differ (an unasked question can't appear as a reason), and the risk
percentage can move within the band. The decision does not.

### 15. Recovery: spell out the options, then offer a way out
One failed answer: the question again with its options or examples, plus buttons on the web call (buttons send
ordinary words, so the same parser, quote and read-back apply). Two: an optional question is offered a skip
or the advisor; a required one goes to error recovery. A precise refusal ("18 to 100") gets one more go.
**Enforced.** `tests/test_recovery_loop.py`.

### 16. Work is read back as a category
Free-text work maps to a category the caller hears back (shop owner / self-employed, salaried, driver,
farmer, daily-wage worker, ...), with their own words kept beside it. The model still gets its own
categories, unchanged for every keyword it knew. **Enforced.** `tests/test_work_category.py`.

### 17. The call has no dead end
After the result Saathi offers a what-if, the advisor, or a new check. A finished call can reopen only into
the what-if simulator (`COMPLETED -> WHAT_IF`); nothing is collected or assessed again in an ended call.
**Enforced.** `tests/test_after_call.py`, `test_states.py`.

### 18. "Don't know" stays unknown
History questions answered "pata nahi" are skipped and stay None; training blanked 10% of answers so the
model has a learned "not answered" branch. Never turn an unknown into a "no". (Short-call skips use the same
branch.)

## Evaluation

### 19. Dev and held-out extraction sets are reported apart
The 64 dev cases (`tests/extraction_cases.json`) are the ones the parser was fixed against, so 64/64 there
says little. 54 held-out cases (`tests/extraction_heldout.json`) were written and committed (0cbb4d8) after
the parser was frozen and before any run, and are never used to tune it: first run 51/54
(`reports/extraction_benchmark_heldout.json`). Fixing a held-out miss makes it a dev case; a fresh held-out
set is then needed.

### 20. Benchmarks and tests never call an LLM by accident
Tests and the parser benchmark set `GROQ_DISABLED=1` (an empty key can't do it on Windows, where "" unsets
the variable for child processes and the key in `.env` returned). Live-LLM runs are opt-in
(`LIVE_LLM_TESTS=1`, `bench_latency.py`).

## Deployment

### 21. Free hosting, honest limits
The API runs on a free Render web service (Docker); Hugging Face Docker Spaces turned out to need a paid
plan. A free service sleeps after 15 idle minutes, so the dashboard is also a static site on Render's CDN
(`loan-saathi-web`), which loads at once and pings `/health` on load to wake the API. `/dashboard/` on the API
still works. See `deploy/DEPLOY.md`.

## Open questions
- Devanagari number words for years and hundreds ("पंद्रह साल से", "बारह सौ") are not parsed (held-out misses).
- No figures from real first-time borrowers yet: every measurement above is scripted or synthetic.
- The per-network public call limit (20 a day) is low for a demo room; raising it is a product call.
