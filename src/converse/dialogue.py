"""The turn: one caller utterance or button press through the state machine."""

import json
import re
import time
import uuid

import normalize as norm
import offer as offer_mod
import simulate as sim_mod
from scoring import inr

from .states import (AFFORDABILITY, ASSESSING, COLLECTING, COMPLETED, CONFIRMED, CORRECTION, FINISHED, HANDOFF, NEXT_STEPS,
    NotConfirmed, RECOVERY, RESULT, transition, TRANSITIONS, VERIFYING, WHAT_IF)
from .fields import (BACK, BY_KEY, DONT_KNOW, HISTORY, HUMAN, LABELS, LOW_EMI, MAX_CORRECTIONS, MAX_EMI, MAX_TRIES, MONEY, NO,
    NUMERIC, REQUIRED, whatif_amounts, whatif_request, WRONG_WORDS, YES)
from .texts import (CLARIFY, HELP, INTENT_PROMPT, INTENT_SCHEMA, LANGUAGE_RULES, NO_SAVE, PROBLEMS, RECOVER_AFTER,
    _repeat_money, RESULT_FOLLOWUP, RESULT_PROMPT, RESULT_SCHEMA, SAVE_OK, SAY, SKIP_WORDS)
from .validation import fmt, mentioned_field, next_field, valid
from .llm import _extract, _llm, _llm_available
from .sessions import _spoken_summary
from .values import _cross_checks, _no, offer, _pending_prompt, _problem_text, _resolve_pending, _unset, _yes


# ---------- the turn ----------

async def turn(s, text, decide, build_applicant, save, api_key="", model="gemini-3.5-flash-lite",
               action="", assess=None, safety=None, simulator=None, advisor=None, llm=None):
    """Handles one caller utterance (or button press) and returns the reply to speak. `llm` is the server's
    provider router (Groq, then Gemini); without it `api_key` means Gemini alone."""
    s["_llm_used_this_turn"] = False
    text = (text or "").strip()[:1000]
    action = (action or "").strip()
    s["updated"] = time.time()
    s.update(changed=set(), clarify=None, just_corrected=None, problem_value=None, help_for=None)
    if s["state"] in FINISHED:
        wants_person = action == "handoff" or (not action and text and HUMAN.search(text))
        if wants_person and advisor and not s.get("advisor_case"):
            return _late_handoff(s, advisor, text)
        if wants_person and s.get("advisor_case"):
            return SAY["handoff_done"][s["lang"]].format(case=s["advisor_case"]) + _after_options(s)
        if s["state"] == HANDOFF:
            transition(s, COMPLETED, "call over")
        aside = _offer_turn(s, text, action)
        if aside is not None:
            return aside
        if action == "new_call" or (not action and text and NEW_CALL.search(text)):
            s["history"].append(("Caller", text or "[new_call]"))
            s["new_call"] = True  # the call screen starts a fresh call (new session, nothing carried over)
            return SAY["new_call"][s["lang"]]
        if _can_reopen_what_if(s, simulator) and (action == "whatif" or (not action and text and
                                                                         _looks_like_what_if(text))):
            s["history"].append(("Caller", text or "[whatif]"))
            transition(s, WHAT_IF, "caller came back for a what-if")
            s["asking"] = "whatif"
            if action == "whatif" or not text:
                reply = SAY["whatif_prompt"][s["lang"]]
            else:
                env = {"simulator": simulator}
                reply = _try_what_if(s, env, text, "") or SAY["whatif_prompt"][s["lang"]]
            s["history"].append(("Saathi", reply))
            return reply
        return SAY["finished"][s["lang"]] + _after_options(s)
    aside = _offer_turn(s, text, action)
    if aside is not None:
        return aside
    s["history"].append(("Caller", text or f"[{action}]"))
    wanted = language_request(text, action)
    if wanted:  # "talk in English please": switch, say so, and ask the same thing again
        s["lang"] = wanted
        reply = SAY["lang_switched"][wanted] + _repeat_question(s)
        s["history"].append(("Saathi", reply))
        return reply
    if text and not action:
        s["lang"] = _language(text, None, s["lang"])
    env = {"decide": decide, "build_applicant": build_applicant, "save": save, "assess": assess,
           "safety": safety, "simulator": simulator, "advisor": advisor, "api_key": api_key, "model": model,
           "llm": llm}
    s["_advisor"] = advisor  # used by _handoff, which some paths reach without env
    s["_build_applicant"] = build_applicant  # used by the short call's band probe (next_field)

    # Consent can change at any point: "save mat karo" stops storage, "save karo" allows it.
    choice = (action == "consent:no" or (not action and NO_SAVE.search(text or ""))) and "no" or \
        (action == "consent:yes" or (not action and SAVE_OK.search(text or ""))) and "yes" or None
    if choice:
        s["consent"] = choice == "yes"
        s["consent_changed"] = True
        reply = SAY["consent_" + choice][s["lang"]]
        if s["state"] == COLLECTING and s.get("asking") in BY_KEY and not s["pending"]:
            reply += " " + BY_KEY[s["asking"]][s["lang"]]
        s["history"].append(("Saathi", reply))
        return reply

    if action == "handoff" or (not action and text and HUMAN.search(text)):
        if HANDOFF in TRANSITIONS[s["state"]]:
            if not s.get("consent"):  # asking for an advisor is asking us to keep the summary
                s["consent"] = True
                s["consent_via"] = "advisor request"
            reply = _handoff(s, "caller asked for a loan advisor", question=text)
            s["history"].append(("Saathi", reply))
            return reply

    handler = {COLLECTING: _collecting, VERIFYING: _verifying, CORRECTION: _correcting,
               RESULT: _after_result, NEXT_STEPS: _after_next_steps, RECOVERY: _recovering,
               WHAT_IF: _what_if}[s["state"]]
    reply = await handler(s, env, text, action)
    s["history"].append(("Saathi", reply))
    return reply


