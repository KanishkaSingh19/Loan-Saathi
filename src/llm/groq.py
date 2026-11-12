"""Groq (OpenAI-compatible chat completions), the primary provider when GROQ_API_KEY is set."""
import json

import httpx

from .base import InvalidJSON, LLMError, Provider, parse_json

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(Provider):
    name = "groq"

    def __init__(self, api_key, model, url=GROQ_URL, timeout=10.0):
        self.api_key, self.model, self.url, self.timeout = api_key, model, url, timeout

    async def complete(self, system, user, schema):
        # JSON mode plus the schema in the system prompt works across Groq's models.
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"{system}\n\nReply with one JSON object only, matching this JSON "
                                              f"schema:\n{json.dumps(schema)}"},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 400,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(self.url, json=body, headers={"Authorization": f"Bearer {self.api_key}"})
        if not r.is_success:
            raise LLMError(f"Groq HTTP {r.status_code}: {r.text[:200]}")
        try:
            text = r.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise InvalidJSON("Groq reply had no message content") from None
        return parse_json(text)
