"""No live Groq calls under pytest: they are slow, rate-limited and not deterministic. The tests that exercise
the LLM layer use fakes (tests/test_llm.py). Set LIVE_LLM_TESTS=1 to use the real GROQ_API_KEY from .env.
GROQ_DISABLED is inherited by the suites tests/test_suites.py runs in subprocesses."""
import os

if not os.environ.get("LIVE_LLM_TESTS"):
    os.environ["GROQ_DISABLED"] = "1"

# tests/suites/ holds the end-to-end scripts: each configures the server at import and runs as its own process
# (tests/test_suites.py), so pytest must not import them itself.
collect_ignore_glob = ["suites/*"]
