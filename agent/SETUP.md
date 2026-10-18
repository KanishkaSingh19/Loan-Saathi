# Setting up the Saathi agent in Gnani Agent Builder

The agent handles the phone call (Gnani speech-to-text, LLM and text-to-speech). It calls our
webhook once mid-call to score the caller, then speaks the result.

```
Caller ⇄ Gnani Agent (Prisma STT → LLM → Timbre TTS)
              │  On-Call action: check_loan_eligibility
              ▼
        Public tunnel URL ──► src/server.py /v1/prescreen ──► LightGBM voice model
              ▲                                              + affordability rule
        Post-call trigger ──► /v1/post-call (call log for the dashboard)
```

## 0. Start the webhook and the tunnel

1. Start the server (keep the window open):
   `python -I -X utf8 src/server.py`
2. Start a public tunnel to port 8000 (see the main README) and copy the `https://…` URL it prints.
   Check `https://<tunnel>/health` returns `{"ok":true}`.
3. Open `.env` and copy the `WEBHOOK_KEY` value. You'll paste it into Agent Builder below.

## 1. Create the agent
Manage Agents → Create Agent → **Create from Scratch**
- Name: `Saathi Loan Prescreen`
- Description: `Voice pre-screening for small loans in Hindi, English and Kannada`

## 2. Agent setup tab
| Field | Value |
|---|---|
| Greeting message | `Namaste! Main Saathi hoon, Saathi Loans se. Main do minute mein bata sakti hoon ki aapko kitna loan mil sakta hai. Aapko kitne paise ki zaroorat hai?` |
| Ending message | `Dhanyavaad! Aapka din shubh ho.` |
| Provider / model | Try **Gnani → Pampa Priority** first (or Pampa Go). If actions don't trigger reliably, switch to a model marked *function calling*. |
| Temperature | `0.3` (steady wording, fewer made-up numbers) |
| System prompt | paste all of `agent/system_prompt.md` |

## 3. Customize tab
- Languages: `hi-IN`, `en-IN`, `kn-IN` (max 3). Turn on **language switch** (implicit).
- Voice: Gnani Timbre. For a Hindi or Hinglish feel use a `hi-IN` voice such as **Nalini** or the `hi-en` voice **Poorvi**.
- Advanced: turn on **denoiser** (phone lines are noisy), barge-in on.

## 4. Integration (once per org)
Integrations → Custom → new integration
- Name: `saathi_webhook`
- Method: `POST`
- URL: `https://<tunnel>/v1/prescreen`
- Authentication: Key `X-Webhook-Key`, Value = your `WEBHOOK_KEY`

## 5. Action
Manage Agents → Saathi → **Action** tab → `+`
- Name: `check_loan_eligibility`
- Description: `Run this once the caller has told you their loan amount and monthly income, plus whatever else they were willing to share (work, years in work, age, other loans, housing, family, education, vehicle). Run it again if the result says need_more_info.`
- Integration: `saathi_webhook`
- Trigger: **On-Call**
- Speak during action: `Ek second, main aapki details check kar rahi hoon.`
- Timeout: `10`
- Headers: `Content-Type` = `application/json`
- Body: paste `agent/action_body.json`

### Before API Call Variables
Make them all **String**. The webhook understands "pachaas hazaar", "2 saal", "haan" etc., and
String avoids failures when the caller says a number in words.

| Variable | Prompt |
|---|---|
| `loan_amount` | How much money the caller wants to borrow, as they said it (e.g. "50000", "dhai lakh"). |
| `tenure_months` | How long they want to take to repay, with the unit (e.g. "12 months", "2 saal"). |
| `monthly_income` | The caller's monthly income from their work, as they said it. |
| `work` | What work the caller does, in their own words (e.g. "kirana shop", "auto driver", "sarkari naukri", "retired"). |
| `years_in_work` | How long they have done this work, with the unit (e.g. "5 saal", "6 mahine"). |
| `age` | The caller's age in years. |
| `other_loans_count` | How many other loans they are repaying now. "0" if none. |
| `other_loans_outstanding` | Total amount still left to repay on those other loans. |
| `other_loans_emi` | Total monthly EMI they pay on those other loans. |
| `housing` | Where they live: own house, rented, with parents, or company quarters. |
| `family_members` | How many people are in their family, including them. |
| `children` | How many children they have. |
| `education` | How far they studied (e.g. "10th pass", "graduate", "didn't study"). |
| `owns_vehicle` | Whether they own a two-wheeler, car or auto: yes or no. |

### After API Call Variables
Name them exactly like the response keys:
`decision`, `reply_hint`, `offer_amount_text`, `offer_emi_text`, `tenure_months`,
`reasons_against`, `reasons_for`, `tips`, `missing_fields`, `prescreen_id`.

## 6. Post-call trigger (optional, for the dashboard)
Agent settings → Post-call trigger → on
- Method: `POST`, URL: `https://<tunnel>/v1/post-call`
- Header: `X-Webhook-Key` = your `WEBHOOK_KEY`

## 7. Test
1. **Test → Chat Window** first. Type: "mujhe 50 hazaar chahiye dukaan ke liye", then answer its questions.
   Check the action's ⋮ → **View Logs**: the request body should have your answers, and the response a `decision`.
   The server window also prints one line per pre-screen.
2. **Test → Web-based (Voice)**: speak in Hindi or Hinglish.
3. **Trigger Agent Call** to your whitelisted phone number for the real telephony test (and the demo video).

### Three scripts to try
- **Pre-approved:** 50 hazaar for the kirana shop, 1 saal, earn 30 hazaar a month, shop for 15 years, age 45, no other loans, own house.
- **Counter-offer:** 3 lakh, 2 saal, salary 25 hazaar, sales job for 6 years, one loan with 40 hazaar left and 2000 EMI.
- **Not now:** 2 lakh, construction work for 6 months, earn 15 hazaar, 3 loans with 1.5 lakh left.

## If something doesn't work
- **The action never fires:** make the action description more explicit, lower the temperature, or switch to a function-calling model.
- **The body arrives with `{{variable}}` text:** the variable wasn't filled. The webhook treats it as missing and returns `need_more_info` if it was income or amount.
- **The agent doesn't use the result:** the docs don't say how responses reach the LLM. If After API Call Variables stay empty, check View Logs; the agent may still see the raw JSON. As a last resort, mention `{{reply_hint}}` and `{{decision}}` in the system prompt.
- **401 in the logs:** the `X-Webhook-Key` value doesn't match `.env`.
- **Timeout:** the tunnel or server is down. Check the `/health` URL.
