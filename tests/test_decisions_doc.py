"""docs/decisions.md stays true: every file it names exists, and every figure it quotes matches the report it
names (so the record can't drift from what the scripts measured).

Run with `python -m pytest tests/test_decisions_doc.py`.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")


def report(name):
    return json.loads((ROOT / "reports" / name).read_text(encoding="utf-8"))


def test_every_path_named_exists():
    paths = set(re.findall(r"`((?:src|tests|reports|models|config|deploy|docs)/[\w./-]+)`", DOC))
    missing = [p for p in paths if not (ROOT / p).exists()]
    assert not missing, missing


def test_quoted_figures_match_their_reports():
    dev, held = report("extraction_benchmark.json"), report("extraction_benchmark_heldout.json")
    assert f"{dev['correct']}/{dev['cases']} on the dev set" in DOC
    assert f"first run {held['correct']}/{held['cases']}" in DOC
    length = report("call_length.json")["summary"]
    r = length["random"]
    assert f"{r['full_turns_mean']} → {r['short_turns_mean']} on {r['applicants']} seeded random" in DOC
    total = sum(g["applicants"] for g in length.values())
    assert all(g["same_decision"] == g["same_band"] == g["same_offer"] == g["applicants"] for g in length.values())
    assert f"all {total}" in DOC
    stall = report("stall_benchmark.json")
    worst = {k: max(v["turn_ms"]) / 1000 for k, v in stall.items()}
    assert f"{round(min(worst.values()), 1)} s instead of {round(max(worst.values()), 1)} s" in DOC
    lat = report("latency_benchmark.json")["summary"]["speech_end_to_first_audio_ms"]["median"]
    assert f"median {lat / 1000:.2f} s with Groq" in DOC
