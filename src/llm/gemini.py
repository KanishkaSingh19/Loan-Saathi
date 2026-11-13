"""Gemini, the fallback provider. The HTTP call itself stays in converse._gemini (tests replace it), so
this class only adapts that function to the Provider interface."""
from .base import InvalidJSON, Provider


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key, model, send):
        """`send(api_key, model, system, user, schema)` makes the call and returns a dict (async)."""
        self.api_key, self.model, self._send = api_key, model, send

    async def complete(self, system, user, schema):
        out = await self._send(self.api_key, self.model, system, user, schema)
        if not isinstance(out, dict):
            raise InvalidJSON("Gemini returned non-object JSON")
        return out
