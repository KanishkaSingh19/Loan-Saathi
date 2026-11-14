"""Web-call conversation manager: a deterministic state machine around Gemini and our model.

    LLM interprets -> our code validates -> caller confirms -> model scores risk
    -> affordability rule decides the amount.

States (TRANSITIONS lists every allowed move; anything else raises InvalidTransition):

    START -> COLLECTING_PROFILE -> VERIFYING_INFORMATION -> CONFIRMED -> ASSESSING_RISK
          -> CHECKING_AFFORDABILITY -> SHOWING_RESULT -> NEXT_STEPS -> COMPLETED
    plus CORRECTION_REQUIRED (caller changes one answer), ERROR_RECOVERY (we keep failing to
    understand, or the assessment failed), HUMAN_HANDOFF (caller asks for a person) and WHAT_IF
    (reserved for the what-if simulator; nothing enters it yet).

Gemini only ever (1) pulls answers out of what the caller said, each with an exact quote of the
caller's words, (2) phrases the next question, (3) classifies a reply to the summary when our
keyword rules can't, and (4) phrases the final result, whose numbers must match ours. It never
chooses a state, a value it can't quote, an amount or a decision. Every value goes through our
parser and checks:

- each field has a status: "validated" (passed our checks) or "confirmed" (the caller confirmed
  the summary). A value that needs the caller's OK first waits in `pending` ("extracted").
  Assessment refuses to run unless the call is in CONFIRMED and every answer is confirmed
  (`require_confirmed`).
- risky numbers are read back before use: a bare "40" for an income, a range ("40-50 hazaar"),
  two amounts in one answer, an English -teen/-ty word, a value far outside the usual range, or a
  Gemini value that differs from the caller's own words.
- a new value that differs from an earlier one is never silently overwritten: Saathi asks which
  one to use. Corrections from the summary change only that field.
- impossible values (negative, zero income, tenure over 10 years...) are refused with a reason.

If Gemini is unavailable the call carries on with scripted questions and our own parser.

The package (one module per concern, each importing only from the ones before it):

    settings   switches and LLM settings          states     states, transitions, stages
    fields     questions, limits, answer patterns texts      what Saathi says, prompts, recovery help
    faq        fixed answers to mid-call questions
    validation checks, formatting, next question  parser     our own parser (no LLM)
    llm        the provider router, extraction    sessions   sessions, summary, buttons, public view
    values     read-backs, contradictions         dialogue   the turn and its state handlers

`import converse` still gives every name (underscore ones too). Setting an attribute on the package
(`converse.SHORT_CALL = False`, `monkeypatch.setattr(converse, "_gemini", fake)`) also sets it in each
submodule that has that name, so the code that reads it sees the change.
"""
import sys
import types

from . import dialogue, faq, fields, llm, parser, sessions, settings, states, texts, validation, values

_SUBMODULES = (settings, states, fields, texts, faq, validation, parser, llm, sessions, values, dialogue)
for _m in _SUBMODULES:
    for _name, _value in vars(_m).items():
        if not (_name.startswith("__") and _name.endswith("__")) and not isinstance(_value, types.ModuleType):
            globals()[_name] = _value
for _m in _SUBMODULES:  # the stdlib and helper modules the old single file exposed (converse.re, converse.norm ...)
    for _name, _value in vars(_m).items():
        if isinstance(_value, types.ModuleType) and _name not in globals():
            globals()[_name] = _value


class _Package(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for m in _SUBMODULES:
            if name in vars(m):
                setattr(m, name, value)

    def __delattr__(self, name):
        super().__delattr__(name)
        for m in _SUBMODULES:
            if name in vars(m):
                delattr(m, name)


sys.modules[__name__].__class__ = _Package
