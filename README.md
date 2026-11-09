# Saathi: voice loan pre-screening for first-time borrowers

Millions of people in India (shopkeepers, drivers, farmers, daily-wage workers) have no
credit score and can't get through English loan forms. **Saathi** lets them find out, on a short
phone call in Hindi, Hinglish or English, whether they can get a small loan and **how
much they can safely afford**.

Built on **Gnani Agent Builder** (Prisma speech-to-text, LLM, Timbre text-to-speech), with a
LightGBM credit-risk model trained on the
[Home Credit Default Risk](https://www.kaggle.com/c/home-credit-default-risk) dataset as a mid-call webhook.

## How it works
1. The caller talks naturally: *"Mujhe 50 hazaar chahiye, kirana ki dukaan ke liye…"*
2. The Gnani agent asks follow-up questions (income, work, existing loans…) one at a time.
3. It calls our webhook, which normalises spoken answers ("dhai lakh", "6 mahine", "apna ghar"),
   scores repayment risk and applies the affordability rule.
4. The agent explains the outcome in the caller's language: eligible for further review, a smaller
   suggested amount, an advisor review, or kindly "not right now", with reasons and tips.

## Web call with Gemini (in the browser)
The dashboard's **Web call** tab lets anyone try Saathi from a browser, with no phone line:

```
mic → Gnani Prisma (speech-to-text) → Gemini → our model + affordability rule → Gemini → Gnani Timbre (text-to-speech) → speaker
```

- **Gemini understands and phrases**: it pulls answers out of free speech ("ek lakh chahiye,
  kamaata hoon 40 hazaar" fills two answers at once), answers side questions, and words each reply
  in the caller's style.
- **Our code decides**: the question order, the risk score, the loan amount and the EMI come from
  our model and the 50% rule, never from the LLM. Gemini must quote the caller's exact words for
  every answer it extracts, and answers it can't quote are dropped, so it can't invent an age or a
  house. Every value is then checked by our parser (an age of 7 is rejected). Gemini's question is used only if it asks what we expect next,
  and its result reply only if every number in it matches our decision. Otherwise Saathi uses a
  scripted line.
- **A state machine, not a free chat**: the call moves through fixed states
  (collecting → verifying → confirmed → assessing risk → checking affordability → result → next steps),
  plus correction, error recovery and handoff to a person. Gemini interprets; our code picks every
  transition.
- **"Here's what I understood"**: before any assessment Saathi reads back the financial profile
  (loan, tenure, income, EMI, debt) and the screen shows it with **Confirm** / **Change** buttons. Each
  answer is *validated* until the caller confirms it; the model never runs on an unconfirmed profile.
- **Corrections without restarting**: "my income is wrong" asks for just the income, changes only
  that field, and reads the profile back again.
- **Risky numbers are checked, not guessed**: a bare "40" for an income ("did you mean ₹40,000?"), a
  range ("40-50 hazaar"), two amounts in one answer, "fourteen/forty", very large values, or a Gemini
  figure that differs from the caller's words are read back. A new value that contradicts an earlier
  one ("actually I earn ₹60,000") is asked about, never silently overwritten. Negative or zero income,
  a tenure over 10 years and an EMI above the income are refused with a reason.
- **It never dies on the LLM**: without a Gemini key, or if Gemini is slow or down, the call carries
  on with scripted Hinglish/English questions and our own Hindi number parser.
- Typing works too, for testing without speech keys. Every finished call appears in the live feed.

Agent Builder (the phone agent) can't use Gemini (it offers Gnani's Pampa, OpenAI and Claude), so
the web call is a second front door to the same model, not a replacement. Callers' answers are
sent to Google when `GEMINI_API_KEY` is set.

## Your Loan-Saathi Assessment
Every finished pre-screen comes with an applicant-facing assessment (`src/assessment.py`), shown at
the end of a web call and at the top of each live-feed entry:
- **At a glance**: estimated risk (low / medium / high), estimated affordability (the most a new EMI
  can be while all EMIs stay within 50% of income), the requested loan and its EMI, and a suggested
  range (all EMIs within 40% to 50% of income, never above what was asked). One plain sentence says
  whether the loan is *within your estimated repayment capacity* or *may put too much pressure on
  your monthly income*. It never says "approved": the best outcome is *eligible for further review*.
- **Your financial picture**: one bar splits monthly income into existing EMIs, the new EMI and what
  is left, with the 50% line marked.
- **Why this result?**: helping and risk factors from the affordability rule, the debt rule and the
  model's speakable what-if reasons. Gender and marital status are never used; age is never a reason;
  a model reason that runs against the applicant's own number (e.g. "little is owed" for someone
  who owes more than typical) is dropped.
