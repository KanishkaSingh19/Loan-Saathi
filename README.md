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

## Project layout
```
src/features.py        feature engineering (memory-friendly chunked aggregation)
src/train.py           trains voice + full models  (python -I src/train.py [--voice-only])
src/scoring.py         score one applicant: risk, band, reasons, affordable loan
src/normalize.py       spoken values -> model inputs ("dhai lakh" -> 250000)
src/server.py          FastAPI webhook for Gnani Agent Builder
src/test_webhook.py    end-to-end tests with Hinglish inputs
agent/                 system prompt, action body and step-by-step Agent Builder setup
models/                trained models, metadata, metrics.json
submission.csv         Kaggle submission from the full model
```

## Run it
```bash
pip install lightgbm pandas scikit-learn fastapi uvicorn
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
