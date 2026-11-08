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