NEW_CALL = re.compile(r"\b(new (call|check)|start (again|over)|naya (check|call)|nayi (call|baat)|dobara shuru|"
                      r"phir se shuru|shuru se)\b|नया|दोबारा शुरू", re.I)


def _can_reopen_what_if(s, simulator):
    return s["state"] == COMPLETED and bool(s.get("result")) and bool(s.get("applicant")) and bool(simulator)


def _looks_like_what_if(text):
    loan, tenure = whatif_request(text)
    return loan is not None or tenure is not None or bool(WHAT_IF_WORDS.search(text) or MAX_EMI.search(text)
                                                          or LOW_EMI.search(text))


def _after_options(s):
    """What the caller can still do once the call is over."""
    lang = s["lang"]
    if s.get("advisor_case"):
        return SAY["after_options_saved"][lang]
    return SAY["after_options_result" if s.get("result") else "after_options"][lang]


def _late_handoff(s, advisor, text):
    """An advisor request after the call has ended: save the case without reopening the call."""
    lang = s["lang"]
    s["history"].append(("Caller", text or "[handoff]"))
    s["consent"] = True              # asking for an advisor is asking us to keep the summary
    s.setdefault("consent_via", "advisor request")
    try:
        case = advisor(s, text)
    except Exception as e:  # noqa: BLE001
        print(f"[converse {s['id']}] advisor case failed: {type(e).__name__}: {e}", flush=True)
        reply = SAY["handoff_failed"][lang]
    else:
        s["advisor_case"] = case["id"]
        s.setdefault("trail", []).append({"from": s["state"], "to": s["state"], "why": "advisor requested after the call",
                                          "ts": round(time.time(), 3)})
        key = "handoff_notified" if (case.get("notification") or {}).get("delivered") else "handoff"
        reply = SAY[key][lang].format(case=case["id"])
    s["history"].append(("Saathi", reply))
    return reply


def _handoff(s, why, question=""):
    """Save an advisor case (when the server provides one) and tell the caller honestly."""
    lang = s["lang"]
    case_id, notified = "", None
    if s.get("_advisor"):
        try:
            case = s["_advisor"](s, question)
            case_id = case["id"]
            notified = (case.get("notification") or {}).get("delivered")
        except Exception as e:  # noqa: BLE001
            print(f"[converse {s['id']}] advisor case failed: {type(e).__name__}: {e}", flush=True)
            return SAY["handoff_failed"][lang]
    transition(s, HANDOFF, why)
    s["asking"] = "done"
    s["advisor_case"] = case_id or s.get("advisor_case")
    print(f"[converse {s['id']}] advisor handoff: {why} case={case_id}", flush=True)
    if not case_id:
        return ("Theek hai, aap loan advisor se baat karna chahte hain. Dhanyavaad!" if lang == "hi"
                else "Okay, you'd like to talk to a loan advisor. Thank you!")
    if notified:  # only when the advisor channel confirmed delivery
        return SAY["handoff_notified"][lang].format(case=case_id)
    return SAY["handoff"][lang].format(case=case_id)


async def _collecting(s, env, text, action):
    asked = s.get("asking") if s.get("asking") in BY_KEY else next_field(s)
    if action == "answer":  # a help button: exactly the words on it, treated like typed text
        action = ""
    if s.get("skip_offer") and s["skip_offer"] != asked:
        s["skip_offer"] = None
    if s.get("skip_offer") == asked and not s["pending"]:
        if action == "skip" or (not action and (SKIP_WORDS.search(text) or (_yes(text, "") and not
                                                                            valid(asked, text)))):
            s["skip_offer"] = None
            s["skipped"].add(asked)
            return _advance(s, env, SAY["skipped_ok"][s["lang"]])
        if action == "retry" or (not action and _no(text, "") and not valid(asked, text)):
            s["skip_offer"] = None
            s["asking"] = asked
            s["help_for"] = asked
            return SAY["retry_question"][s["lang"]] + BY_KEY[asked][s["lang"]] + " " + HELP[asked][s["lang"]]
        s["skip_offer"] = None  # anything else: they may simply be answering it
    if s["pending"]:
        k = s["pending"][0]["field"]
        prefix, problem = _resolve_pending(s, text, action)
        return _advance(s, env, prefix, (k, problem) if problem else None)

    if asked == "tenure_months" and DONT_KNOW.search(text) and not norm.amounts(text):
        s["skipped"].add("tenure_months")  # the affordability rule picks the tenure
        return _advance(s, env, SAY["noted"][s["lang"]])
    if asked in HISTORY and DONT_KNOW.search(text):
        s["skipped"].add(asked)  # unknown stays unknown: the model has a learned "not answered" branch
        return _advance(s, env, SAY["noted"][s["lang"]])
    if asked == "tenure_months" and LOW_EMI.search(text) and not norm.amounts(text):
        s["skipped"].add("tenure_months")  # "EMI kam rakhni hai": options come after the result
        s["prefers_low_emi"] = True
        return _advance(s, env, SAY["low_emi_noted"][s["lang"]])

    # "My income is wrong" before the summary: forget that answer and ask for it again.
    wrong = mentioned_field(text)
    # Only an explicit "wrong / galat / change": "koi loan nahi" (no other loans) mentions "loan" and
    # "nahi" but is an answer, not a correction (a synthetic dry run caught it erasing the loan amount).
    if wrong in s["answers"] and WRONG_WORDS.search(text) and not norm.amounts(text) and not action:
        _unset(s, wrong)
        s["tries"].pop(wrong, None)
        if next_field(s) == wrong:
            s["asking"] = wrong
            return SAY["noted"][s["lang"]] + SAY["ask_correct"][s["lang"]].format(label=LABELS[s["lang"]][wrong])
        return _advance(s, env, SAY["noted"][s["lang"]])  # asked again when its turn comes

    s["asked_before"] = asked
    candidates, g = await _extract(s, env, text, asked)
    s["claims"] = {k: valid(k, raw) for k, raw, _ in candidates if k in MONEY}
    problem = None
    for k, raw, words in candidates:
        if k != asked and k in ("work", "housing") and k in s["answers"]:
            continue  # a side mention never replaces a free-text answer already given (corrections do)
        p = offer(s, k, raw, words, "collect")
        if p and k == asked:
            problem = (k, p)
    # Gemini may miss a short direct answer ("do", "nahi"); try our parser on it too.
    if g["used"] != "fallback" and asked in BY_KEY and asked not in s["answers"] and not s["pending"] \
            and not s["clarify"] and text:
        p = offer(s, asked, text, text, "collect")
        if p and p != "unclear":
            problem = (asked, p)
    s["llm"].append(g["used"])

    if asked in BY_KEY and asked not in s["answers"] and not s["pending"] and not s["clarify"]:
        s["tries"][asked] = s["tries"].get(asked, 0) + 1
        if s["tries"][asked] >= MAX_TRIES and not BY_KEY[asked].get("need"):
            s["skipped"].add(asked)
    return _advance(s, env, "", problem, g)


