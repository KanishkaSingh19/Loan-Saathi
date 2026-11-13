"""Calls providers in order (Groq, then Gemini) under one hard deadline, with a small response cache.

    router = Router([groq, gemini], deadline_s=3.0)
    obj, provider = await router.call(system, user, schema, prompt_version="v3")

- One deadline for the whole call, across providers and retries: a voice turn never waits longer.
- One retry on invalid JSON (same provider) if time remains; any other failure moves to the next one.
- In-memory cache keyed on provider + model + prompt version + input (system, user, schema), so a
  repeated question (the same read-back, a retry after a network blip) costs nothing.
- When every provider fails or time runs out it raises LLMUnavailable, and the caller uses our
  deterministic parser or scripted reply.
"""
import asyncio
import hashlib
import json
import time
from collections import OrderedDict

from .base import InvalidJSON


class LLMUnavailable(Exception):
    """No provider answered in time; use the deterministic path."""


class ResponseCache:
    def __init__(self, max_items=500, ttl_s=600):
        self.max_items, self.ttl_s = max_items, ttl_s
        self._items = OrderedDict()
        self.hits = self.misses = 0

    @staticmethod
    def key(provider, model, prompt_version, system, user, schema):
        raw = json.dumps([provider, model, prompt_version, system, user, schema], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key):
        item = self._items.get(key)
        if item is None or time.time() - item[0] > self.ttl_s:
            self._items.pop(key, None)
            self.misses += 1
            return None
        self._items.move_to_end(key)
        self.hits += 1
        return item[1]

    def put(self, key, value):
        self._items[key] = (time.time(), value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)


class Router:
    def __init__(self, providers, deadline_s=3.0, cache=None, retries_on_invalid_json=1):
        self.providers = [p for p in providers if p is not None]
        self.deadline_s = float(deadline_s)
        self.cache = cache if cache is not None else ResponseCache()
        self.retries = retries_on_invalid_json

    @property
    def available(self):
        return bool(self.providers)

    async def call(self, system, user, schema, prompt_version="", deadline_s=None):
        """(parsed JSON object, provider name). Raises LLMUnavailable."""
        if not self.providers:
            raise LLMUnavailable("no LLM provider configured")
        for p in self.providers:  # a cached answer from any provider, in order of preference
            hit = self.cache.get(ResponseCache.key(p.name, p.model, prompt_version, system, user, schema))
            if hit is not None:
                return dict(hit), p.name
        budget = self.deadline_s if deadline_s is None else min(float(deadline_s), self.deadline_s)
        end = time.monotonic() + budget
        errors = []
        for p in self.providers:
            for attempt in range(1 + self.retries):
                left = end - time.monotonic()
                if left <= 0.05:
                    raise LLMUnavailable(f"deadline of {budget:.1f}s reached ({'; '.join(errors) or 'no answer'})")
                try:
                    out = await asyncio.wait_for(p.complete(system, user, schema), timeout=left)
                except InvalidJSON as e:
                    errors.append(f"{p.name}: invalid JSON ({e})")
                    continue                       # retry the same provider once
                except asyncio.TimeoutError:
                    raise LLMUnavailable(f"{p.name} didn't answer within {budget:.1f}s") from None
                except Exception as e:  # noqa: BLE001  (network, HTTP, quota: try the next provider)
                    errors.append(f"{p.name}: {type(e).__name__}: {str(e)[:120]}")
                    break
                self.cache.put(ResponseCache.key(p.name, p.model, prompt_version, system, user, schema), out)
                return out, p.name
        raise LLMUnavailable("; ".join(errors) or "no provider answered")
