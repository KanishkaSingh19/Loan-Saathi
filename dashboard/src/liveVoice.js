// Live voice for the web call: the microphone streams to Gnani's streaming STT (through the server's
// /ws/stt, so no Gnani key reaches the browser), and replies stream back from Gnani's TTS
// (/v1/tts/stream) and start playing on the first chunk. Gnani's voice-activity detection ends each
// turn; the caller can interrupt Saathi by speaking.

import { apiBase } from "./api";

export { looksLikeEcho } from "./echo.js";

const MIC_RATE = 16000;
// The worklet turns the microphone into 512-sample 16-bit frames (what Gnani wants) and reports the
// level of each frame, which times the end of the caller's speech and guards barge-in against echo.
const WORKLET = `
class PcmFrames extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Int16Array(512); this.n = 0; this.sum = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      this.sum += s * s;
      if (this.n === 512) {
        this.port.postMessage({ pcm: this.buf.buffer.slice(0), rms: Math.sqrt(this.sum / 512) });
        this.n = 0; this.sum = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-frames", PcmFrames);
`;

export const VOICE_LEVEL = 0.02; // RMS above this counts as the caller speaking

/**
 * Opens the live microphone. onEvent gets Gnani's events ({type: "speech_start" | "transcript" | "error"}).
 * @param {{apiKey: string, lang: string, onEvent: (e: any) => void, onClose?: () => void}} opts
 */
export async function openLiveMic({ apiKey, lang, onEvent, onClose = () => {} }) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const ctx = new AudioContext({ sampleRate: MIC_RATE });
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
  try {
    await ctx.audioWorklet.addModule(url);
  } finally {
    URL.revokeObjectURL(url);
  }
  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-frames");
  source.connect(node);

  const wsUrl = `${apiBase().replace(/^http/, "ws") || `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`}/ws/stt`;
  const ws = new WebSocket(wsUrl);
  ws.binaryType = "arraybuffer";
  const state = { lastVoiceAt: 0, level: 0, open: false, closed: false };

  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Live speech didn't connect")), 10000);
    ws.onopen = () => ws.send(JSON.stringify({ key: apiKey, language: lang }));
    ws.onmessage = (m) => {
      const e = JSON.parse(m.data);
      if (e.type === "connected") {
        clearTimeout(timer);
        state.open = true;
        resolve(undefined);
      } else if (e.type === "error") {
        clearTimeout(timer);
        reject(new Error(e.message || "Live speech failed"));
      }
    };
    ws.onerror = () => {
      clearTimeout(timer);
      reject(new Error("Live speech didn't connect"));
    };
  });

  ws.onmessage = (m) => {
    try {
      onEvent(JSON.parse(m.data));
    } catch {
      /* ignore malformed frames */
    }
  };
  const close = () => {
    if (state.closed) return;
    state.closed = true;
    try { node.port.onmessage = null; source.disconnect(); node.disconnect(); } catch { /* already gone */ }
    stream.getTracks().forEach((t) => t.stop());
    ctx.close().catch(() => {});
    if (ws.readyState <= 1) ws.close();
  };
  ws.onclose = () => {
    state.open = false;
    if (!state.closed) {
      close();
      onClose();
    }
  };
  node.port.onmessage = (e) => {
    state.level = e.data.rms;
    if (e.data.rms > VOICE_LEVEL) state.lastVoiceAt = performance.now();
    if (state.open && ws.readyState === 1) ws.send(e.data.pcm);
  };
  return {
    close,
    commit: () => ws.readyState === 1 && ws.send(JSON.stringify({ type: "commit" })),
    get lastVoiceAt() { return state.lastVoiceAt; },
    get level() { return state.level; },
  };
}

// ---------- playback ----------

let playCtx = null;
function context() {
  if (!playCtx || playCtx.state === "closed") playCtx = new AudioContext();
  if (playCtx.state === "suspended") playCtx.resume();
  return playCtx;
}

function toFloat(int16) {
  const f = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) f[i] = int16[i] / 0x8000;
  return f;
}

/**
 * Plays 16-bit PCM chunks as they arrive. Returns {firstAudio, done, stop}: firstAudio resolves with the
 * performance.now() time the first sound was scheduled; done resolves when playback ends or is stopped.
 * @param {AsyncIterable<Uint8Array>|null} chunks
 * @param {number} rate
 * @param {Int16Array} [whole] a buffer to play instead of chunks (cached fillers)
 */
export function playPcm(chunks, rate, whole = undefined) {
  const ctx = context();
  const sources = new Set();
  let at = 0;
  let stopped = false;
  /** @type {(t: number | null) => void} */
  let firstResolve = () => {};
  /** @type {Promise<number | null>} */
  const firstAudio = new Promise((r) => (firstResolve = r));
  let carry = new Uint8Array(0);

  const schedule = (int16) => {
    if (stopped || !int16.length) return;
    const buf = ctx.createBuffer(1, int16.length, rate);
    buf.copyToChannel(toFloat(int16), 0);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    at = Math.max(at, ctx.currentTime + 0.03);
    src.start(at);
    at += buf.duration;
    sources.add(src);
    src.onended = () => sources.delete(src);
    firstResolve(performance.now());
  };

  const done = (async () => {
    if (whole) schedule(whole);
    else if (chunks) {
      for await (const part of chunks) {
        if (stopped) break;
        const bytes = new Uint8Array(carry.length + part.length);
        bytes.set(carry);
        bytes.set(part, carry.length);
        const even = bytes.length - (bytes.length % 2);
        carry = bytes.slice(even);
        schedule(new Int16Array(bytes.buffer.slice(0, even)));
      }
    }
    firstResolve(null);
    while (!stopped && (sources.size || ctx.currentTime < at)) {
      await new Promise((r) => setTimeout(r, 60));
    }
  })();

  return {
    firstAudio,
    done,
    stop: () => {
      stopped = true;
      sources.forEach((s) => { try { s.stop(); } catch { /* already stopped */ } });
      sources.clear();
    },
  };
}

/** Streams a reply from /v1/tts/stream and plays it as it arrives. */
export async function speakStream(apiKey, text, lang) {
  const res = await fetch(`${apiBase()}/v1/tts/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Webhook-Key": apiKey },
    body: JSON.stringify({ text: text.slice(0, 5000), language: lang === "en" ? "en-IN" : "hi-en" }),
  });
  if (!res.ok || !res.body) throw new Error(`voice stream failed (HTTP ${res.status})`);
  const rate = Number(res.headers.get("X-Sample-Rate")) || 24000;
  const reader = res.body.getReader();
  const chunks = {
    async *[Symbol.asyncIterator]() {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        yield value;
      }
    },
  };
  const playback = playPcm(chunks, rate);
  const stop = playback.stop;
  playback.stop = () => {
    stop();
    reader.cancel().catch(() => {});
  };
  return playback;
}

/** Fetches a whole short clip (a filler like "achha, ek second") once, for instant playback later. */
export async function fetchClip(apiKey, text, lang) {
  const res = await fetch(`${apiBase()}/v1/tts/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Webhook-Key": apiKey },
    body: JSON.stringify({ text, language: lang === "en" ? "en-IN" : "hi-en" }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const bytes = new Uint8Array(await res.arrayBuffer());
  return { pcm: new Int16Array(bytes.buffer.slice(0, bytes.length - (bytes.length % 2))),
    rate: Number(res.headers.get("X-Sample-Rate")) || 24000 };
}
