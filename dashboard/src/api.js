// Empty VITE_API_BASE means same origin (served by the FastAPI server, the tunnel, or the Vite
// proxy). On Vercel, set it to the API's public URL, e.g. https://<user>-loan-saathi.hf.space
const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/+$/, "");
const STORAGE_KEY = "saathi_key";

export function loadKey() {
  try {
    return sessionStorage.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function saveKey(key) {
  try {
    sessionStorage.setItem(STORAGE_KEY, key);
  } catch {
    /* private mode: key lives in memory only */
  }
}

export class Unauthorized extends Error {}

export async function fetchApplications(key, limit = 100) {
  const res = await fetch(`${API_BASE}/v1/applications?limit=${limit}`, { headers: { "X-Webhook-Key": key } });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function sendPrescreen(key, body) {
  const res = await fetch(`${API_BASE}/v1/prescreen`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Webhook-Key": key },
    body: JSON.stringify(body),
  });
  if (res.status === 401) throw new Unauthorized();
  return res.json();
}

async function postJson(key, path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Webhook-Key": key },
    body: JSON.stringify(body),
  });
  if (res.status === 401) throw new Unauthorized();
  return res;
}

async function errorText(res) {
  try {
    const j = await res.json();
    return j.detail || `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

// ---------- web call ----------

// `action` is a button press (confirm, change, correct:<field>, yes, no, choose:<n>, handoff);
// `text` is still sent so the transcript reads naturally.
export async function converse(key, sessionId, text, language = "hi", action = "") {
  const res = await postJson(key, "/v1/converse", { session_id: sessionId || "", text, language, action });
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function transcribe(key, wavBlob, language = "hi") {
  const form = new FormData();
  form.append("audio_file", wavBlob, "speech.wav");
  form.append("language_code", language === "en" ? "en-IN" : "hi-IN");
  const res = await fetch(`${API_BASE}/v1/stt`, { method: "POST", headers: { "X-Webhook-Key": key }, body: form });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) throw new Error(await errorText(res));
  return (await res.json()).transcript || "";
}

export async function speak(key, text) {
  const res = await postJson(key, "/v1/tts", { text: text.slice(0, 5000), language: "hi-en" });
  if (!res.ok) throw new Error(await errorText(res));
  return res.blob();
}