def _advance(s, env, prefix="", problem=None, g=None):
    """Decide what to say next from the state of the profile. Never calls Gemini."""
    lang = s["lang"]
    cross = _cross_checks(s)
    if s["pending"]:
        s["asking"] = s["pending"][0]["field"]
        lead = _problem_text(s, problem[0], problem[1]) if problem else prefix
        return lead + _pending_prompt(s)

    if s["state"] == CORRECTION:
        if s["corrections"] > MAX_CORRECTIONS:
            return SAY["too_many_changes"][lang] + _handoff(s, "too many corrections")
        k = s["correcting"]
        if k:
            s["asking"] = k
            lead = _problem_text(s, k, problem[1]) if problem else (prefix or SAY["noted"][lang])
            return lead + SAY["ask_correct"][lang].format(label=LABELS[lang][k])
        if s.get("just_corrected"):
            k = s["just_corrected"]
            if k in s["answers"]:
                prefix = SAY["updated"][lang].format(label=LABELS[lang][k], value=fmt(k, s["answers"][k], lang))
        elif not cross and next_field(s) == "done":
            s["asking"] = "correction"
            return prefix + SAY["which_field"][lang]

    nxt = next_field(s)
    if nxt != "done":
        if s["state"] == CORRECTION:
            transition(s, COLLECTING, "correction needs new answers")
        specific = problem and problem[0] == nxt and problem[1] in PROBLEMS and problem[1] != "unclear"
        # A precise refusal ("can't be negative") tells the caller what to fix: allow one more go.
        if BY_KEY[nxt].get("need") and s["tries"].get(nxt, 0) >= (MAX_TRIES if specific else RECOVER_AFTER):
            return _enter_recovery(s, COLLECTING, f"could not understand {nxt}")
        s["asking"] = nxt
        fails = s["tries"].get(nxt, 0) if nxt == s.get("asked_before") and not s.get("clarify") else 0
        if cross and cross[0] == nxt:
            return prefix + cross[1] + BY_KEY[nxt][lang]
        if s.get("clarify") == nxt:
            return prefix + CLARIFY[nxt][lang]
        if fails >= RECOVER_AFTER and nxt not in s.setdefault("skip_offered", []) and not specific:
            s["skip_offered"].append(nxt)
            s["skip_offer"] = nxt
            return prefix + SAY["sorry"][lang] + SAY["offer_skip"][lang]
        if fails == 1 and nxt in HELP and nxt not in MONEY and not specific:
            s["help_for"] = nxt
            return prefix + SAY["sorry"][lang] + BY_KEY[nxt][lang] + " " + HELP[nxt][lang]
        if specific and nxt in HELP and HELP[nxt].get("buttons"):
            s["help_for"] = nxt
        if problem and problem[0] == nxt:
            if problem[1] == "unclear" and nxt in MONEY:
                return prefix + SAY["sorry"][lang] + _repeat_money(nxt, lang)
            return prefix + _problem_text(s, nxt, problem[1]) + BY_KEY[nxt][lang]
        if g and g["used"] != "fallback" and g["asking"] == nxt and g["reply"] and not prefix \
                and _question_ok(g["reply"], s):
            return g["reply"]
        if nxt == s.get("asked_before") and not prefix and nxt in MONEY:
            return SAY["sorry"][lang] + _repeat_money(nxt, lang)
        return prefix + BY_KEY[nxt][lang]

    if s["state"] in (COLLECTING, CORRECTION):
        transition(s, VERIFYING, "profile complete")
    s["asking"] = "confirm"
    s["tries"].pop("confirm", None)
    return prefix + _spoken_summary(s)


