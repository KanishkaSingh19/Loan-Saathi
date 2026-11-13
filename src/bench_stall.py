"""What a caller waits through when the language model hangs. Three web-call turns while every outgoing HTTP
POST is mocked to stall for 30 s (nothing leaves the machine); the turn ends when the code gives up on the
LLM and the deterministic parser answers.

    python -I -X utf8 src/bench_stall.py                          # this checkout -> label "current"
    python -I -X utf8 src/bench_stall.py --src <dir>/src --label before

`--src` points at another copy of src/ (e.g. `git archive main src models reports | tar -x -C <dir>`), so
the same script measures an older version. Results are merged into reports/stall_benchmark.json by label.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "stall_benchmark.json"
SAID = ["mujhe 3 lakh ka loan chahiye", "2 saal mein", "40 hazaar kamata hoon"]


def measure(src):
    """Runs inside a child process whose sys.path starts at `src`."""
    os.environ.update(WEBHOOK_KEY="stall-key", DATABASE_URL="", DB_PATH=str(Path(tempfile.mkdtemp()) / "s.db"),
                      GEMINI_API_KEY="mock-gemini-key", GROQ_API_KEY="", ADVISOR_WEBHOOK_URL="")
    sys.path.insert(0, str(src))
    os.chdir(Path(src).parent)
    import httpx

    async def stall(self, *a, **k):
        await asyncio.sleep(30)

    httpx.AsyncClient.post = stall
    import server
    from fastapi.testclient import TestClient
    server.GEMINI_API_KEY = "mock-gemini-key"
    if hasattr(server, "GROQ_API_KEY"):
        server.GROQ_API_KEY = ""
    c = TestClient(server.app)
    h = {"X-Webhook-Key": "stall-key"}
    sid = c.post("/v1/converse", headers=h, json={"language": "hi"}).json()["session_id"]
    times, r = [], {}
    for said in SAID:
        t0 = time.perf_counter()
        r = c.post("/v1/converse", headers=h, json={"session_id": sid, "text": said}).json()
        times.append(round((time.perf_counter() - t0) * 1000))
    return {"turn_ms": times, "answers": r.get("answers", {})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "src"))
    ap.add_argument("--label", default="current")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        print(json.dumps(measure(a.src)))
        return
    p = subprocess.run([sys.executable, "-I", "-X", "utf8", __file__, "--child", "--src", a.src],
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        sys.exit(p.stderr[-2000:])
    result = json.loads(p.stdout.strip().splitlines()[-1])
    # Never a local path in the report (it would carry the user's folder names).
    result.update(ts=time.time(), src="src" if a.src == str(ROOT / "src") else "another copy of src/ (--src)",
                  method="Every outgoing HTTP POST mocked to stall 30 s; Gemini key set, no Groq key; "
                         "three Hinglish turns through /v1/converse.")
    report = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    report[a.label] = result
    OUT.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(a.label, result["turn_ms"], result["answers"])


if __name__ == "__main__":
    main()
