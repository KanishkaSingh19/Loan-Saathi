"""Runs every end-to-end suite in src/ under pytest (each is a standalone script with its own server).

    python -m pytest -q            # everything: the spoken-number table + all suites

Each script runs in its own process because they configure the server through environment variables
at import time. test_postgres needs a real DATABASE_URL and is skipped without one.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUITES = sorted(p.name for p in (ROOT / "src").glob("test_*.py"))


@pytest.mark.parametrize("script", SUITES)
def test_suite(script):
    if script == "test_postgres.py" and not os.environ.get("DATABASE_URL", "").strip():
        pytest.skip("needs a real DATABASE_URL")
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", "-W", "ignore", str(ROOT / "src" / script)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    tail = "\n".join((r.stdout + r.stderr).splitlines()[-25:])
    assert r.returncode == 0, f"{script} failed:\n{tail}"
