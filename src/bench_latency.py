"""Voice latency benchmark: a scripted Hinglish call through the real pipeline, timed per stage.

    python -I -X utf8 src/bench_latency.py --base http://127.0.0.1:8000 --key <WEBHOOK_KEY>

For each caller turn it (1) synthesises the caller's sentence with Gnani TTS, (2) streams that audio in
real time into the server's /ws/stt (Gnani streaming STT), then times:
    speech end -> final transcript      (includes Gnani's end-of-speech silence window)
    transcript -> Saathi's reply        (/v1/converse: parsing, Gemini if configured, the model)
    reply -> first audio byte           (/v1/tts/stream: Gnani streaming TTS)
    speech end -> first audio byte      (what a caller waits through, minus browser playback start)

The caller is a synthetic voice (Gnani TTS) and the client runs next to the server, so this measures the
server pipeline, not a real phone line or a real caller's speech. Results go to
reports/latency_benchmark.json and the Evaluation Lab.
"""
import argparse
import asyncio
import json
import platform
import statistics
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "latency_benchmark.json"
CALLER = ["Mujhe teen lakh rupaye ka loan chahiye, dukaan ke liye.", "Do saal mein chukaunga.", "Meri salary chaalis hazaar hai.",
          "Meri kirana ki dukaan hai.", "Das saal se.", "Ek loan chal raha hai.", "Aath hazaar mahina EMI hai.",
          "Dedh lakh baaki hai.", "Do loan liye hain pehle.", "Nahi.", "Nahi.", "Apna ghar hai.", "Paintees saal."]


def pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))] if v else None


async def synth(client, base, key, text):
    """Caller audio: Gnani TTS at 24 kHz, downsampled to 16 kHz by linear interpolation."""
    r = await client.post(f"{base}/v1/tts/stream", headers={"X-Webhook-Key": key},
                          json={"text": text, "language": "hi-en", "spell_numbers": False})
    r.raise_for_status()
    raw = r.content
    s = [int.from_bytes(raw[i:i + 2], "little", signed=True) for i in range(0, len(raw) - 1, 2)]
    out = bytearray()
    n = int(len(s) * 16000 / 24000)
    for j in range(n):
        x = j * 1.5
        i = int(x)
        a, b = s[i], s[min(i + 1, len(s) - 1)]
        out += int(a + (b - a) * (x - i)).to_bytes(2, "little", signed=True)
    return bytes(out)


async def transcribe_live(base, key, pcm):
    """Streams pcm in real time, then silence; returns (transcript, speech_end, transcript_at)."""
    ws_url = base.replace("http", "ws", 1) + "/ws/stt"
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"key": key, "language": "hi"}))
        hello = json.loads(await ws.recv())
        if hello.get("type") != "connected":
            raise RuntimeError(hello)
        speech_end = None

        async def send():
            nonlocal speech_end
            for i in range(0, len(pcm), 1024):
                await ws.send(pcm[i:i + 1024])
                await asyncio.sleep(0.032)
            speech_end = time.perf_counter()
            for _ in range(60):                       # up to ~2 s of silence; VAD ends the turn
                await ws.send(b"\x00" * 1024)
                await asyncio.sleep(0.032)

        task = asyncio.create_task(send())
        try:
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 30))
                if m.get("type") == "transcript" and (m.get("text") or "").strip():
                    at = time.perf_counter()
                    return m["text"].strip(), speech_end or at, at
                if m.get("type") == "error":
                    raise RuntimeError(m.get("message"))
        finally:
            task.cancel()


async def run(base, key, turns):
    rows = []
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(f"{base}/v1/converse", headers={"X-Webhook-Key": key}, json={"language": "hi"})
        r.raise_for_status()
        sid = r.json()["session_id"]
        for said in CALLER[:turns]:
            pcm = await synth(client, base, key, said)
            heard, speech_end, heard_at = await transcribe_live(base, key, pcm)
            r = await client.post(f"{base}/v1/converse", headers={"X-Webhook-Key": key},
                                  json={"session_id": sid, "text": heard})
            r.raise_for_status()
            reply_at = time.perf_counter()
            body = r.json()
            first = None
            async with client.stream("POST", f"{base}/v1/tts/stream", headers={"X-Webhook-Key": key},
                                     json={"text": body["reply"], "language": "hi-en"}) as s:
                async for chunk in s.aiter_bytes():
                    if chunk and first is None:
                        first = time.perf_counter()
            ms = lambda a, b: round((b - a) * 1000)  # noqa: E731
            rows.append({"said": said, "heard": heard, "llm": body.get("llm", ""),
                         "speech_end_to_transcript_ms": ms(speech_end, heard_at),
                         "transcript_to_reply_ms": ms(heard_at, reply_at),
                         "reply_to_first_audio_ms": ms(reply_at, first),
                         "speech_end_to_first_audio_ms": ms(speech_end, first)})
            print(f"{rows[-1]['speech_end_to_first_audio_ms']:>6} ms  {said}  ->  {heard}", flush=True)
            if body.get("state") != "COLLECTING_PROFILE":
                break
    return rows


def summarise(rows):
    out = {}
    for k in ("speech_end_to_transcript_ms", "transcript_to_reply_ms", "reply_to_first_audio_ms",
              "speech_end_to_first_audio_ms"):
        v = [r[k] for r in rows if r.get(k) is not None]
        out[k] = {"median": statistics.median(v) if v else None, "p90": pct(v, 0.9), "n": len(v)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--key", required=True)
    ap.add_argument("--turns", type=int, default=len(CALLER))
    a = ap.parse_args()
    rows = asyncio.run(run(a.base.rstrip("/"), a.key, a.turns))
    report = {"ts": time.time(), "python": platform.python_version(), "turns": len(rows),
              "method": ("Scripted Hinglish caller voiced by Gnani TTS, streamed in real time to /ws/stt; client on "
                         "the same machine as the server; Gnani over the public internet. Measures the server "
                         "pipeline, not a phone line or a real caller."),
              "summary": summarise(rows), "rows": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["summary"], indent=1))


if __name__ == "__main__":
    main()
