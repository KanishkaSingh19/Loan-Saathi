// Relative to the page's origin: works behind the tunnel, on localhost, and via the Vite proxy.
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
  const res = await fetch(`/v1/applications?limit=${limit}`, { headers: { "X-Webhook-Key": key } });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function sendPrescreen(key, body) {
  const res = await fetch("/v1/prescreen", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Webhook-Key": key },
    body: JSON.stringify(body),
  });
  if (res.status === 401) throw new Unauthorized();
  return res.json();
}