def _enter_recovery(s, back_to, why):
    s["recoveries"] += 1
    if s["recoveries"] > 2 and HANDOFF in TRANSITIONS[s["state"]]:
        return _handoff(s, f"recovery failed: {why}")
    transition(s, RECOVERY, why)
    s["recover_to"] = back_to
    s["asking"] = "recovery"
    return SAY["recovery"][s["lang"]]


async def _recovering(s, env, text, action):
    back = s["recover_to"] or COLLECTING
    if not action and re.search(r"\b(advisor|adviser|summary|save)\b|save kar", text or "", re.I):
        return _handoff(s, "caller chose the advisor in recovery", question=text)
    if _no(text, action) and not _yes(text, action):
        transition(s, COMPLETED, "caller stopped")
        s["asking"] = "done"
        return SAY["bye"][s["lang"]] + _after_options(s)
    if back == COLLECTING:
        transition(s, COLLECTING, "retry")
        k = next_field(s)
        s["tries"].pop(k, None)
        s["asked_before"] = None  # a fresh start: ask plainly, not "sorry, I didn't understand"
        if _yes(text, action) or not text:
            return _advance(s, env)
        s["asking"] = k
        return await _collecting(s, env, text, action)  # they may have just answered
    transition(s, VERIFYING, "retry")
    return _advance(s, env)


async def _intent(s, env, text, action):
    """("confirm" | "correct" | None, field or None) for a reply to the summary."""
    if action == "confirm":
        return "confirm", None
    if action == "change":
        return "correct", None
    if action.startswith("correct:"):
        k = action.split(":", 1)[1]
        return "correct", k if k in BY_KEY else None
    field = mentioned_field(text)
    no, yes = NO.search(text), YES.search(text)
    if no and (not yes or field or re.search(r"\b(sahi|theek|thik|correct|right)\s+(nahi|nahin|not)\b|"
                                             r"\bnot\s+(correct|right)\b", text, re.I)):
        return "correct", field
    if field and norm.amounts(text):
        return "correct", field
    if yes and not no:
        return "confirm", None
    if _llm_available(env) and text:
        try:
            out, provider = await _llm(s, env, "intent", INTENT_PROMPT,
                                       json.dumps({"caller_said": text}, ensure_ascii=False), INTENT_SCHEMA)
            s["llm"].append(provider)
            intent, k = out.get("intent"), out.get("field")
            if intent in ("confirm", "correct"):
                return intent, k if k in BY_KEY else None
        except Exception as e:  # noqa: BLE001
            print(f"[converse {s['id']}] LLM intent unavailable: {type(e).__name__}: {e}", flush=True)
    return None, field


def _apply_correction_value(s, k, text):
    """In the same breath as naming the field ("income 60 hazaar hai"), take the value too."""
    if k in NUMERIC and norm.amounts(text):
        return offer(s, k, text, text, "correct")
    return None


WHAT_IF_WORDS = re.compile(r"\bwhat if\b|\bagar\b|\bsuppose\b|\bkya hoga\b", re.I)


async def _verifying(s, env, text, action):
    if not action and WHAT_IF_WORDS.search(text or ""):
        # A what-if before the assessment would silently change the request: answer it after.
        return SAY["whatif_later"][s["lang"]]
    intent, field = await _intent(s, env, text, action)
    if intent == "confirm":
        transition(s, CONFIRMED, "caller confirmed")
        for k in s["answers"]:
            s["status"][k] = "confirmed"
        return await _assess(s, env)
    if intent == "correct":
        transition(s, CORRECTION, f"caller wants to change {field or 'something'}")
        s["correcting"] = field
        problem = _apply_correction_value(s, field, text) if field and not action else None
        return _advance(s, env, "", (field, problem) if problem else None)
    s["tries"]["confirm"] = s["tries"].get("confirm", 0) + 1
    if s["tries"]["confirm"] >= MAX_TRIES:
        return _enter_recovery(s, VERIFYING, "no clear yes or no to the summary")
    return SAY["verify_unclear"][s["lang"]]


async def _correcting(s, env, text, action):
    if s["pending"]:
        k = s["pending"][0]["field"]
        prefix, problem = _resolve_pending(s, text, action)
        return _advance(s, env, prefix, (k, problem) if problem else None)

    k = s["correcting"]
    if action.startswith("correct:"):
        k = action.split(":", 1)[1]
        s["correcting"] = k if k in BY_KEY else None
        return _advance(s, env)
    if action == "confirm":
        transition(s, VERIFYING, "nothing to change")
        return _advance(s, env)
    mentioned = mentioned_field(text)

    if k is None:
        if mentioned:
            s["correcting"] = mentioned
            problem = _apply_correction_value(s, mentioned, text)
            return _advance(s, env, "", (mentioned, problem) if problem else None)
        if YES.search(text) and not NO.search(text):
            transition(s, VERIFYING, "nothing to change")
            return _advance(s, env)
        _, field = await _intent(s, env, text, "")
        if field:
            s["correcting"] = field
            return _advance(s, env)
        s["tries"]["correction"] = s["tries"].get("correction", 0) + 1
        if s["tries"]["correction"] >= MAX_TRIES:
            transition(s, VERIFYING, "could not tell which field")
            return _advance(s, env)
        return SAY["which_field"][s["lang"]]

    # Free-text fields would swallow "no, the income is wrong": switch field first.
    if mentioned and mentioned not in (k, "loan_amount") and (k not in NUMERIC or not norm.amounts(text)):
        s["correcting"] = k = mentioned
        problem = _apply_correction_value(s, k, text)
        return _advance(s, env, "", (k, problem) if problem else None)
    problem = offer(s, k, text, text, "correct")
    if problem == "unclear" and _llm_available(env) and text:
        candidates, _ = await _extract(s, env, text, k)
        for f, raw, words in candidates:
            if f == k:
                problem = offer(s, k, raw, words, "correct")
    return _advance(s, env, "", (k, problem) if problem else None)