- **Number provenance**: every figure carries its source ("Provided by you", "Calculated by
  Loan-Saathi affordability rules", "Estimated by Loan-Saathi risk model"...) in the data itself.
- **Assessment completeness**: what is known, and what a pre-screening can't know (credit history,
  income proof, employment verification). No made-up "AI score".
- **Safety check**: before the model runs, deterministic checks (required information, valid values,
  caller confirmation, contradictions resolved, consistent answers) must pass, or the assessment
  pauses and says what is needed. Debt and EMI affordability checks are shown with the result.

## What if you changed the loan?
Under the explanation, the simulator lets the applicant change the loan amount (slider or typed) and
the tenure (3 to 120 months) and see, side by side with their original request: EMI, estimated total
repayment, room left in their estimated affordability, and the estimated risk band, with arrows
judged from the borrower's side. Three scenarios sit on top: **safer option** (all EMIs within 40%
of income), **your request**, and **higher burden** (half as much again), each with *Try this option*.
Every number comes from `POST /v1/simulate`, which runs the same safety check, model, affordability
rule and debt rule as the assessment; the saved request never changes. EMIs use the model's assumed
18% yearly interest. Everything is labelled *Simulation — not a lender offer*. On a web call the
applicant can also ask by voice: "What if I take ₹2 lakh?", "What if I choose 5 years?", "What is the
maximum EMI I can afford?", "EMI kam rakhni hai".

## Talk to a Loan Advisor, listen, and recover
- **Talk to a Loan Advisor** (button on the assessment, or say "talk to a loan advisor" / "advisor se
  baat") saves a structured advisor case: income, existing EMI and debt (with whether the applicant
  confirmed them), requested loan and tenure, estimated risk and affordable EMI, the current
  simulation (recomputed on the server), the applicant's question, a conversation summary and
  assessment completeness. Cases appear under **Advisor cases**. This is a prototype: the applicant is
  told that no advisor is notified automatically.
- **Listen to this result** reads a short, deterministic summary with Gnani Timbre, in English or
  Hinglish.
- **Code-mixed speech** such as "meri salary 40 hazaar hai", "mujhe 3 lakh ka loan chahiye" and "EMI kam
  rakhni hai" works even without Gemini. Supported languages: Hindi, Hinglish and English.
- **Recovery, never stack traces**: unclear answers get "Could you repeat your monthly income?", a
  failed transcription asks to try again, a server error keeps the call and offers **Try again**, a
  model failure pauses with a retry, and repeated trouble offers the advisor.
- `src/test_safety.py` checks the product's boundaries end to end: no approval claims or lender names
  anywhere an applicant can see or hear, no protected attributes, the LLM never decides, numbers are
  validated, confirmation is required, affordability and the debt rule hold, contradictions are asked.

## Live dashboard (React + Vite)
Open `https://<tunnel>/dashboard` (or `http://127.0.0.1:8000/dashboard`) and paste your
`WEBHOOK_KEY`. It has a live feed on the left and the selected caller's full breakdown on the
right, and every pre-screen appears within 3 seconds. The breakdown shows:
- **what the caller said**, exactly as the agent heard it ("30 hazaar", "kiraye ka ghar")
- the **decision and offer** (amount, tenure, EMI), plus a repayment-risk meter with the band cut-offs
- the **reasons** each way, and what the agent was told to say
- every model factor, including the ones the agent deliberately doesn't speak

**Follow live** opens each new caller automatically, which is handy while recording a demo. The filter
chips narrow the feed by decision, and the **test caller** buttons replay the three demo scripts
without a phone call. Post-call data from Gnani shows up as "Call ended" entries. Light and dark
themes follow your system, and the layout works on phones.

