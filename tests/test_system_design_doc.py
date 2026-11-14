"""docs/system-design.md stays true: every path it names exists, its route table covers every route the server
has (and names none it doesn't), every storage table is described, and its figures match the reports.

Run with `python -m pytest tests/test_system_design_doc.py`.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "system-design.md").read_text(encoding="utf-8")
SERVER = (ROOT / "src" / "server.py").read_text(encoding="utf-8")


def test_every_path_named_exists():
    paths = set(re.findall(r"`((?:src|tests|reports|models|config|deploy|docs|dashboard)/[\w./-]*\w)/?`", DOC))
    missing = [p for p in paths if not (ROOT / p).exists()]
    assert not missing, missing


def test_the_route_table_matches_the_server():
    server = {p for p in re.findall(r'@app\.(?:get|post|patch|put|delete|websocket)\("(/(?:v1|ws|health)[^"]*)"',
                                    SERVER)}
    documented = set(re.findall(r"`(?:GET|POST|PATCH|WS)(?:/(?:GET|POST|PATCH))* (/[^` ,]+)", DOC))
    documented |= {"/v1/tts", "/v1/tts/stream", "/v1/post-call", "/v1/study/survey"} & set(
        re.findall(r"`POST (/v1/[\w/-]+)`", DOC))
    # "/v1/study/*" covers the study routes.
    server_named = {p for p in server if not p.startswith("/v1/study/")} | {"/v1/study/*"}
    documented = {p for p in documented if not p.startswith("/v1/study/")} | {"/v1/study/*"}
    assert not server_named - documented, f"routes the doc misses: {server_named - documented}"
    assert not documented - server_named, f"routes the doc invents: {documented - server_named}"


def test_every_storage_table_is_described():
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SERVER))
    assert tables and all(f"`{t}`" in DOC for t in tables), tables - {t for t in tables if f"`{t}`" in DOC}


def test_quoted_figures_match_their_reports():
    metrics = json.loads((ROOT / "models" / "metrics.json").read_text(encoding="utf-8"))
    assert f"AUC {metrics['voice']['auc']:.3f}" in DOC
    meta = json.loads((ROOT / "models" / "voice_model_meta.json").read_text(encoding="utf-8"))
    assert f"{len(meta['features'])} features" in DOC
    length = json.loads((ROOT / "reports" / "call_length.json").read_text(encoding="utf-8"))["summary"]["random"]
    assert f"{length['full_turns_mean']} → {length['short_turns_mean']} on {length['applicants']}" in DOC
    lat = json.loads((ROOT / "reports" / "latency_benchmark.json").read_text(encoding="utf-8"))
    assert f"median {lat['summary']['speech_end_to_first_audio_ms']['median'] / 1000:.2f} s with Groq" in DOC
    dev = json.loads((ROOT / "reports" / "extraction_benchmark.json").read_text(encoding="utf-8"))
    held = json.loads((ROOT / "reports" / "extraction_benchmark_heldout.json").read_text(encoding="utf-8"))
    assert f"dev {dev['correct']}/{dev['cases']}, held-out {held['correct']}/{held['cases']}" in DOC