def require_confirmed(s):
    """The gate before any assessment: confirmed state, required fields, every answer confirmed."""
    if s["state"] != CONFIRMED:
        raise NotConfirmed(f"assessment needs state {CONFIRMED}, call is in {s['state']}")
    missing = [k for k in REQUIRED if k not in s["answers"]]
    unconfirmed = [k for k in s["answers"] if s["status"].get(k) != "confirmed"]
    if missing or unconfirmed or s["pending"]:
        raise NotConfirmed(f"missing {missing}, unconfirmed {unconfirmed}, pending {len(s['pending'])}")


async def _assess(s, env):
    """CONFIRMED -> ASSESSING_RISK (model) -> CHECKING_AFFORDABILITY (rules) -> SHOWING_RESULT."""
    require_confirmed(s)
    s["asking"] = "done"
    body = {k: str(v) for k, v in s["answers"].items()}
    context = {"channel": "web", "confirmed": True, "open_checks": len(s["pending"])}
    try:
        applicant = env["build_applicant"](body)
        if env.get("safety"):
            # Deterministic safety check before the model runs: a failure pauses with what's needed.
            failed = [c for c in env["safety"](applicant, context) if c["state"] == "fail"]
            if failed:
                print(f"[converse {s['id']}] safety check failed: {[c['id'] for c in failed]}", flush=True)
                transition(s, RECOVERY, "safety check failed")
                s["recover_to"] = VERIFYING
                s["asking"] = "recovery"
                s["safety_failed"] = failed
                return SAY["safety_failed"][s["lang"]] + " " + " ".join(c["detail"] for c in failed)
        transition(s, ASSESSING, "profile confirmed")
        risk = env["assess"](applicant) if env["assess"] else None
        transition(s, AFFORDABILITY, "risk scored")
        result, details = env["decide"](applicant, risk, context)
        if result.get("decision") == "need_more_info":
            raise ValueError(f"decision needs more info: {result.get('missing_fields')}")
    except Exception as e:  # noqa: BLE001
        print(f"[converse {s['id']}] assessment failed: {type(e).__name__}: {e}", flush=True)
        reply = _enter_recovery(s, VERIFYING, "assessment failed")
        return SAY["assess_failed"][s["lang"]] if s["state"] == RECOVERY else reply
    pid = uuid.uuid4().hex[:12]
    result["prescreen_id"] = pid
    env["save"](pid, {**body, "source": "web-call", "session_id": s["id"]}, applicant, result, details)
    s.update(result=result, prescreen_id=pid, applicant=applicant, simulation=None)
    transition(s, RESULT, result["decision"])

    reply, provider = "", "fallback"
    if _llm_available(env):
        try:
            out, provider = await _llm(s, env, "phrase_result",
                                       RESULT_PROMPT.format(language_rule=LANGUAGE_RULES[s["lang"]]),
                                       f"Guidance:\n{result['reply_hint']}", RESULT_SCHEMA)
            reply = str(out.get("reply") or "").strip()
            if reply and not _amounts_ok(reply, result):
                print(f"[converse {s['id']}] Gemini result reply changed an amount; using fallback", flush=True)
                reply = ""
        except Exception as e:  # noqa: BLE001
            print(f"[converse {s['id']}] LLM result phrasing unavailable: {type(e).__name__}: {e}", flush=True)
    s["llm"].append(provider if reply else "fallback")
    if not reply:
        reply = _fallback_result(result, s["lang"])
    return reply + RESULT_FOLLOWUP.get(result["decision"], {}).get(s["lang"], "")


def _try_what_if(s, env, text, action):
    """Answers a what-if question with the deterministic simulator, or returns None."""
    if action or not text or not env.get("simulator") or not s.get("applicant"):
        return None
    lang, a = s["lang"], s["applicant"]
    if MAX_EMI.search(text):
        cap = ((s["result"] or {}).get("assessment") or {}).get("affordability") or {}
        if cap.get("value", 0) <= 0:
            reply = SAY["no_room"][lang]
        else:
            reply = SAY["max_emi"][lang].format(cap=inr(cap["value"]))
        if s["state"] != WHAT_IF:
            transition(s, WHAT_IF, "caller asked for the maximum EMI")
        return reply + SAY["whatif_more"][lang]
    current = s.get("simulation") or {}
    loan, tenure = whatif_request(text)
    loans, bare = whatif_amounts(text)
    if len(loans) > 1 or (loan is None and tenure is None and bare and WHAT_IF_WORDS.search(text)):
        if s["state"] != WHAT_IF:
            transition(s, WHAT_IF, "caller asked an unclear what-if")
        if len(loans) > 1:
            return SAY["whatif_which"][lang].format(options=" / ".join(inr(v) for v in loans))
        return SAY["whatif_bare"][lang].format(n=bare[0])
    if LOW_EMI.search(text) and loan is None and tenure is None:
        options = env["simulator"](a, scen=True).get("scenarios") or []
        safer = next((o for o in options if o.get("kind") == "safer" and not o.get("unavailable")), None)
        if safer:
            loan, tenure = safer["loan_amount"]["value"], safer["tenure"]["value"]
        else:  # no lower amount fits: show the same loan over a longer time
            base = int(a.get("tenure_months") or 24)
            loan, tenure = a["loan_amount"], min(120, base + 12)
    if BACK.search(text) and loan is None and tenure is None:
        loan, tenure = a["loan_amount"], int(a.get("tenure_months") or 24)
    if loan is None and tenure is None:
        return None
    sim = (current.get("simulation") or {})
    loan = loan if loan is not None else (sim.get("loan_amount") or {}).get("value")
    tenure = tenure if tenure is not None else (sim.get("tenure") or {}).get("value")
    try:
        out = env["simulator"](a, loan, tenure)
    except sim_mod.InvalidSimulation as e:
        return str(e) + SAY["whatif_more"][lang]
    s["simulation"] = out
    sm = out["simulation"]
    if sm.get("status") != "paused":
        s.setdefault("sim_log", []).append(f"{sm['loan_amount']['text']} over {sm['tenure']['value']} months "
                                           f"(EMI {sm['emi']['text']})")
        s.setdefault("sim_events", []).append({"ts": round(time.time(), 3), "text": s["sim_log"][-1]})
    if s["state"] != WHAT_IF:
        transition(s, WHAT_IF, "caller asked a what-if")
    return sim_mod.spoken(out["simulation"], lang) + SAY["whatif_more"][lang]


