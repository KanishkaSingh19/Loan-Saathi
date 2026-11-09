# Loan-Saathi: 2-minute demo

## Before you start (once)
```powershell
cd C:\Users\ks608\Desktop\Gnani_project
.\.venv\Scripts\Activate.ps1
cd dashboard; npm run build; cd ..
python -I -X utf8 src/server.py
```
Open `http://127.0.0.1:8000/dashboard/`, paste your `WEBHOOK_KEY`, and stay on **Talk to Saathi**.
Pick **English**, leave **Speak replies** on, and allow the microphone. For a clean feed, use a
separate `DB_PATH` (e.g. `$env:DB_PATH="data/demo.db"`) before starting the server.

## The script (≈ 2 minutes)

| Time | Do / say | What the audience sees |
|---|---|---|
| 0:00 | Tap **Start call**. | Saathi greets you and says it's a pre-screening, not an approval. The journey bar shows *Talk → Your details*. |
| 0:10 | Tap the mic and say: *"I earn forty thousand rupees per month. I already pay eight thousand EMI. I have around one and a half lakh debt. I want a three lakh loan for three years."* Tap the mic again. | Gnani Prisma transcribes; Gemini pulls out five answers at once (income, EMI, debt, loan, tenure); every one is checked by our parser. *Answers so far* fills in, each marked "to confirm". |
| 0:30 | Answer the few follow-ups: *"I run a kirana shop"*, *"10 years"*, *"one loan"*, *"my own house"*, *"35"*. | If Saathi asks "I heard ₹40,000, is that right?" that's the ambiguity check: say *"yes"*. |
| 0:55 | Listen to **"Here's what I understood"**. Tap **Confirm**. | The card on the right shows the financial profile. Nothing is assessed until you confirm. |
| 1:05 | Scroll to **Your Loan-Saathi Assessment**. Tap **🔊 Listen to this result**. | *Within your estimated repayment capacity.* Estimated risk, estimated affordability (₹12,000/month), requested EMI, suggested range, each with where the number came from. |
| 1:20 | Point at **Your financial picture** and **Why this result?** | The bar shows existing EMI + new EMI against the 50% line; reasons come from the rules and the risk model, never from the chat. |
| 1:30 | In **What if you changed the loan?**, tap **Try safer option**. Or say *"What if I take 2 lakh for 4 years?"* | EMI ↓, room left in your affordability ↑, total repayment ↓, labelled *Simulation, not a lender offer*. |
| 1:45 | Tap **Talk to a Loan Advisor**, type *"Can I get a longer tenure?"*, tap **Save my summary**. | Case number shown, with the honest prototype note. |
| 1:55 | Open the **Advisor cases** tab. | The advisor's view: confirmed profile, risk, affordable EMI, the simulation you tried, your question and a conversation summary. |

## If something goes wrong on stage
- **Mic or speech-to-text fails:** type the same sentence in the box. Everything after that is identical.
- **Gemini is slow or down:** Saathi carries on with scripted questions; our own parser still reads the
  long first sentence (it's tested with Gnani Prisma's real transcript of it).
- **Server hiccup:** the call screen offers **Try again**; nothing technical is shown.
- **Show a safety story instead (30 s):** at the summary, say *"My income is wrong"* → *"45,000"*, or
  start a new call and say *"I earn 40,000 ... actually I earn 60,000"* to see the contradiction check.
