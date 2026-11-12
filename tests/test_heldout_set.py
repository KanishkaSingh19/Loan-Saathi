"""The held-out extraction set stays separate from the dev set: enough cases, none copied from the dev set,
every field covered, and its report covers the whole file.

Run with `python -m pytest tests/test_heldout_set.py`.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV = json.loads((ROOT / "tests" / "extraction_cases.json").read_text(encoding="utf-8"))["cases"]
HELD = json.loads((ROOT / "tests" / "extraction_heldout.json").read_text(encoding="utf-8"))["cases"]


def _norm(t):
    return " ".join(str(t).lower().split())


def test_at_least_forty_held_out_cases_none_from_the_dev_set():
    assert len(HELD) >= 40
    dev = {(c["field"], _norm(c["said"])) for c in DEV}
    copied = [c["said"] for c in HELD if (c["field"], _norm(c["said"])) in dev]
    assert not copied, copied
    assert len({(c["field"], _norm(c["said"])) for c in HELD}) == len(HELD), "duplicates inside the held-out set"


def test_held_out_cases_cover_every_dev_field_and_both_languages():
    assert {c["field"] for c in DEV} <= {c["field"] for c in HELD}
    assert {c["lang"] for c in HELD} == {"hi", "en"}
    assert any(any("ऀ" <= ch <= "ॿ" for ch in c["said"]) for c in HELD), "Devanagari cases"


def test_the_held_out_report_is_for_this_file_and_reported_apart():
    rep = json.loads((ROOT / "reports" / "extraction_benchmark_heldout.json").read_text(encoding="utf-8"))
    assert rep["set"] == "heldout" and rep["source"] == "tests/extraction_heldout.json"
    assert rep["cases"] == len(HELD) and {r["said"] for r in rep["rows"]} == {c["said"] for c in HELD}
