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
- Always run Python as `python -I -X utf8 ...`. `-I` ignores `PYTHON*` env vars, and `-X utf8` is
  required because ₹ crashes the default cp1252 console. Add `-W ignore` to hide pandas noise.
- In PowerShell, `$env:X=''` *deletes* the variable. Use a non-empty value to override `.env`.

## Commands
```bash
python -I -X utf8 src/train.py               # both models, ~4 min (needs Kaggle CSVs in repo root)
python -I -X utf8 src/train.py --voice-only  # voice model only, ~1 min
python -I -X utf8 src/test_webhook.py        # end-to-end webhook tests with Hinglish inputs
python -I -X utf8 src/demo_applicants.py     # score three sample applicants
python -I -X utf8 src/compare_models.py      # LightGBM vs XGBoost vs CatBoost, ~30 min
python -I -X utf8 src/server.py              # webhook + /dashboard on 127.0.0.1:8000
"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://127.0.0.1:8000
```
The quick-tunnel URL changes on every restart. It must then be updated in Agent Builder.
Server env: `WEBHOOK_KEY` (from `.env`), `PORT`, `DB_PATH`. Use a separate `DB_PATH` and `PORT`
for test instances so the live dashboard stays clean.

## Layout
- `src/features.py`: feature engineering and `VOICE_FEATURES` / `VOICE_MONOTONE_UP` / `EXCLUDED`.
- `src/train.py`: trains and writes `models/voice_model.txt`, `voice_model_meta.json`, `full_model.txt`, `metrics.json` and `submission.csv`.
- `src/scoring.py`: `score()` (risk, band, what-if reasons) and `affordable_loan()` (FOIR rule).
- `src/normalize.py`: spoken values to inputs ("dhai lakh", "6 mahine", "kirana ki dukaan").
- `src/server.py`: `/v1/prescreen`, `/v1/post-call`, `/v1/applications`, `/dashboard`, `/health`. Stores data in SQLite under `data/`.
- `src/static/dashboard.html`: the live dashboard. A React + Vite rewrite was discussed but not started.
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
- Never commit the Kaggle CSVs, `.env` or `data/`. All are gitignored. The models (~8 MB) and
  `submission.csv` are committed.
