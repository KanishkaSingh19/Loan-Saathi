"""Tests the browser web call (/v1/converse) without calling Gemini or Gnani.

    python -I -X utf8 -W ignore src/test_converse.py

1. No Gemini key: scripted questions + our parser carry a whole Hinglish call to a decision.
2. Mocked Gemini: answers it extracts are used, and its phrasing is spoken.
3. Guardrails: Gemini asking the wrong question, inventing an amount, returning junk values,
   or failing outright never changes what the caller hears about the decision.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "converse-test-key"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = ""  # never write test calls into the real database
os.environ["GEMINI_API_KEY"] = ""

import converse  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(server.app)
H = {"X-Webhook-Key": "converse-test-key"}


def say(sid, text):
    r = c.post("/v1/converse", headers=H, json={"session_id": sid, "text": text})
    assert r.status_code == 200, r.text
    return r.json()


def start(lang="hi"):
    r = c.post("/v1/converse", headers=H, json={"language": lang})
    assert r.status_code == 200, r.text
    return r.json()


# --- auth ---
assert c.post("/v1/converse", json={}).status_code == 401
assert c.post("/v1/converse", headers=H, json={"session_id": "nope", "text": "hi"}).status_code == 404

# --- 1. no Gemini: scripted + our parser ---
t = start()
assert t["asking"] == "loan_amount" and "Loan Saathi" in t["reply"], t
sid = t["session_id"]
t = say(sid, "pata nahi")                       # unclear: asked again
assert t["asking"] == "loan_amount" and t["reply"].startswith("Maaf"), t
t = say(sid, "pachas hazaar")
assert t["answers"]["loan_amount"] == 50000 and t["asking"] == "monthly_income", t
t = say(sid, "tees hazaar")
t = say(sid, "kirana ki dukaan")
assert t["asking"] == "years_in_work", t
t = say(sid, "das saal")
t = say(sid, "nahi")                            # no other loans -> EMI question skipped
assert t["answers"]["other_loans_count"] == 0 and t["asking"] == "housing", t
t = say(sid, "xyz")                             # not a housing answer
assert t["asking"] == "housing" and "housing" not in t["answers"], t
t = say(sid, "apna ghar hai")
t = say(sid, "35")
assert t["done"] and t["result"]["decision"] in ("pre_approved", "needs_review", "counter_offer"), t
assert t["result"]["offer_amount_text"].replace(",", "")[1:] in t["reply"].replace(",", ""), t["reply"]
assert t["llm"] == "fallback"
print("1. scripted call OK:", t["result"]["decision"], "-", t["reply"][:70], "...")
apps = c.get("/v1/applications", headers=H).json()["prescreens"]
assert apps and apps[0]["said"]["source"] == "web-call", apps[:1]
assert "done" in say(sid, "aur kuch?")["asking"]  # finished calls stay finished

# --- 2/3. mocked Gemini ---
calls = []


def A(field, value, quote):
    return {"field": field, "value": value, "quote": quote}


def fake(script):
    async def _g(api_key, model, system, user, schema):
        calls.append(user)
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step
    return _g


server.GEMINI_API_KEY = "fake-key"
t = start()
sid = t["session_id"]
converse._gemini = fake([
    # Caller says loan + income in one go; Gemini extracts both and asks about work.
    {"answers": [A("loan_amount", "100000", "ek lakh chahiye"), A("monthly_income", "40000", "kamaata hoon 40 hazaar")],
     "asking": "work",
     "reply": "Theek hai, 1 lakh ka loan aur 40 hazaar kamaai. Aap kya kaam karte hain?", "language": "hi"},
    # Junk value + asks the wrong field -> we keep asking years, scripted.
    {"answers": [A("work", "salaried driver", "driver hoon"), A("age", "7", "driver")], "asking": "age",
     "reply": "Aapki umar?", "language": "hi"},
    RuntimeError("timeout"),                    # Gemini down -> our parser takes "5 saal"
    {"answers": [A("other_loans_count", "1", "ek loan hai")], "asking": "other_loans_emi",
     "reply": "Ek loan. Uski EMI kitni hai?", "language": "hi"},
    {"answers": [A("other_loans_emi", "3000", "teen hazaar"),
                 A("age", "45", "45 saal")],  # invented: the caller never said it -> dropped
     "asking": "housing", "reply": "Aur ghar?", "language": "hi"},
    {"answers": [A("housing", "own house", "apna ghar")],  # quote not in "kiraye pe" -> dropped
     "asking": "age", "reply": "Umar?", "language": "hi"},
    {"answers": [A("age", "32", "32")], "asking": "done", "reply": "Shukriya, check karta hoon.", "language": "hi"},
    # Result phrasing invents an amount -> fallback text with the real numbers.
    {"reply": "Badhai ho! Aapko 9,99,999 rupaye mil gaye."},
])
t = say(sid, "mujhe ek lakh chahiye, kamaata hoon 40 hazaar")
assert t["answers"]["loan_amount"] == 100000 and t["answers"]["monthly_income"] == 40000, t
assert t["asking"] == "work" and t["reply"].startswith("Theek hai") and t["llm"] == "gemini", t
t = say(sid, "driver hoon, naukri hai")
assert "age" not in t["answers"], "age 7 must be rejected"
assert t["asking"] == "years_in_work" and t["reply"] == converse.BY_KEY["years_in_work"]["hi"], t
t = say(sid, "5 saal")
assert t["answers"]["years_in_work"] == 5 and t["llm"] == "fallback", t
t = say(sid, "haan ek loan hai")
t = say(sid, "teen hazaar")
assert "age" not in t["answers"], "an answer Gemini could not quote must be dropped"
t = say(sid, "kiraye pe")
assert t["answers"]["housing"] == "kiraye pe", t  # our parser on the real words, not Gemini's "own house"
t = say(sid, "32")
assert t["done"], t
r = t["result"]
assert "9,99,999" not in t["reply"] and "999999" not in t["reply"].replace(",", ""), t["reply"]
assert r["decision"] in ("pre_approved", "needs_review", "counter_offer", "not_eligible_now"), r
print("2/3. Gemini + guardrails OK:", r["decision"], "-", t["reply"][:70], "...")
assert '"still_needed"' in calls[0] and "loan_amount" in calls[0]

assert converse.quoted("Kamaata  hoon, 40 hazaar!", "mujhe ek lakh chahiye, kamaata hoon 40 hazaar")
assert converse.quoted("₹1,00,000 का लोन", "मुझे ₹1,00,000 का लोन चाहिए")
assert not converse.quoted("", "anything") and not converse.quoted("apna ghar", "kiraye pe")

# Result phrasing that keeps the real numbers is spoken as-is.
assert converse._amounts_ok("Aapko ₹50,000 mil sakta hai, 12 mahine, EMI ₹4,584.",
                            {"reply_hint": "pre-approved for ₹50,000 over 12 months, EMI about ₹4,584",
                             "tenure_months": 12})
assert not converse._amounts_ok("₹60,000", {"reply_hint": "₹50,000", "tenure_months": 12})

# Spelled-out amounts can't be checked, so they're refused; the offer itself must be stated.
hint = {"reply_hint": "request for ₹50,000 over 24 months (EMI about ₹2,496)", "offer_amount": 50000,
        "tenure_months": 24}
assert not converse._amounts_ok("Your request for fifty thousand rupees looks possible.", hint)
assert not converse._amounts_ok("Aapka loan mumkin lagta hai.", hint)          # offer missing
assert converse._amounts_ok("Aapka ₹50,000 ka loan, parivaar ke saath, 24 mahine, EMI ₹2,496.", hint)
assert converse._language("मैं 28 साल का हूँ", "en", "hi") == "hi"            # Devanagari stays Hindi
assert converse._language("I am 28", "en", "hi") == "en"
assert converse.valid("housing", "pg mein") and not converse.valid("housing", "upgrade")

# English start
t = start("en")
assert t["reply"].startswith("Hello") and t["language"] == "en"
asyncio.run(asyncio.sleep(0))
print("All converse tests passed.")
