"""The LLM provider layer (src/llm): parsing, order, retry, deadline, cache, and one LLM wait per turn.

No network: providers are faked. Run with `python -m pytest tests/test_llm.py`.
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("WEBHOOK_KEY", "llm-test-key")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["DATABASE_URL"] = ""

import llm  # noqa: E402
from llm import InvalidJSON, LLMError, LLMUnavailable, Router, parse_json  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class Fake(llm.Provider):
    def __init__(self, name, replies, delay=0.0, model="m"):
        self.name, self.model, self.replies, self.delay, self.calls = name, model, list(replies), delay, 0

    async def complete(self, system, user, schema):
        self.calls += 1
        await asyncio.sleep(self.delay)
        r = self.replies.pop(0) if self.replies else {"ok": True}
        if isinstance(r, Exception):
            raise r
        return r


# ---- JSON from real model output ----
@pytest.mark.parametrize("text, want", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('```\n{"a": 1}\n```', {"a": 1}),
    ('Sure! Here it is: {"a": 1} Hope that helps.', {"a": 1}),
])
def test_parse_json_tolerates_fences_and_preambles(text, want):
    assert parse_json(text) == want


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2]", "{broken"])
def test_parse_json_refuses_what_is_not_an_object(text):
    with pytest.raises(InvalidJSON):
        parse_json(text)


# ---- order, fallback, retry ----
def test_groq_first_then_gemini_on_failure():
    groq, gemini = Fake("groq", [LLMError("HTTP 503")]), Fake("gemini", [{"x": 2}])
    out, who = run(Router([groq, gemini]).call("s", "u", {}))
    assert (out, who) == ({"x": 2}, "gemini") and groq.calls == 1 and gemini.calls == 1


def test_one_retry_on_invalid_json_then_success():
    groq = Fake("groq", [InvalidJSON("fence soup"), {"x": 1}])
    out, who = run(Router([groq]).call("s", "u", {}))
    assert who == "groq" and out == {"x": 1} and groq.calls == 2


def test_invalid_json_twice_moves_to_next_provider():
    groq, gemini = Fake("groq", [InvalidJSON("a"), InvalidJSON("b")]), Fake("gemini", [{"x": 3}])
    out, who = run(Router([groq, gemini]).call("s", "u", {}))
    assert who == "gemini" and groq.calls == 2


def test_no_provider_or_all_failing_raises_unavailable():
    with pytest.raises(LLMUnavailable):
        run(Router([]).call("s", "u", {}))
    with pytest.raises(LLMUnavailable):
        run(Router([Fake("groq", [LLMError("x")]), Fake("gemini", [LLMError("y")])]).call("s", "u", {}))


# ---- the hard deadline ----
def test_deadline_cuts_a_slow_provider():
    slow = Fake("groq", [{"late": True}], delay=5)
    t0 = time.monotonic()
    with pytest.raises(LLMUnavailable):
        run(Router([slow], deadline_s=0.3).call("s", "u", {}))
    assert time.monotonic() - t0 < 1.0


def test_deadline_covers_fallback_too():
    groq = Fake("groq", [LLMError("down")], delay=0.25)
    gemini = Fake("gemini", [{"late": True}], delay=5)
    t0 = time.monotonic()
    with pytest.raises(LLMUnavailable):
        run(Router([groq, gemini], deadline_s=0.5).call("s", "u", {}))
    assert time.monotonic() - t0 < 1.2, "one deadline for the whole call, not per provider"


# ---- the cache ----
def test_cache_hit_skips_the_provider_and_key_includes_prompt_version():
    groq = Fake("groq", [{"x": 1}, {"x": 2}])
    r = Router([groq])
    assert run(r.call("s", "u", {}, prompt_version="v1")) == ({"x": 1}, "groq")
    assert run(r.call("s", "u", {}, prompt_version="v1")) == ({"x": 1}, "groq") and groq.calls == 1
    assert run(r.call("s", "u", {}, prompt_version="v2"))[0] == {"x": 2} and groq.calls == 2
    assert run(r.call("s", "other input", {}, prompt_version="v1"))[0] == {"ok": True}
    k = llm.ResponseCache.key
    assert k("groq", "a", "v1", "s", "u", {}) != k("groq", "b", "v1", "s", "u", {}), "model is part of the key"


# ---- Groq's request ----
def test_groq_provider_sends_json_mode_and_parses_fenced_content(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            seen.update(url=url, body=json, headers=headers)
            import httpx
            return httpx.Response(200, json={"choices": [{"message": {"content": '```json\n{"reply": "hi"}\n```'}}]})

    monkeypatch.setattr(llm.groq.httpx, "AsyncClient", Client)
    out = run(llm.GroqProvider("gsk_test", "llama-test").complete("sys", "user", {"type": "object"}))
    assert out == {"reply": "hi"}
    assert seen["body"]["response_format"] == {"type": "json_object"} and seen["body"]["model"] == "llama-test"
    assert seen["headers"]["Authorization"] == "Bearer gsk_test" and seen["url"].endswith("/chat/completions")
    assert '"type": "object"' in seen["body"]["messages"][0]["content"], "the schema rides in the system prompt"
    assert "reasoning_effort" not in seen["body"]
    run(llm.GroqProvider("gsk_test", "qwen/qwen3.8-27b").complete("sys", "user", {}))
    assert seen["body"]["reasoning_effort"] == "none"
    run(llm.GroqProvider("gsk_test", "openai/gpt-oss-20b").complete("sys", "user", {}))
    assert seen["body"]["reasoning_effort"] == "low"



# ---- the server's order ----
def test_server_router_is_groq_then_gemini(monkeypatch):
    import server
    monkeypatch.setattr(server, "GROQ_API_KEY", "gsk")
    monkeypatch.setattr(server, "GEMINI_API_KEY", "gem")
    monkeypatch.setattr(server, "GROQ_MODELS", ["only-one"])
    assert [p.name for p in server.llm_router().providers] == ["groq", "gemini"]
    monkeypatch.setattr(server, "GROQ_MODELS", ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"])
    providers = server.llm_router().providers
    assert [(p.name, getattr(p, "model", None)) for p in providers] == [
        ("groq", "qwen/qwen3.8-27b"), ("groq", "openai/gpt-oss-20b"), ("gemini", server.GEMINI_MODEL)]
    monkeypatch.setattr(server, "GROQ_API_KEY", "")
    assert [p.name for p in server.llm_router().providers] == ["gemini"]
    monkeypatch.setattr(server, "GEMINI_API_KEY", "")
    assert not server.llm_router().available


# ---- a turn never waits on two LLM calls in a row ----
class CountingRouter:
    available = True

    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    async def call(self, system, user, schema, prompt_version=""):
        self.calls += 1
        return dict(self.reply), "groq"


def test_confirm_turn_uses_the_llm_once_and_phrases_the_result_from_the_script():
    import assessment
    import converse as cv
    import scoring
    import server
    s, _ = cv.start("en")
    for k, v in (("loan_amount", 100000), ("tenure_months", 24), ("monthly_income", 40000), ("work", "kirana shop"),
                 ("other_loans_count", 0), ("past_loans", 0), ("ever_refused", 0), ("housing", "own house"),
                 ("age", 35)):
        cv._set(s, k, v, str(v))
    cv.transition(s, cv.VERIFYING)
    router = CountingRouter({"intent": "confirm", "field": None})
    # "umm" is neither yes nor no, so reading the intent needs the LLM; the result phrasing then must not wait
    # on a second call in the same turn.
    reply = run(cv.turn(s, "umm", server.decide, server.build_applicant, lambda *a: None, llm=router,
                        assess=scoring.score, safety=assessment.safety_pre))
    assert s["state"] == cv.RESULT and router.calls == 1, router.calls
    assert reply.startswith(cv._fallback_result(s["result"], "en")[:30]), "scripted result reply"
    trail = s["llm_trail"]
    assert [(t["purpose"], t["provider"]) for t in trail] == [("intent", "groq"), ("phrase_result", "fallback")]
    assert "one LLM call per turn" in trail[-1]["why"] and s["llm"][-1] == "fallback"


def test_extraction_records_which_provider_answered():
    import converse as cv
    import server
    s, _ = cv.start("en")
    router = CountingRouter({"answers": [{"field": "loan_amount", "value": "300000", "quote": "3 lakh"}],
                             "reply": "", "asking": "tenure_months", "language": "en"})
    run(cv.turn(s, "3 lakh", server.decide, server.build_applicant, lambda *a: None, llm=router))
    assert s["answers"]["loan_amount"] == 300000 and s["llm"][-1] == "groq"
    assert s["llm_trail"][-1]["provider"] == "groq" and s["llm_trail"][-1]["purpose"] == "extract"


def test_groq_disabled_keeps_a_configured_key_unused():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    code = "import sys; sys.path.insert(0, 'src'); import server; print(repr(server.GROQ_API_KEY))"
    env = {**__import__("os").environ, "GROQ_API_KEY": "gsk_configured", "GROQ_DISABLED": "1", "WEBHOOK_KEY": "k",
           "DATABASE_URL": ""}
    out = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], cwd=root, env=env, capture_output=True,
                         text=True, timeout=120)
    assert out.stdout.strip().splitlines()[-1] == "''", out.stderr[-500:]
    env["GROQ_DISABLED"] = "0"
    out = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code], cwd=root, env=env, capture_output=True,
                         text=True, timeout=120)
    assert out.stdout.strip().splitlines()[-1] == "'gsk_configured'", out.stderr[-500:]