```bash
cd dashboard
npm ci
npm run build   # the webhook serves dashboard/dist at /dashboard
npm run dev     # optional: hot-reload UI on :5173, proxying /v1 to the webhook on :8000
npm test        # view-logic tests (node --test)
npm run lint    # ESLint
npm run typecheck   # TypeScript over the JS/JSX (checkJs)
```

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

### A hard rule where the model is wrong
Live testing found a caller owing ₹36.5 lakh on a ₹40,000 income scored as *lower* risk (6.1%) than the
same caller with no debt (8.2%). In the Home Credit data, large bureau debts went with wealthier
borrowers, so the model learned "more debt, safer". Saathi therefore applies a plain rule on top:
**owing more than 24 months of income on other loans means "not right now"**, with that reason and
"pay down existing loans first" spoken to the caller.

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
requirements.txt         Python API + LightGBM training dependencies
src/features.py        feature engineering (memory-friendly chunked aggregation)
src/train.py           trains voice + full models  (python -I src/train.py [--voice-only])
src/compare_models.py  LightGBM vs XGBoost vs CatBoost on identical splits
src/scoring.py         score one applicant: risk, band, reasons, affordable loan
src/normalize.py       spoken values -> model inputs ("dhai lakh" -> 250000)
src/server.py          FastAPI webhook, Gnani STT/TTS proxy, and dashboard server
dashboard/             React + Vite live dashboard (src/App.jsx, src/components/, src/styles.css)
src/test_webhook.py    end-to-end tests with Hinglish inputs
agent/                 system prompt, action body and step-by-step Agent Builder setup
models/                trained models, metadata, metrics.json, comparison.json/.md
data/kaggle/           default location for the Kaggle Home Credit CSVs (not in git)
```

## Run it

Python 3.10+ and Node.js 20.19+ are required. The trained LightGBM models are included, so the API
can run before retraining. The Home Credit CSVs already found on this machine are in
`C:\Users\ks608\Desktop\Loan-Saathi\data\kaggle`; `.env.example` points to that folder without
copying the multi-gigabyte dataset into this project.

In PowerShell:
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
pip install -r requirements.txt
python -I -X utf8 src/test_webhook.py
python -I -X utf8 src/server.py
```

Edit `.env` and add your Gnani STT and TTS API keys. You can use separate
`GNANI_STT_API_KEY` / `GNANI_TTS_API_KEY` values or set one shared `GNANI_API_KEY`.
Set `WEBHOOK_KEY` to a long random value; speech routes require it in the
`X-Webhook-Key` request header. Keep `.env` private and never commit it. The local API is at
`http://127.0.0.1:8000`, with interactive documentation at `http://127.0.0.1:8000/docs`.

### Gnani speech endpoints

The server proxies audio requests so Gnani credentials stay on the backend:

| Route | Input | Output |
|---|---|---|
| `POST /v1/stt` | Multipart `audio_file`, `language_code` (default `hi-IN`), `format` (default `transcribe`) | Gnani transcript JSON |
| `POST /v1/tts` | JSON `text`, `language`, `voice`, `model`, optional `speed` | WAV audio |

Example STT request:
```powershell
curl.exe -X POST http://127.0.0.1:8000/v1/stt `
  -H "X-Webhook-Key: <your WEBHOOK_KEY>" `
  -F "audio_file=@sample.wav" -F "language_code=hi-IN" -F "format=transcribe"
```

Example TTS request:
```powershell
curl.exe -X POST http://127.0.0.1:8000/v1/tts `
  -H "X-Webhook-Key: <your WEBHOOK_KEY>" `
  -H "Content-Type: application/json" `
  -d '{"text":"Namaste, aap kaise hain?","language":"hi-IN","voice":"Nalini","model":"timbre-v2.5"}' `
  --output speech.wav
```

### Train or retrain the model

`HOME_CREDIT_DATA_DIR` in `.env` selects the folder containing the Kaggle CSV files. The supplied
configuration uses the dataset already found on the Desktop; change it if you move the files.

```powershell
python -I -X utf8 src/train.py
python -I -X utf8 src/train.py --voice-only
```

The full run generates both the voice model used by the live scoring path and the full benchmark
model. For the optional LightGBM/XGBoost/CatBoost comparison, install `requirements-compare.txt`
and run `python -I -X utf8 src/compare_models.py`.

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
