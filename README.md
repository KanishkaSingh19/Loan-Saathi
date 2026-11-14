# Saathi: voice loan pre-screening for first-time borrowers

Millions of people in India (shopkeepers, drivers, farmers, daily-wage workers) have no
credit score and can't get through English loan forms. **Saathi** lets them find out, on a short
phone call in Hindi, Hinglish or English, whether they can get a small loan and **how
much they can safely afford**.

Built on **Gnani Agent Builder** (Prisma speech-to-text, LLM, Timbre text-to-speech), with a
LightGBM credit-risk model trained on the
[Home Credit Default Risk](https://www.kaggle.com/c/home-credit-default-risk) dataset as a mid-call webhook.

**Try it in one command:** `.\demo.ps1` (Windows) or `./demo.sh` (macOS/Linux), then open
http://127.0.0.1:8000/dashboard/. Add Gnani keys to `.env` to talk; without them, type.

## What we can show (not just claim)
Every number below comes from a file a script wrote, and the dashboard's **Evaluation Lab** shows them
with their sources; anything not measured says *Not currently measured*.

- **We caught our own model being wrong.** Live testing found a caller owing ₹36.5 lakh on ₹40,000 a
  month scored *safer* (6.1%) than the same caller with no debt (8.2%): in the training data big debts went
  with wealthier borrowers. Measured on 240 synthetic profiles, risk fell as debt rose in 520 of 1,680 checks.
  We added a hard rule (more than 24 months of income owed means "not right now", whatever the model says)
  and retrained the model monotone in debt: **0 of 1,680** now (`models/validation_report.json`).
- **Live voice, measured.** Gnani streaming STT hears the caller and ends the turn on its own; Gnani streaming
  TTS starts speaking on the first chunk; the caller can interrupt. Caller stops → first audio of the reply:
  **median 2.17 s, 90th percentile 3.36 s** over 13 scripted Hinglish turns, all 13 replies by Groq
  (`reports/latency_benchmark.json`, 12 Nov 2026; a synthetic caller voice on one machine, so a real phone
  line will be slower). The same script with Gemini on 10 Nov: median 3.09 s, 90th percentile 3.41 s; the
  transcript → reply stage went from a 1.64 s to a 0.81 s median. If the language model hangs, a turn now
  waits 3.0 s for the parser instead of 8.0 s (`src/bench_stall.py`, mocked stall, `reports/stall_benchmark.json`).
- **Extraction accuracy on unseen answers: 51 of 54.** A held-out set (`tests/extraction_heldout.json`) was
  written and committed after the parser was frozen, before any run, and is never used to tune it. First and
  only run, parser alone: 51/54 (`reports/extraction_benchmark_heldout.json`). Misses: Devanagari number
  words for years ("पंद्रह साल से") and hundreds ("बारह सौ"), plus "fifty thousand rupees", which Saathi reads
  back to confirm (it does that on purpose for "-ty thousand", which is often misheard); that expectation was
  written stricter than the dev set's rule and is left as committed.
- **Extraction accuracy, with the misses shown (dev set).** 64 hand-written answers ("dedh lakh", "ढाई लाख",
  "meri salary 35 hazaar hai", "2-3 lakh") with the correct outcome for each. The first run scored 59/64 and
  found four parser bugs ("six months" stored as 72 months, among others); after fixing them, 64/64 on the
  same cases, so that is not an independent figure (`reports/extraction_benchmark*.json`).
- **Tested where it matters:** confirmation before assessment, lakh/crore, ambiguous and contradictory
  amounts, excessive debt, the model failing to load, speech-to-text failing, no LLM-made decisions, no
  approval claims. Every suite is run and recorded by `python -I -X utf8 src/evaluation.py run`.
- **Not yet measured:** accuracy on Indian borrowers (no labelled Indian data exists) and results with real
  first-time borrowers (the study kit in STUDY.md is ready).

## How it works

**LISTEN → EXTRACT → VALIDATE → CONFIRM → ASSESS → EXPLAIN → SIMULATE → HANDOFF**

| Stage | What happens | Who decides |
|---|---|---|
| **Listen** | The caller speaks (or types) in Hindi, Hinglish or English; Gnani Prisma turns speech into text. | – |
| **Extract** | Gemini pulls answers out of free speech, each with an exact quote of the caller's words; our parser does the same deterministically and fills anything Gemini misses. | Gemini proposes, our code checks |
| **Validate** | Every value is range-checked; ambiguous numbers ("40", "40-50 hazaar") are read back, contradictions are asked about, impossible values refused. | Our code |
| **Confirm** | "Here's what I understood": the caller confirms or corrects single fields. Nothing is assessed before this. | The caller |
| **Assess** | Safety checks, then the LightGBM risk model, the 50% affordability rule and the 24-month debt rule. | Model + rules |
| **Explain** | The result in plain words: status, estimated risk and affordability, the financial picture and *why*, every number with its source. | Our code (Gemini may phrase it; numbers must match) |
| **Simulate** | "What if I take ₹2 lakh over 5 years?": the same decision path on a copy of the request, labelled *Simulation, not a lender offer*. | Model + rules |
| **Handoff** | Next steps, or a structured summary saved (and optionally sent) for a loan advisor. | The caller |

The call's `stage` comes from the state machine (`converse.stage()`) and is recorded with each call; the
borrower sees a single status line instead (Listening, Understanding, Verifying, Confirm). On the phone
(Agent Builder) the same stages run without the read-back step.

## Web call with Gemini (in the browser)
The dashboard's **Web call** tab lets anyone try Saathi from a browser, with no phone line:

```
mic → /ws/stt → Gnani streaming STT (its VAD ends the turn) → Gemini → our model + affordability rule
    → Gemini → /v1/tts/stream → Gnani streaming TTS → speaker (plays from the first chunk)
```

- **A live conversation, not push-to-talk**: *Talk to Saathi* keeps the microphone streaming; the caller
  just speaks and Saathi answers when they pause. Speaking over Saathi stops it (barge-in, with a guard so
  Saathi's own voice from the speakers isn't taken as an answer). A slow turn gets an "Achha, ek second".
  Amounts are spoken the way people say them ("teen lakh rupaye", not digits). Tap-to-talk and typing
  remain as fallbacks, and the Gnani keys never reach the browser.

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
- **It never dies on the LLM**: `src/llm/` tries **Groq** first (when `GROQ_API_KEY` is set), then
  **Gemini**, under one hard deadline per use (`LLM_DEADLINE_S`, default 3 s), with one retry on invalid
  JSON and a small response cache. If no provider answers in time, the call carries on with scripted
  Hinglish/English questions and our own Hindi number parser. A turn waits on the LLM at most once: if the
  read-back answer needed it, the result is phrased from the script. Each call records which provider
  answered each turn (`llm_trail`).
- **Ask a question, in either language, any time**: "What is an EMI?" in a Hinglish call is answered in English,
  "EMI kya hota hai?" in an English call in Hinglish, and Saathi carries on from the question it was asking. Fixed,
  tested answers (EMI, interest, how much loan, approval, documents, credit score, privacy, tenure, fees, FOIR, why
  it asks) from the policy's own numbers (`src/converse/faq.py`); anything else gets "a loan advisor can answer
  that". A question never counts as a failed answer (`tests/test_questions.py`).
- **What the loan is for, and the documents to keep ready**: Saathi starts where a lender starts: "Loan kis cheez
  ke liye chahiye?" (vehicle, education, business, home, personal), read from any sentence that says it ("bike ke
  liye 80 hazaar" fills both). It adds a tenure hint for that kind of loan and, with the result, a checklist that
  fits the loan and how the caller earns: salary slips and Form 16 for a salaried job, ITR and business proof for a
  shop, land records for a farmer, the dealer's quotation for a vehicle, the admission letter for education
  (`src/documents.py`). The purpose is never a model or rule input; decisions are identical with or without it
  (`tests/test_loan_purpose.py`), and Saathi never asks for or keeps a document.
- **A simple result screen**: a big status, one sentence, the EMI the caller can afford, and two buttons (Try
  another amount, Talk to an advisor). Everything else (what they told Saathi, the full assessment, the advisor
  view) is one tap away under "See details". Checked at phone width (375 px, no sideways scroll); the wording
  comes from the server's assessment (`dashboard/src/resultView.js`, tested).
- **EMIs as a range, not one made-up rate**: a lender's real rate isn't known before an offer, so every EMI the
  caller sees or hears (result, assessment, simulator, spoken summary) is a range across
  `emi_rate_range` in `config/policy.yaml` (14–24% a year), e.g. "₹4,801 to ₹5,287 a month" for ₹1 lakh over 24 months. The rules (50% limit,
  the offer amount) still use the single assumed 18%, so no decision changes (`tests/test_emi_range.py`). The
  decision also returns `offer_emi_low`, `offer_emi_high` and `offer_emi_range_text` for the phone agent.
- **Check an offer you got**: say "mujhe ek offer mila hai 2 lakh, 24 mahine, EMI 11,000" on the call, type it
  in the call room's panel, or `POST /v1/offer-check`. Saathi works out the real yearly rate the EMI implies
  (processing fee included), the total cost, and whether the EMI fits the caller's income under the 50% rule;
  it flags a rate above `offer_check.flag_rate_above` in `config/policy.yaml` (30%) and a "flat" rate that hides
  a higher real one. Labelled an estimate, never names a lender, stores nothing (`src/offer.py`,
  `tests/test_offer.py`). The profile's answers are never touched: an offer is the lender's, not the caller's.
- **How it fits together**: `docs/system-design.md` maps the architecture, a call turn, the state machine,
  the decision pipeline, storage, the API, security and deployment, with diagrams.
- **Why it is built this way**: `docs/decisions.md` records each design decision, what was turned down,
  and where it is enforced.
- **The rules live in one file**: `config/policy.yaml` holds every lending rule on top of the model (FOIR
  limit 50%, comfortable 40%, debt limit 24 months of income, assumed 18% interest, accepted tenure and age
  ranges, amount limits, retention). `src/policy.py` checks it at start-up and refuses to start on a missing
  or out-of-range value; changing a rule there changes the decision (`tests/test_policy.py`). The risk-band
  thresholds stay with the trained model (`models/voice_model_meta.json`), so they always match it.
- **A shorter call, same decisions**: the questions the decision rules read come first (loan, tenure,
  income, other loans); a question only the risk model reads (work, years, history, housing, age) is asked
  only while some answer to it could still move the risk band, checked by scoring every possible answer in
  one batch (about 0.1 s). `src/bench_call_length.py`: caller turns to the result, mean 14.43 → 13.04 over 200
  seeded random applicants and 13.17 → 11.83 over the 12 study personas (both counting the loan-purpose question), with the same decision, band and
  offer for all 215 applicants (`reports/call_length.json`). It also caught a parser bug in the full call:
  the housing answer "sarkari quarter" was overwriting the work, now fixed.
- **No identifiers reach the LLM**: `src/pii.py` masks phone numbers, Aadhaar, PAN, IFSC, account and card
  numbers, email, date of birth, a PIN code and a name the caller gives ("mera naam …") before any text goes
  to Groq or Gemini, and before our parser reads it (a phone number is not an amount). Amounts, counts and
  years pass unchanged; the trail records how many of each kind were masked, never the values.
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
- **Number provenance**: every figure carries its source ("You told us", "Calculated by
  Loan-Saathi", "Based on affordability rules", "Estimated by model"...) in the data itself.
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
- `tests/suites/test_safety.py` checks the product's boundaries end to end: no approval claims or lender names
  anywhere an applicant can see or hear, no protected attributes, the LLM never decides, numbers are
  validated, confirmation is required, affordability and the debt rule hold, contradictions are asked.

## Who can see what
- **Borrowers need no key.** Anyone can open the site and talk to Saathi. Each call gets its own secret token,
  given only to the browser that started it, so a visitor reaches their own call, result, what-ifs and advisor
  request, and nothing else. Calls are limited per network (`PUBLIC_CALLS_PER_IP_DAY`, default 20) and per day
  (`PUBLIC_CALLS_PER_DAY`, default 300) to bound Gnani and Gemini spend. The Evaluation Lab is public too: it
  holds no personal data.
- **Staff need `WEBHOOK_KEY`** for everything that shows other callers: Pre-screens, Advisor cases, the audit
  log, the study kit, admin actions and the phone webhook. `PUBLIC_CALLS=0` makes every call need the key too.

## WhatsApp, and a daily digest for advisors
- **WhatsApp** (`/v1/whatsapp/webhook`, Meta's WhatsApp Cloud API): the same conversation, rules and
  model over text, reply buttons and voice notes. A voice note is transcribed by Gnani STT (OGG, up to
  30 s) and answered with a Gnani TTS voice note plus the text. Meta's signature is checked on every
  message, retries are answered once, and phone numbers aren't stored (threads use a hashed key). Off
  unless the four `WHATSAPP_*` settings are present (deploy/DEPLOY.md, section 4).
- **Daily advisor digest** (`POST /v1/advisor-digest`): open and in-review advisor cases with their
  headline and question, sent to `ADVISOR_WEBHOOK_URL`. It runs when a scheduler calls it (a GitHub
  Actions cron is in deploy/DEPLOY.md) and reports whether it was actually delivered.

## Operations: persistent calls, advisor notifications, user study
- **Calls survive restarts:** every web-call turn is stored (`sessions` table, SQLite or Neon) and a
  call resumes where it was for 2 hours.
- **Advisors are notified for real** when `ADVISOR_WEBHOOK_URL` is set (Slack/Teams incoming webhook or
  any JSON endpoint; only the headline, question and case id are sent). Saathi says "notified" only
  when the webhook confirms delivery. Advisors **Pick up** and **Close** cases (with name, note and
  history) in **Advisor cases**.
- **User study kit** (`STUDY.md`): open the dashboard with `?study=1` for consent, persona cards and a
  3-question survey; the **Study** tab shows extraction accuracy, time to result, read-backs,
  corrections and survey results, with CSV export. `src/study_dryrun.py` plays every persona through
  real speech as a synthetic dry run (reported separately; not user results).

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
| **Voice model** (only what a caller can say) | 28 | **0.711** |

The gap is the real cost of asking questions instead of pulling bureau data. The voice model still
separates people well. On held-out data the low band defaults 3.2% of the time, medium 8.0% and high 18.8%.

### Better questions, not a bigger algorithm
The voice model's ceiling is what a caller can tell us, so accuracy came from asking about **repayment
history** in six short questions (`src/experiment_features.py` measured each on the same split):

| Question | AUC gain alone |
|---|---|
| In the last 12 months, did you pay any EMI late? | +0.0086 |
| How many loans have you taken, closed ones included? | +0.0078 |
| Have you ever paid an EMI late? | +0.0043 |
| Has a loan application of yours ever been refused? | +0.0042 |
| Ever more than a month late? / Is an EMI overdue now? | about +0.001 each |
| **All six together** | **+0.0204** |

The retrained model went from AUC **0.6870 to 0.7113**. A small hyperparameter search (`src/tune.py`, 12
settings) moved AUC by at most 0.0014 and confirmed the existing settings. On the call they're a funnel:
a first-time borrower hears only two of them. "Don't know" stays unknown (training blanks 10% of answers
so the model learns that case) and is scored a little more cautiously than a clean history, never as "no".
Every "yes" can only raise risk (monotone constraints; 0 of 960 synthetic checks failed). Answers are
self-reported, so real-world gains will be smaller than these dataset gains.

Decisions made along the way:
- **Fairness:** gender and marital status are never used. Age is used by the model but never
  spoken as a reason.
- **Removed a misleading signal:** loan size ÷ EMI looked very predictive, but it mostly reflects
  the interest rate the lender itself charged (its own risk view). A voice agent can't know that.
- **Monotone constraints:** risk can only go up as EMI-to-income, debt-to-income and the amount still
  owed go up. The debt constraint was added after live testing (see below); it cost 0.0025 AUC
  (0.6895 → 0.6870) and the risk bands are unchanged.
- **Calibration is checked, not assumed:** training fits an isotonic map on a separate calibration
  slice and keeps it only if it improves held-out Brier and calibration error. For this model it
  didn't (ECE 0.0023 raw vs 0.0034 calibrated), so predicted risks are used as they are.
- **Explanations are what-if effects** (swap in a typical applicant's value and see the change),
  and the agent only speaks factors that are fair and meaningful to a borrower.
- **Loan amounts come from a lending rule, not the model:** all EMIs must stay within 50% of income
  (FOIR). In this data safer people were given bigger loans, so the model can't choose amounts.
- **Currency:** the dataset isn't in rupees. Amounts are scaled so its median monthly income equals
  ₹25,000. Ratios are unaffected.

### A hard rule where the model is wrong
Live testing found a caller owing ₹36.5 lakh on a ₹40,000 income scored as *lower* risk (6.1%) than the
same caller with no debt (8.2%). In the Home Credit data, large bureau debts went with wealthier
borrowers, so the model learned "more debt, safer". Two fixes:
- **The model is now trained monotone in debt.** On 240 synthetic Indian applicants
  (`src/validate_model.py`), risk fell as debt rose in 520 of 1,680 checks with the old model and the
  final decision *improved* with more debt in 38 cases; with the retrained model both are 0.
- **A plain rule stays on top:** owing more than 24 months of income on other loans means "not right
  now", with that reason and "pay down existing loans first" spoken to the caller (the model's risk
  levels off at high debt, so the rule still matters).
The synthetic check tests behaviour only: there is no labelled Indian outcome data to measure
accuracy or calibration on Indian borrowers.

## Why LightGBM? Comparing LightGBM, XGBoost and CatBoost
All three were trained on the same 80/20 split and features, with matched settings (learning rate
0.05, early stopping after 200 rounds, similar tree size, L2 = 5, the same monotone constraints on the
voice model). They ran on a laptop CPU with 6 threads. Reproduce with `python -I -X utf8 src/compare_models.py`.

**Voice model, earlier version** (22 features, before the repayment-history questions; the current voice
model has 28 features and AUC 0.7113, and this comparison was not repeated for it)

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
src/server.py          FastAPI webhook, Gnani STT/TTS proxy (streaming too), and dashboard server
src/spoken.py          numbers spelled out for speech ("teen lakh rupaye")
src/evaluation.py      Evaluation Lab evidence; `run` records every test suite
src/bench_latency.py   voice latency benchmark through the live pipeline
src/bench_extraction.py extraction accuracy on tests/extraction_cases.json
src/whatsapp.py        WhatsApp Cloud API channel (text and voice notes)
demo.ps1 / demo.sh     one-command local start
dashboard/             React + Vite live dashboard (src/App.jsx, src/components/, src/styles.css)
tests/suites/test_webhook.py    end-to-end tests with Hinglish inputs
agent/                 system prompt, action body and step-by-step Agent Builder setup
models/                trained models, metadata, metrics.json, comparison.json/.md
data/kaggle/           default location for the Kaggle Home Credit CSVs (not in git)
```

## Run it

Python 3.10+ and Node.js 20.19+ are required. The trained LightGBM models are included, so the API
runs without the dataset. To retrain, download the
[Home Credit CSVs](https://www.kaggle.com/c/home-credit-default-risk/data) into `data/kaggle/` (or set
`HOME_CREDIT_DATA_DIR` to wherever you unzipped them); they are gitignored.

In PowerShell:
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
pip install -r requirements.txt
python -I -X utf8 tests/suites/test_webhook.py
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

`HOME_CREDIT_DATA_DIR` in `.env` selects the folder containing the Kaggle CSV files (empty means
`data/kaggle/`). After retraining, run `python -I -X utf8 src/validate_model.py --strict`.

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
