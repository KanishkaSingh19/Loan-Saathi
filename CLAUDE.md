# CLAUDE.md

Loan Saathi: a voice loan pre-screening agent for first-time borrowers in India. A Gnani Agent
Builder voice agent (Prisma STT, LLM, Timbre TTS) collects answers on a call and calls our FastAPI
webhook mid-call. The webhook scores repayment risk with a LightGBM model trained on Kaggle's Home
Credit Default Risk data, applies an affordability rule and returns a decision plus a reply hint
that the agent speaks. See README.md for the pitch and results, and agent/SETUP.md for the Agent
Builder configuration.

## Environment
- Windows 11 with PowerShell, Python 3.12. The laptop has 7 GB RAM and often under 1 GB free, so keep
  data loading lean (float32, `usecols`, chunked aggregation) and don't load all the side tables at once.
- The project lives in `C:\Users\ks608\Desktop\Gnani_project` (earlier names: home-credit-default-risk,
  Loan-Saathi). Use its virtualenv: `.venv\Scripts\python.exe` (deps in `requirements.txt`;
  `requirements-compare.txt` adds xgboost/catboost). `python` below means that interpreter.
- Always run Python as `python -I -X utf8 ...`. `-I` ignores `PYTHON*` env vars, and `-X utf8` is
  required because ₹ crashes the default cp1252 console. Add `-W ignore` to hide pandas noise.
- In PowerShell, `$env:X=''` *deletes* the variable. Use a non-empty value to override `.env`.

## Commands
```bash
python -I -X utf8 src/train.py               # both models, ~4 min (needs the Kaggle CSVs, see below)
python -I -X utf8 src/train.py --voice-only  # voice model only, ~1 min
python -I -X utf8 src/test_webhook.py        # end-to-end webhook tests with Hinglish inputs
python -I -X utf8 src/test_speech_api.py     # speech proxy route tests (mocked, no API calls)
python -I -X utf8 src/test_converse.py       # web-call conversation tests (Gemini mocked)
python -I -X utf8 src/test_states.py         # web-call state machine: confirm, correct, ambiguity, contradictions
python -I -X utf8 src/test_assessment.py     # applicant assessment: provenance, wording, why, safety check
python -I -m ruff check src                  # Python lint (requirements-dev.txt)
cd dashboard; npm test; npm run lint; npm run typecheck; npm run build   # frontend checks
python -I -X utf8 src/test_postgres.py       # real Postgres check (needs DATABASE_URL; removes its own rows)
python -I -X utf8 src/demo_applicants.py     # score three sample applicants
python -I -X utf8 src/compare_models.py      # LightGBM vs XGBoost vs CatBoost, ~30 min
python -I -X utf8 src/server.py              # webhook + /dashboard on 127.0.0.1:8000
"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://127.0.0.1:8000
```
The quick-tunnel URL changes on every restart. It must then be updated in Agent Builder.
Env (from `.env`, see `.env.example`): `WEBHOOK_KEY` (required, protects every `/v1` route),
`GNANI_STT_API_KEY` / `GNANI_TTS_API_KEY` (or shared `GNANI_API_KEY`), `PORT`, `DB_PATH`, and
`HOME_CREDIT_DATA_DIR` (Kaggle CSVs; empty or unset means `data/kaggle/`). Use a separate
`DB_PATH` and `PORT` for test instances so the live dashboard stays clean. Never expose the server
on a tunnel without a `WEBHOOK_KEY`.

Free deployment (`deploy/DEPLOY.md`): the API runs as a Hugging Face Docker Space (`Dockerfile`,
port 7860), stores data in Neon Postgres via `DATABASE_URL` (the Space's disk is wiped on restart),
and allows the dashboard's origin through `ALLOWED_ORIGINS`. The dashboard deploys to Vercel from
`dashboard/` with `VITE_API_BASE` set to the Space URL; `vite.config.js` uses base `/` when
`VERCEL` is set. SQL in `server.py` goes through `_run()` with `?` placeholders, which are
adapted for Postgres, so keep queries portable between SQLite and Postgres.

The Kaggle CSVs are not on this machine at the moment. Running the agent doesn't need them (the
trained models are in `models/`); only retraining or the model comparison does.

## Layout
- `src/features.py`: feature engineering and `VOICE_FEATURES` / `VOICE_MONOTONE_UP` / `EXCLUDED`.
- `src/train.py`: trains and writes `models/voice_model.txt`, `voice_model_meta.json`, `full_model.txt`, `metrics.json` and `submission.csv`.
- `src/scoring.py`: `score()` (risk, band, what-if reasons) and `affordable_loan()` (FOIR rule).
- `src/normalize.py`: spoken values to inputs ("dhai lakh", "6 mahine", "kirana ki dukaan").
- `src/converse.py`: the browser web call. Gemini (`GEMINI_API_KEY`, default model
  `gemini-3.5-flash-lite`, REST `generateContent` with `responseJsonSchema`) extracts answers and
  phrases replies; our code fixes the question order, validates every value with `normalize.py`, and
  decides with `server.decide()`. Every Gemini answer must carry an exact quote of the caller's words
  (`quoted()`), or it is dropped: without this, Gemini invented ages and housing in live tests.
  Gemini's question is used only if it asks the field we expect; its
  result reply only if every number in it appears in `reply_hint`. Any Gemini failure falls back to
  scripted questions. Sessions live in memory (single instance, 30-minute expiry).
  The call is an explicit state machine (`TRANSITIONS`; `transition()` raises `InvalidTransition`):
  COLLECTING_PROFILE → VERIFYING_INFORMATION → CONFIRMED → ASSESSING_RISK → CHECKING_AFFORDABILITY →
  SHOWING_RESULT → NEXT_STEPS → COMPLETED, plus CORRECTION_REQUIRED, ERROR_RECOVERY, HUMAN_HANDOFF and
  WHAT_IF (reserved, nothing enters it yet). Fields are "validated" until the caller confirms the
  read-back, then "confirmed"; `require_confirmed()` guards the assessment. Risky values wait in
  `pending` for a read-back (`check()`: bare numbers, ranges, two amounts, -teen/-ty words, large
  values, Gemini figures that differ from the quote); a differing value for an answered field
  becomes a "which one?" question, never an overwrite. Button presses arrive as `action`
  (confirm, change, correct:<field>, yes, no, choose:<n>, handoff). `normalize.amounts()` parses
  compound Indian amounts ("1 lakh 50 hazaar", "saadhe teen lakh", "2 crore 50 lakh").
