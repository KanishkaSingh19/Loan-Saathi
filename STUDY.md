# Loan-Saathi user study: protocol

**Goal:** measure, with 10–20 real first-time borrowers, whether people can complete a Loan-Saathi call
by voice, how accurately Saathi understands them, how long it takes, and whether they understand
and trust the result.

## What is measured (automatically)
| Measure | How |
|---|---|
| **Extraction accuracy, first pass** | Each participant speaks from a **persona card** (known true values). We compare the first value Saathi understood for each field with the card. |
| **Accuracy after read-back** | Same comparison on the confirmed profile (after read-backs and corrections). |
| **Time to result** | Seconds from the greeting to the assessment (and to the "Here's what I understood" read-back). |
| Turns, read-backs, corrections, recoveries | From the call's state trail. |
| Gemini share | How often Gemini (vs the scripted fallback) handled a turn. |
| Survey | Ease (1–5), understood the result (yes/partly/no), trust (1–5), comment, speech failures. |

Persona cards (`src/study.py`, 12 of them) cover Hinglish, English and Devanagari Hindi speakers,
daily wages, heavy debt, an over-ask, a pension, a student and a scripted correction. Using cards
means **no participant shares their real finances**, and accuracy can be measured exactly.

## Recruitment (10–20 people)
- First-time or thin-file borrowers: small-shop owners, drivers, daily-wage workers, domestic and
  delivery workers, students; aim for at least 40% women and at least 40% Hindi/Hinglish speakers.
- Mix of literacy and smartphone comfort. Recruit through a local partner (self-help group, NGO,
  cooperative), never through a lender, so nobody thinks a loan depends on it.
- Offer a small, fixed thank-you that doesn't depend on how they do.

## Session (about 15 minutes each)
1. **Set up:** open the dashboard with `?study=1` (e.g. `http://127.0.0.1:8000/dashboard/?study=1`),
   tab **Talk to Saathi**. Use a quiet room and the participant's own phone if possible.
2. **Consent:** read the four consent points on screen aloud in the participant's language. Tick the
   box only if they agree. Enter a code (T01, T02…), never a name.
3. **Persona:** pick the next card (rotate so each card is used by 1–2 people; give P10 to a Hindi
   speaker). Read the card together; ask them to "be" that person and speak naturally.
4. **Call:** tap **Start call**. Don't help unless they're stuck for 30 seconds; note where.
5. **Survey:** three questions appear after the result. Read them aloud if needed.
6. **Debrief (2 min):** "What was confusing? What would make you trust it more?" Note quotes in the comment.

## Analysis
- **Study** tab: participants and synthetic dry runs side by side; **Download CSV** for everything.
- Targets for this prototype (decide go/no-go before running, not after):
  first-pass accuracy ≥ 85%, accuracy after read-back ≥ 98%, median time to result ≤ 3 minutes,
  ≥ 80% "understood the result: yes", ease ≥ 4.
- Look at `wrong_first_pass` per field to see what to fix (numbers, Hindi words, speech errors).

## Privacy and safety
- Codes only; no names, phone numbers or real financial details. The stored data is the conversation
  text, timings and survey answers.
- It's a pre-screening prototype: tell participants plainly that no loan is offered or refused.
- Keep the study database separate (`DB_PATH=data/study.db`) and delete it after analysis.

## Dry run before real participants
`python -I -X utf8 src/study_dryrun.py --base http://127.0.0.1:8000 --key <WEBHOOK_KEY> --speech`
plays every persona through Gnani Timbre → Prisma → Gemini → our code. Results are tagged
**synthetic** and shown separately: they test the kit, they are not user results.