async def _what_if(s, env, text, action):
    lang = s["lang"]
    reply = _try_what_if(s, env, text, action)
    if reply:
        return reply
    if _no(text, action) and not _yes(text, action):
        transition(s, COMPLETED, "caller finished")
        s["asking"] = "done"
        return SAY["bye"][lang] + _after_options(s)
    if _yes(text, action) or re.search(r"next|aage|steps", text or "", re.I):
        transition(s, NEXT_STEPS, "caller wants next steps")
        return _next_steps(s["result"], lang) + SAY["more"][lang]
    return SAY["whatif_unclear"][lang]


async def _after_result(s, env, text, action):
    lang = s["lang"]
    reply = _try_what_if(s, env, text, action)
    if reply:
        return reply
    if _no(text, action) and not _yes(text, action):
        transition(s, COMPLETED, "caller declined next steps")
        s["asking"] = "done"
        return SAY["bye"][lang] + _after_options(s)
    transition(s, NEXT_STEPS, "caller wants next steps")
    return _next_steps(s["result"], lang) + SAY["more"][lang]


async def _after_next_steps(s, env, text, action):
    reply = _try_what_if(s, env, text, action)
    if reply:
        return reply
    transition(s, COMPLETED, "call finished")
    s["asking"] = "done"
    return SAY["finished"][s["lang"]] + _after_options(s)


def _next_steps(r, lang):
    """Deterministic next steps for each decision."""
    d = r["decision"]
    tips = r.get("tips_hi" if lang == "hi" else "tips", "") or r.get("tips", "")
    if lang == "hi":
        if d in ("pre_approved", "needs_review", "counter_offer"):
            return ("Aage ke steps: Aadhaar card, PAN card aur pichhle 3 mahine ka bank statement taiyaar rakhiye. "
                    "Lender documents ki jaanch ke baad hi koi faisla karega. Agar aap chahein, toh 'loan advisor "
                    "se baat' boliye, main aapka summary advisor ke liye save kar dunga.")
        return ((f"Isme madad karega: {tips}. " if tips else "")
                + "3 se 6 mahine baad, jab EMI ya baaki loan kam ho jaaye, dobara check kijiye.")
    if d in ("pre_approved", "needs_review", "counter_offer"):
        return ("Next steps: keep your Aadhaar card, PAN card and the last 3 months of bank statements ready. "
                "A lender decides only after checking those documents. If you like, say 'talk to a loan advisor' "
                "and I'll save your summary for one.")
    return ((f"What would help: {tips}. " if tips else "")
            + "Check again in 3 to 6 months, once your EMIs or existing loans have come down.")


def _digits(text):
    return {int(m.replace(",", "")) for m in re.findall(r"\d[\d,]*", text) if m.replace(",", "").isdigit()}


# Words that spell out an amount. Small numbers ("ek", "do", "two working days") are everyday
# words and are allowed; amounts always need a scale word or a tens word.
NUMBER_WORDS = re.compile(
    r"\b(hundreds?|thousands?|lakhs?|lacs?|crores?|hazaar|hazar|hajar|sau|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|bees|pachees|tees|chalis|chaalis|pachas|pachaas|"
    r"sattar|assi|nabbe)\b",  # not "saath" (60): it also means "with"; "saath hazaar" is caught by hazaar
    re.I)


SPELLED_NUMBERS = re.compile(
    r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|bees|pachees|tees|chalis|chaalis|"
    r"pachas|pachaas|sattar|assi|nabbe|sau)\b", re.I)


def _question_ok(reply, s):
    """Gemini's next question may echo the caller's own numbers but never bring new ones: in a live
    test it told a caller "most people borrow between twenty thousand and one lakh", an invented
    anchor. Spelled-out numbers are refused (they can't be checked), and every figure must be one
    the caller said or one of their collected answers."""
    if SPELLED_NUMBERS.search(reply):
        return False
    said = set()
    for _, text in (h for h in s["history"] if h[0] == "Caller"):
        said |= {int(a["value"]) for a in norm.amounts(text)}  # "ek lakh" -> 100000, "2 saal" -> 2
    said |= {int(v) for v in s["answers"].values() if isinstance(v, (int, float))}
    # Only figures written in digits or with lakh/hazaar count: a lone word like "do" is English "do".
    return {int(a["value"]) for a in norm.amounts(reply) if a["digits"] or a["scaled"]} <= said


