"""Smoke tests for the Gnani speech API proxy routes; no external API calls are made."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
os.environ.setdefault("GROQ_DISABLED", "1")  # no live Groq calls from a suite (LIVE_LLM_TESTS=1 run aside)
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


class FakeGnaniClient:
    calls = []

    def __init__(self, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/stt/v3"):
            return server.httpx.Response(
                200,
                json={"success": True, "transcript": "namaste"},
            )
        return server.httpx.Response(
            200,
            content=b"RIFF-test-audio",
            headers={"content-type": "audio/wav"},
        )


if __name__ == "__main__":
    server.WEBHOOK_KEY = "test-webhook-key"
    server.GNANI_STT_API_KEY = "test-stt-key"
    server.GNANI_TTS_API_KEY = "test-tts-key"
    FakeGnaniClient.calls.clear()
    client = TestClient(server.app)

    with patch.object(server.httpx, "AsyncClient", FakeGnaniClient):
        stt = client.post(
            "/v1/stt",
            headers={"X-Webhook-Key": "test-webhook-key"},
            files={"audio_file": ("sample.wav", b"audio-data", "audio/wav")},
            data={"language_code": "hi-IN", "format": "transcribe"},
        )
        assert stt.status_code == 200, stt.text
        assert stt.json()["transcript"] == "namaste"

        tts = client.post(
            "/v1/tts",
            headers={"X-Webhook-Key": "test-webhook-key"},
            json={"text": "Namaste", "language": "hi-IN"},
        )
        assert tts.status_code == 200, tts.text
        assert tts.headers["content-type"] == "audio/wav"
        assert tts.content == b"RIFF-test-audio"

        unauthorized = client.post(
            "/v1/tts",
            headers={"X-Webhook-Key": "wrong-key"},
            json={"text": "Namaste"},
        )
        assert unauthorized.status_code == 401

    assert len(FakeGnaniClient.calls) == 2
    stt_url, stt_request = FakeGnaniClient.calls[0]
    tts_url, tts_request = FakeGnaniClient.calls[1]
    assert stt_url.endswith("/stt/v3")
    assert stt_request["headers"]["X-API-Key-ID"] == "test-stt-key"
    assert tts_url.endswith("/api/v1/tts/inference")
    assert tts_request["headers"]["X-API-Key-ID"] == "test-tts-key"
    print("Speech API proxy tests passed.")
