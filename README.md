# Saathi: voice loan pre-screening for first-time borrowers

Millions of people in India (shopkeepers, drivers, farmers, daily-wage workers) have no
credit score and can't get through English loan forms. **Saathi** lets them find out, on a short
phone call in Hindi, Hinglish, English or Kannada, whether they can get a small loan and **how
much they can safely afford**.

Built on **Gnani Agent Builder** (Prisma speech-to-text, LLM, Timbre text-to-speech), with a
LightGBM credit-risk model trained on the
[Home Credit Default Risk](https://www.kaggle.com/c/home-credit-default-risk) dataset as a mid-call webhook.

## How it works
1. The caller talks naturally: *"Mujhe 50 hazaar chahiye, kirana ki dukaan ke liye…"*
2. The Gnani agent asks follow-up questions (income, work, existing loans…) one at a time.
3. It calls our webhook, which normalises spoken answers ("dhai lakh", "6 mahine", "apna ghar"),
   scores repayment risk and applies the affordability rule.
4. The agent explains the outcome in the caller's language: pre-approved, a safer counter-offer,
   a loan-officer review, or kindly "not right now", with reasons and tips.

## Live dashboard
Open `https://<tunnel>/dashboard` (or `http://127.0.0.1:8000/dashboard`) and paste your
`WEBHOOK_KEY`. Every pre-screen appears within 3 seconds, showing:
- **what the caller said**, exactly as the agent heard it ("30 hazaar", "kiraye ka ghar")
- the **decision and offer**, plus a repayment-risk bar
- the **reasons** each way, and what the agent was told to say
- every model factor, including the ones the agent deliberately doesn't speak

**Send a test caller** buttons replay the three demo scripts without a phone call.
Post-call data from Gnani shows up as "Call ended" cards.

## The model, honestly
| Model | Features | Validation AUC |
|---|---|---|
| Full benchmark (credit bureau, payment history…) | 170 | **0.789** |
| **Voice model** (only what a caller can say) | 22 | **0.690** |

The gap is the real cost of asking questions instead of pulling bureau data. The voice model still
separates people well. On held-out data the low band defaults ~3% of the time, medium ~8% and high ~19%.

Decisions made along the way:
- **Fairness:** gender and marital status are never used. Age is used by the model but never
  spoken as a reason.
- **Removed a misleading signal:** loan size ÷ EMI looked very predictive, but it mostly reflects
  the interest rate the lender itself charged (its own risk view). A voice agent can't know that.
- **Monotone constraints:** risk can only go up as EMI-to-income and debt-to-income go up.
- **Explanations are what-if effects** (swap in a typical applicant's value and see the change),
  and the agent only speaks factors that are fair and meaningful to a borrower.
- **Loan amounts come from a lending rule, not the model:** all EMIs must stay within 50% of income
  (FOIR). In this data safer people were given bigger loans, so the model can't choose amounts.
- **Currency:** the dataset isn't in rupees. Amounts are scaled so its median monthly income equals
  ₹25,000. Ratios are unaffected.

## Why LightGBM? Comparing LightGBM, XGBoost and CatBoost
All three were trained on the same 80/20 split and features, with matched settings (learning rate
0.05, early stopping after 200 rounds, similar tree size, L2 = 5, the same monotone constraints on the
voice model). They ran on a laptop CPU with 6 threads. Reproduce with `python -I -X utf8 src/compare_models.py`.

**Voice model** (22 features, the one the agent uses)

| Model | AUC | Brier | Train time | Score 1 caller | Model size |
|---|---|---|---|---|---|
| **LightGBM** | **0.6889** | 0.0712 | **14 s** | 7.3 ms | **1.1 MB** |
| XGBoost | 0.6876 | 0.0713 | 33 s | 18.8 ms | 2.4 MB |
| CatBoost | 0.6883 | 0.0712 | 435 s | **3.1 ms** | 1.4 MB |
| Average of all three | 0.6897 | 0.0712 | | | |

**Full model** (170 features, benchmark)

| Model | AUC | Brier | Train time | Score 1 caller | Model size |
|---|---|---|---|---|---|
| LightGBM | 0.7866 | 0.0658 | **54 s** | **15.4 ms** | **1.7 MB** |
| XGBoost | 0.7865 | 0.0657 | 88 s | 48.5 ms | 3.3 MB |
| **CatBoost** | **0.7896** | **0.0656** | 992 s | 17.4 ms | 2.8 MB |
| Average of all three | 0.7900 | 0.0654 | | | |

Risk bands are just as reliable across all three. On the voice model, the low band defaults
3.2–3.5% of the time, medium 7.4–8.0% and high 16.8–17.1%, so every model is equally well
calibrated for our fixed cut-offs (5% / 12%).

What we learned:
- **On the voice model, the algorithm doesn't matter.** All three are within 0.0013 AUC, and
  averaging them gains just 0.0008. The ceiling comes from what a caller can tell us, not the
  algorithm, so better questions beat a better algorithm.
- **CatBoost wins on the full model, but slowly.** It gains 0.003 AUC, most likely from its
  handling of high-cardinality categories (e.g. 58 organisation types), but it trains 18× slower than LightGBM.
- **LightGBM is the best fit for this project.** It's the fastest to train, which mattered for
  iterating on a laptop with under 1 GB of free RAM. It's the smallest model, accuracy is tied, and
  the voice model scores a caller in ~7 ms, which is negligible on a phone call.
- **XGBoost has no edge here.** Its accuracy is the same, but it trains 2× slower, scores more
  slowly through its DMatrix interface, and produces the largest files.
- The production LightGBM models use a lower learning rate (0.03), which gives 0.6895 (voice)
  and 0.7890 (full), almost closing the gap with CatBoost.

## Project layout
```
src/features.py        feature engineering (memory-friendly chunked aggregation)
src/train.py           trains voice + full models  (python -I src/train.py [--voice-only])
src/compare_models.py  LightGBM vs XGBoost vs CatBoost on identical splits
src/scoring.py         score one applicant: risk, band, reasons, affordable loan
src/normalize.py       spoken values -> model inputs ("dhai lakh" -> 250000)
src/server.py          FastAPI webhook for Gnani Agent Builder (+ /dashboard)
src/static/dashboard.html   live pre-screen dashboard
src/test_webhook.py    end-to-end tests with Hinglish inputs
agent/                 system prompt, action body and step-by-step Agent Builder setup
models/                trained models, metadata, metrics.json, comparison.json/.md
submission.csv         Kaggle submission from the full model
```

## Run it
```bash
pip install lightgbm pandas scikit-learn fastapi uvicorn
pip install xgboost catboost   # only for src/compare_models.py
python -I -X utf8 src/train.py          # ~4 minutes; needs the Kaggle CSVs in this folder
python -I -X utf8 src/test_webhook.py   # sanity check
python -I -X utf8 src/server.py         # webhook on http://127.0.0.1:8000
```
Copy `.env.example` to `.env` and set `WEBHOOK_KEY`.

### Public URL for Agent Builder
Gnani's servers must reach the webhook, so expose port 8000 with a tunnel, e.g. Cloudflare's free
quick tunnel:
```bash
cloudflared tunnel --url http://localhost:8000
```
Use the printed `https://….trycloudflare.com` URL in Agent Builder. It changes each time you
restart the tunnel. Then follow [agent/SETUP.md](agent/SETUP.md).

## Limits
- The data is not Indian, and the voice model is a pre-screen, not a lending decision. The agent always
  says final approval needs document verification.
- Callers' answers are self-reported and unverified.