def _amounts_ok(reply, result):
    """Every number Gemini says must appear in the guidance, written in digits, and the offer
    amount itself must be in the reply. Number words are refused because they can't be checked."""
    if NUMBER_WORDS.search(reply):
        return False
    allowed = _digits(result["reply_hint"]) | {result.get("tenure_months", 0)}
    said = _digits(reply)
    if result.get("offer_amount") and int(result["offer_amount"]) not in said:
        return False
    return said <= allowed


HI_WORDS = re.compile(r"\b(hai|hain|hoon|hu|mujhe|mera|meri|mere|chahiye|kamaata|kamata|kamaati|kamati|nahi|nahin|"
                      r"haan|kya|aap|main|ka|ki|ke|ko|se|mein|saal|mahine|mahina|hazaar|hazar|wala|wali|kar|raha|"
                      r"rahi|karta|karti|ghar|kiraye|dukaan|apna|apni|toh|bhi|aur|kitna|kitne|baaki|kist)\b", re.I)
EN_WORDS = re.compile(r"\b(i|am|my|is|the|a|an|want|need|earn|have|for|and|of|to|do|it|yes|per|month|months|"
                      r"years|what|how|much|own|live|work|loan|loans|rent|with)\b", re.I)


# "Can you talk in English please" / "इंग्लिश में बात करो" / "Hindi mein boliye": an explicit request to switch.
# Speech-to-text set to Hindi writes English words in Devanagari, so both scripts count.
_EN_NAME = r"(?<![a-z])(?:english|inglish|angrezi|angreji)|इंग्लिश|इंगलिश|अंग्रेज़ी|अंग्रेजी"
_HI_NAME = r"hindi|hinglish|हिंदी|हिन्दी|हिंग्लिश"
_VERB = r"talk|speak|reply|answer|continue|switch|change|baat|bol\w*|टॉक|स्पीक|बात|बोल\w*"
_AFTER = r"please|pls|mein|me|में|प्लीज|bolo|boliye|baat|बात|बोलो|बोलिए|mai"


def _switch(name):
    """A request to change language: a talking verb near the name, the name followed by mein/please/bolo,
    or a sentence ending "in <name>". Not "I work in an English school"."""
    return re.compile(rf"(?:{_VERB})\W+(?:\w+\W+){{0,3}}?(?:in\s+|इन\s+)?(?:{name})|"
                      rf"(?:{name})\s*(?:{_AFTER})(?:\W|$)|\b(?:in|इन)\s+(?:{name})\s*[.!?।]*\s*$", re.I)


SWITCH_TO = {"en": _switch(_EN_NAME), "hi": _switch(_HI_NAME)}


def language_request(text, action=""):
    """'en' or 'hi' when the caller asks to change language (button or words), else None."""
    if action in ("lang:en", "lang:hi"):
        return action[5:]
    if action or not text or norm.amounts(text):
        return None
    for lang, pat in SWITCH_TO.items():
        if pat.search(text):
            return lang
    return None


# ---------- "Check an offer you got": an aside, never a state change or a profile answer ----------

OFFER_START = re.compile(
    r"offer\s*(?:mila|mili|aaya|aya|diya|di\b|hai\s+mere|hai\s+mujhe)|(?:got|received|have|get)\s+(?:an?|one|this|my)?"
    r"\s*(?:loan\s+)?offer|check\s+(?:an?|my|this|the)\s+offer|offer\s+check|ऑफ़र\s*मिला|ऑफर\s*मिला", re.I)
OFFER_STOP = re.compile(r"\b(rehne do|chhodo|chhod do|cancel|nahi chahiye|never ?mind|stop|wapas|back)\b|रहने दो", re.I)
OFFER_SAY = {
    "ask": {"hi": "Zaroor, main offer jaanch deta hoon. Offer ki rakam, kitne mahine, aur mahine ki EMI bataiye. "
                  "Processing fee ho toh woh bhi.",
            "en": "Sure, I'll check the offer. Tell me the loan amount, how many months, and the monthly EMI, "
                  "plus any processing fee."},
    "missing": {"hi": "Offer jaanchne ke liye {what} bhi bataiye.", "en": "To check it I also need the {what}."},
    "what": {"hi": {"loan amount": "loan ki rakam", "tenure": "kitne mahine", "EMI": "mahine ki EMI"},
             "en": {"loan amount": "loan amount", "tenure": "tenure (months or years)", "EMI": "monthly EMI"}},
    "stopped": {"hi": "Theek hai, offer chhod dete hain.", "en": "Okay, let's leave the offer."},
    "back": {"hi": " Ab hum wahin se aage badhte hain.", "en": " Now, back to where we were."},
}


