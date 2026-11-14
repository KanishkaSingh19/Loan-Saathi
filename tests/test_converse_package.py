"""src/converse/ is a package, but behaves like the single module it replaced: every name is reachable as
converse.<name>, setting a flag on the package reaches the code that reads it, and each submodule imports only
the ones listed before it (no cycles).

Run with `python -m pytest tests/test_converse_package.py`.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import converse as cv  # noqa: E402

ORDER = ["settings", "states", "fields", "texts", "faq", "validation", "parser", "llm", "sessions", "values", "dialogue"]


def test_each_module_imports_only_earlier_ones():
    for i, name in enumerate(ORDER):
        tree = ast.parse((ROOT / "src" / "converse" / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                assert node.module in ORDER[:i], f"{name} imports {node.module}"


def test_setting_a_flag_on_the_package_reaches_the_code(monkeypatch):
    monkeypatch.setattr(cv, "SHORT_CALL", False)
    assert cv.validation.SHORT_CALL is False and cv.settings.SHORT_CALL is False
    s, _ = cv.start("hi")
    for k, v in {"loan_amount": 100000, "tenure_months": 24, "monthly_income": 30000}.items():
        cv._set(s, k, v, str(v))
    assert cv.next_field(s) == "work"          # the full order: SHORT_CALL off really took effect


def test_a_patch_is_undone_everywhere(monkeypatch):
    real = cv._gemini

    async def fake(*a):
        return {}
    with monkeypatch.context() as m:
        m.setattr(cv, "_gemini", fake)
        assert cv.llm._gemini is fake
    assert cv._gemini is real and cv.llm._gemini is real


def test_public_names_are_the_same_objects_as_in_their_module():
    for name in ORDER:
        mod = getattr(cv, name)
        for k, v in vars(mod).items():
            if not k.startswith("__") and not isinstance(v, type(sys)):
                assert getattr(cv, k) is v or k in ("llm",), (name, k)
