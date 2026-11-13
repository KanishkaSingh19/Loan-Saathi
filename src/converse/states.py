"""The call's states, the allowed moves between them, and the product pipeline stages."""

import time


# ---------- states ----------

START = "START"
COLLECTING = "COLLECTING_PROFILE"
VERIFYING = "VERIFYING_INFORMATION"
CORRECTION = "CORRECTION_REQUIRED"
CONFIRMED = "CONFIRMED"
ASSESSING = "ASSESSING_RISK"
AFFORDABILITY = "CHECKING_AFFORDABILITY"
RESULT = "SHOWING_RESULT"
WHAT_IF = "WHAT_IF"
NEXT_STEPS = "NEXT_STEPS"
COMPLETED = "COMPLETED"
RECOVERY = "ERROR_RECOVERY"
HANDOFF = "HUMAN_HANDOFF"

TRANSITIONS = {
    START: {COLLECTING},
    COLLECTING: {VERIFYING, RECOVERY, HANDOFF},
    VERIFYING: {CONFIRMED, CORRECTION, RECOVERY, HANDOFF},
    CORRECTION: {VERIFYING, COLLECTING, RECOVERY, HANDOFF},
    CONFIRMED: {ASSESSING, RECOVERY},
    ASSESSING: {AFFORDABILITY, RECOVERY},
    AFFORDABILITY: {RESULT, RECOVERY},
    RESULT: {NEXT_STEPS, WHAT_IF, HANDOFF, COMPLETED},
    WHAT_IF: {RESULT, NEXT_STEPS, HANDOFF, COMPLETED},  # "what if I take 2 lakh?"
    NEXT_STEPS: {COMPLETED, HANDOFF, WHAT_IF},
    RECOVERY: {COLLECTING, VERIFYING, HANDOFF, COMPLETED},
    HANDOFF: {COMPLETED},
    COMPLETED: {WHAT_IF},  # back to "what if I take 2 lakh?" after saying goodbye (needs a result)
}
STATES = tuple(TRANSITIONS)

# The product pipeline: LISTEN -> EXTRACT -> VALIDATE -> CONFIRM -> ASSESS -> EXPLAIN -> SIMULATE -> HANDOFF.
# Every state belongs to one stage (stage()); the call screen shows the stage, the state machine stays
# the source of truth.
STAGES = ("LISTEN", "EXTRACT", "VALIDATE", "CONFIRM", "ASSESS", "EXPLAIN", "SIMULATE", "HANDOFF")
STAGE_OF = {
    START: "LISTEN",
    COLLECTING: "EXTRACT",          # VALIDATE while a read-back is waiting (see stage())
    CORRECTION: "VALIDATE",         # a value is being fixed
    VERIFYING: "CONFIRM",
    CONFIRMED: "ASSESS", ASSESSING: "ASSESS", AFFORDABILITY: "ASSESS",
    RESULT: "EXPLAIN",
    WHAT_IF: "SIMULATE",
    NEXT_STEPS: "HANDOFF", HANDOFF: "HANDOFF",
}


def stage(s):
    """Which pipeline stage the call is in."""
    st = s["state"]
    if st == COLLECTING and s.get("pending"):
        return "VALIDATE"   # "I heard ₹40,000, is that right?" / "which amount?"
    if st == RECOVERY:
        return "CONFIRM" if s.get("recover_to") == VERIFYING else "EXTRACT"
    if st == COMPLETED:
        if s.get("advisor_case") or any(t["to"] in (NEXT_STEPS, HANDOFF) for t in s.get("trail", [])):
            return "HANDOFF"
        if any(t["to"] == WHAT_IF for t in s.get("trail", [])):
            return "SIMULATE"
        return "EXPLAIN" if s.get("result") else "EXTRACT"
    return STAGE_OF.get(st, "EXTRACT")
FINISHED = (COMPLETED, HANDOFF)


class InvalidTransition(Exception):
    pass


class NotConfirmed(Exception):
    """Raised when something tries to assess a profile the caller hasn't confirmed."""


def transition(s, to, why=""):
    if to not in TRANSITIONS.get(s["state"], ()):
        raise InvalidTransition(f"{s['state']} -> {to}")
    s["trail"].append({"from": s["state"], "to": to, "why": why, "ts": round(time.time(), 3)})
    s["state"] = to