def _offer_turn(s, text, action):
    """Handles "mujhe ek offer mila hai ...": collects amount, tenure and EMI (fee optional), then gives the
    estimate and returns to whatever Saathi was asking. Returns the reply, or None when this turn isn't about
    an offer. Nothing here touches s["answers"] or the state: an offer is the lender's, not the caller's
    profile."""
    collecting = s.get("asking") == "offer"
    starting = action == "offer_check" or (not action and text and OFFER_START.search(text))
    if not (collecting or starting):
        return None
    if collecting and not starting and (action in ("handoff",) or action.startswith("lang:") or
                                        (not action and (HUMAN.search(text or "") or language_request(text, "")))):
        s["asking"] = s.pop("offer_return_asking", None) or s.get("asking")  # the rest of turn() handles it
        s["offer_draft"] = {}
        return None
    lang = s["lang"]
    s["history"].append(("Caller", text or f"[{action}]"))
    if collecting and not starting and (OFFER_STOP.search(text or "") or action in ("no", "back")):
        reply = OFFER_SAY["stopped"][lang] + _offer_return(s)
        s["history"].append(("Saathi", reply))
        return reply
    if starting:
        s["offer_draft"] = {}
        if not collecting:
            s["offer_return_asking"] = s.get("asking")
    draft = s.setdefault("offer_draft", {})
    for k, v in offer_mod.parse(text).items():
        if v is not None:
            draft[k] = v
    answers = s.get("answers", {})
    try:
        result = offer_mod.check(draft.get("loan_amount"), draft.get("tenure_months"), draft.get("emi"),
                                 draft.get("processing_fee"), draft.get("quoted_rate"),
                                 monthly_income=answers.get("monthly_income"),
                                 existing_emi=answers.get("other_loans_emi") or 0)
    except offer_mod.OfferError as e:
        missing = [k for k, v in (("loan amount", draft.get("loan_amount")), ("tenure", draft.get("tenure_months")),
                                  ("EMI", draft.get("emi"))) if not v]
        s["asking"] = "offer"
        if missing and len(missing) == 3:
            reply = OFFER_SAY["ask"][lang]
        elif missing:
            reply = OFFER_SAY["missing"][lang].format(what=", ".join(OFFER_SAY["what"][lang][m] for m in missing))
        else:
            reply = str(e) + " " + OFFER_SAY["ask"][lang]
        s["history"].append(("Saathi", reply))
        return reply
    s["offer_result"] = result
    s.setdefault("offer_checks", []).append({"ts": round(time.time(), 3), "verdict": result["verdict"],
                                             "flags": [f["code"] for f in result["flags"]]})
    s["offer_draft"] = {}
    reply = result["summary"][lang] + _offer_return(s)
    s["history"].append(("Saathi", reply))
    return reply


def _offer_return(s):
    """Back to what Saathi was asking before the offer came up."""
    lang = s["lang"]
    s["asking"] = s.pop("offer_return_asking", None) or ("done" if s["state"] in FINISHED else next_field(s))
    if s["state"] in FINISHED:
        return ""
    again = _repeat_question(s)
    if again:
        return OFFER_SAY["back"][lang] + again
    if s["state"] == VERIFYING:
        return ""
    return SAY["more"][lang] if s["state"] in (RESULT, WHAT_IF, NEXT_STEPS) else ""


def _repeat_question(s):
    """What Saathi was waiting for, in the call's (new) language."""
    lang = s["lang"]
    if s["pending"]:
        return ""
    if s["state"] == COLLECTING and s.get("asking") in BY_KEY:
        return " " + BY_KEY[s["asking"]][lang]
    if s["state"] == VERIFYING:
        return " " + _spoken_summary(s)
    return ""


def _language(text, gemini_says, current):
    """Decided by our code from the caller's own words, never by Gemini's guess: Devanagari is Hindi;
    otherwise a clear majority of Hindi or English words switches; anything else (\"3 lakh\", \"35\")
    keeps the call's language. `gemini_says` is ignored and kept only for compatibility."""
    if re.search(r"[ऀ-ॿ]", text or ""):
        return "hi"
    hi, en = len(HI_WORDS.findall(text or "")), len(EN_WORDS.findall(text or ""))
    if hi >= 2 and hi > en:
        return "hi"
    if en >= 2 and en > hi:
        return "en"
    return current


def _fallback_result(r, lang):
    d = r["decision"]
    amt, emi, n = r.get("offer_amount_text", ""), r.get("offer_emi_text", ""), r.get("tenure_months", "")
    tips = r.get("tips_hi" if lang == "hi" else "tips", "") or r.get("tips", "")
    if lang == "hi":
        if d == "pre_approved":
            return (f"Achhi khabar! Is pre-screening ke hisaab se aap {amt} ke loan ki aage ki jaanch ke liye "
                    f"eligible lagte hain, {n} mahine ke liye, EMI lagbhag {emi} mahina. "
                    "Yeh sirf pre-screening hai, final approval nahi.")
        if d == "needs_review":
            return (f"Aapki {amt} ki request, {n} mahine mein, EMI lagbhag {emi}, mumkin lagti hai. "
                    "Ise aage ek loan advisor ko dekhna hoga.")
        if d == "counter_offer":
            return (f"Aapki maangi hui rakam se EMI aapki kamaai ke aadhe se zyada ho jaati. "
                    f"{amt}, {n} mahine ke liye, EMI lagbhag {emi} mahina, aapki kamaai mein fit ho sakta hai. "
                    "Kya yeh theek rahega?")
        return ("Abhi aapki batayi jaankari ke hisaab se hum yeh loan nahi de paayenge. "
                + (f"Isme madad karega: {tips}. " if tips else "") + "Kuch samay baad dobara try zaroor kijiye.")
    if d == "pre_approved":
        return (f"Good news! Based on this pre-screening you look eligible for further review for {amt} over "
                f"{n} months, with an EMI of about {emi} a month. This is a pre-screening, not a final approval.")
    if d == "needs_review":
        return (f"Your request for {amt} over {n} months, EMI about {emi}, looks possible. "
                "A loan advisor would need to review it further.")
    if d == "counter_offer":
        return (f"The amount you asked for would take more than half your income in EMIs. "
                f"A smaller loan of {amt} over {n} months, EMI about {emi} a month, would fit your estimated "
                "capacity. Would that work?")
    return ("Based on what you shared, we can't offer this loan right now. "
            + (f"What would help: {tips}. " if tips else "") + "Please do try again later.")