- `src/assessment.py`: the applicant-facing assessment attached to every decision as
  `result["assessment"]`: figures with `source`/`source_text` provenance, the financial picture,
  why (rules + direction-checked model reasons), completeness and the safety check. `safety_pre()`
  runs before the model (in `decide()` and before ASSESSING_RISK in the web call); a "fail" pauses
  with what's needed. Wording says "estimated" / "eligible for further review", never "approved";
  decision codes (`pre_approved` etc.) are unchanged for Agent Builder.
- `src/server.py`: `/v1/prescreen`, `/v1/post-call`, `/v1/applications`, `/v1/converse`, `/v1/stt` and `/v1/tts`
  (pass-throughs to Gnani's speech APIs), `/dashboard`, `/health`. Stores data in SQLite under `data/`.
- Evon (Gnani's LLM) has no public API; Agent Builder offers Gnani's Pampa Go / Pampa Priority (not
  Gemini), which is why the Gemini path is our own browser web call alongside the Agent Builder phone agent.
  Don't build on an Evon endpoint unless the user confirms they have access.
- `dashboard/`: the React 18 + Vite 5 live dashboard. The server mounts `dashboard/dist` at
  `/dashboard`, so rebuild with `npm run build` after UI changes; `dist/` and `node_modules/`
  are gitignored. Node is at `C:\Program Files\nodejs` and may not be on PATH in older shells.
  Render caller-supplied text only as React text (no `dangerouslySetInnerHTML`). The "Web call"
  view (`CallScreen.jsx`, `recorder.js`) records 16 kHz WAV, then calls /v1/stt → /v1/converse → /v1/tts;
  typing works without speech keys. Never return a value from a `useEffect` arrow (new Chromium's
  `scrollIntoView` returns a Promise, which crashed the app).
- `agent/`: the system prompt, the action body JSON and the step-by-step Agent Builder setup.

## Deliberate modelling decisions (don't undo without discussing)
- **No `LOAN_TERM_MONTHS` or raw `AMT_ANNUITY` in the voice model.** Credit ÷ annuity mostly encodes
  the interest rate the lender priced in (its own risk view), and default rates jump around
  between term buckets. A voice agent can't know the rate.
- **`CODE_GENDER` and `NAME_FAMILY_STATUS` are excluded** from both models, for fairness.
- **Monotone constraints:** risk can only rise with `ANNUITY_TO_INCOME` and `DEBT_TO_INCOME`.
- **Explanations are what-if effects** (swap in the typical value and re-predict), not LightGBM
  `pred_contrib`, because SHAP signs were misleading for the constrained features.
- **Only factors in `scoring.SPOKEN` are said to callers.** Loan size, purchase price and income level
  are confounded (safer people got bigger loans), so they stay in `all_factors` for the dashboard
  only. Age is never spoken as a reason.
- **Loan amounts come from the FOIR rule** (all EMIs ≤ 50% of income), not the model.
- **Debt rule (`server.DEBT_LIMIT_MONTHS = 24`):** owing more than 24 months of income on other loans
  means `not_eligible_now` (`policy_rule: debt_over_limit`), whatever the model says. The model is
  not monotone in `BUREAU_ACTIVE_DEBT` and scores huge debts as *safer* (₹36L owed on ₹40k/month:
  6.1% vs 8.2% with no debt), a confounding artifact. A proper fix is a monotone constraint on
  that feature, which needs the Kaggle CSVs to retrain.
- **Currency:** dataset amounts are *monthly* and not in rupees. `TYPICAL_MONTHLY_INCOME_RS = 25_000`
  sets the scale so the dataset's median income maps to ₹25k/month.
- Risk bands are <5% low, <12% medium and otherwise high (`train.BANDS`, stored in the model metadata).

## Webhook contract
Agent Builder fills the request body from conversation variables, so any value may be a messy
string, empty, "null" or an unfilled `{{placeholder}}`. Normalise leniently, and treat unknown
values as missing. Responses must stay **flat with simple keys** (`decision`, `reply_hint`,
`offer_amount_text`, …), because Gnani's docs don't say how responses map back to the agent.
`reply_hint` is English guidance for the agent's LLM, not a script. Keep it free of risk
percentages and of any mention of a model.

## Git
- Remote: github.com/KanishkaSingh19/Loan-Saathi (branch `main`).
- Test locally, then commit locally. Never push unless the user asks in that turn.
- Never commit the Kaggle CSVs, `.env` or `data/`. All are gitignored. The models (~8 MB) and
  `submission.csv` are committed.
