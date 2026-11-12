"""No stray control characters in the source. A regex written through a shell heredoc once turned `\\b` into
a backspace character (U+0008), which silently made patterns never match."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"\t", "\n", "\r"}


def test_no_control_characters_in_python_or_js_sources():
    bad = []
    for pattern in ("src/**/*.py", "tests/**/*.py", "dashboard/src/**/*.js", "dashboard/src/**/*.jsx"):
        for p in ROOT.glob(pattern):
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if any(ord(ch) < 32 and ch not in ALLOWED for ch in line):
                    bad.append(f"{p.relative_to(ROOT)}:{n}")
    assert not bad, f"control characters in: {bad}"
