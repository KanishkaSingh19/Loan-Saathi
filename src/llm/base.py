"""What every LLM provider looks like, and JSON parsing that survives real model output.

The LLM never decides anything in Loan-Saathi: it extracts quoted answers and phrases replies. So a
provider only has to turn (system prompt, user message, JSON schema) into a JSON object, or fail.
"""
import json
import re


class LLMError(Exception):
    """A provider failed (network, HTTP error, empty reply)."""


class InvalidJSON(LLMError):
    """The provider answered, but not with a JSON object."""


class Provider:
    """Interface: `name`, `model`, and `complete(system, user, schema) -> dict` (async)."""

    name = "provider"
    model = ""

    async def complete(self, system, user, schema):  # pragma: no cover - interface
        raise NotImplementedError


_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*(.*?)\s*```\s*$", re.S)


def parse_json(text):
    """A JSON object from model output: plain JSON, JSON inside ``` fences, or JSON after a short preamble.
    Raises InvalidJSON when there is no object to be found."""
    if not isinstance(text, str) or not text.strip():
        raise InvalidJSON("empty reply")
    t = text.strip()
    m = _FENCE.match(t)
    if m:
        t = m.group(1).strip()
    try:
        out = json.loads(t)
    except ValueError:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            raise InvalidJSON(f"no JSON object in reply: {t[:80]!r}") from None
        try:
            out = json.loads(t[start:end + 1])
        except ValueError as e:
            raise InvalidJSON(f"unparseable JSON: {e}") from None
    if not isinstance(out, dict):
        raise InvalidJSON("JSON is not an object")
    return out
