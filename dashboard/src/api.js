// Empty VITE_API_BASE means same origin (served by the FastAPI server, the tunnel, or the Vite
// proxy). On Vercel, set it to the API's public URL, e.g. https://<user>-loan-saathi.hf.space
const API_BASE = (import.meta.env?.VITE_API_BASE || "").replace(/\/+$/, "");
const STORAGE_KEY = "saathi_key";

export function apiBase() {
  return API_BASE;
}

/** Wakes a sleeping free-tier API while the caller reads the landing page. Fire and forget: "no-cors"
 * means no CORS error even before ALLOWED_ORIGINS is set; the request still reaches the server. */
export function wakeServer(fetchImpl = globalThis.fetch) {
  try {
    return Promise.resolve(fetchImpl(`${API_BASE}/health`, { mode: "no-cors", cache: "no-store" })).catch(() => null);
  } catch {
    return Promise.resolve(null);
  }
}

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

// The call this browser started: its id and the token the server handed out with the greeting. Borrowers need
// no key; the token lets them (and only them) continue their own call.
const CALL = { session: "", token: "" };

export function setCall(session, token) {
  CALL.session = session || "";
  CALL.token = token || "";
}

export function callAuth() {
  return { session: CALL.session, token: CALL.token };
}

/**
 * Headers for a request: the staff key when signed in, and the call token when a call is running.
 * @param {string} [key] @returns {Record<string, string>}
 */
export function authHeaders(key = "") {
  /** @type {Record<string, string>} */
  const h = {};
  if (key) h["X-Webhook-Key"] = key;
  if (CALL.token) {
    h["X-Call-Session"] = CALL.session;
    h["X-Call-Token"] = CALL.token;
  }
  return h;
}

export async function fetchApplications(key, limit = 100) {
  const res = await fetch(`${API_BASE}/v1/applications?limit=${limit}`, { headers: authHeaders(key) });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function sendPrescreen(key, body) {
  const res = await fetch(`${API_BASE}/v1/prescreen`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(key) },
    body: JSON.stringify(body),
  });
  if (res.status === 401) throw new Unauthorized();
  return res.json();
}

async function postJson(key, path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders(key) },
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
/**
 * @param {string} key @param {string} sessionId @param {string} text @param {string} [language]
 * @param {string} [action] @param {{participant?: string, persona?: string, consent?: boolean}} [study] study fields and the storage consent, when starting a call
 */
export async function converse(key, sessionId, text, language = "hi", action = "", study = {}) {
  if (!sessionId) setCall("", "");  // a new call: forget the previous call's token
  const res = await postJson(key, "/v1/converse", { session_id: sessionId || "", text, language, action, ...study });
  if (!res.ok) throw new ApiError(await errorText(res), res.status);
  const data = await res.json();
  if (data.call_token) setCall(data.session_id, data.call_token);
  return data;
}

export async function transcribe(key, wavBlob, language = "hi") {
  const form = new FormData();
  form.append("audio_file", wavBlob, "speech.wav");
  form.append("language_code", language === "en" ? "en-IN" : "hi-IN");
  const res = await fetch(`${API_BASE}/v1/stt`, { method: "POST", headers: authHeaders(key), body: form });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) throw new Error(await errorText(res));
  return (await res.json()).transcript || "";
}

export async function speak(key, text, lang = "hi") {
  const res = await postJson(key, "/v1/tts", { text: text.slice(0, 5000), language: lang === "en" ? "en-IN" : "hi-en" });
  if (!res.ok) throw new Error(await errorText(res));
  return res.blob();
}

// ---------- what-if simulator ----------

export class ApiError extends Error {
  /** @param {string} message @param {number} [status] */
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}

// Read-only what-if for a completed pre-screen. Server errors come back as ApiError with a message
// that is safe to show the applicant (e.g. "The tenure must be between 3 months and 10 years").
/**
 * @param {string} key
 * @param {string} prescreenId
 * @param {{loanAmount?: number, tenureMonths?: number, scenarios?: boolean}} [options]
 */
export async function simulate(key, prescreenId, { loanAmount, tenureMonths, scenarios = false } = {}) {
  const body = { prescreen_id: prescreenId, scenarios };
  if (loanAmount != null) body.loan_amount = loanAmount;
  if (tenureMonths != null) body.tenure_months = tenureMonths;
  const res = await postJson(key, "/v1/simulate", body);
  if (!res.ok) throw new ApiError(await errorText(res));
  return res.json();
}

/**
 * "Check an offer you got": the real yearly rate (fees included), total cost and whether the EMI fits.
 * Values may be typed the way people say them ("2 lakh", "2 saal"); the server reads them.
 * @param {string} key
 * @param {{loanAmount: string, tenureMonths: string, emi: string, processingFee?: string, language?: string}} o
 */
export async function checkOffer(key, { loanAmount, tenureMonths, emi, processingFee = "", language = "en" }) {
  const body = { loan_amount: loanAmount, tenure_months: tenureMonths, emi, language };
  if (processingFee) body.processing_fee = processingFee;
  const res = await postJson(key, "/v1/offer-check", body);
  if (!res.ok) throw new ApiError(await errorText(res), res.status);
  return res.json();
}

// ---------- advisor cases ----------

/**
 * Save a summary for a human loan advisor. The server rebuilds every number itself; the simulation
 * is sent only as the loan amount and tenure to recompute.
 * @param {string} key
 * @param {{prescreenId?: string, sessionId?: string, question?: string, loanAmount?: number, tenureMonths?: number}} c
 */
export async function createAdvisorCase(key, { prescreenId, sessionId, question, loanAmount, tenureMonths }) {
  const body = { prescreen_id: prescreenId || "", session_id: sessionId || "", question: question || "" };
  if (loanAmount != null) body.loan_amount = loanAmount;
  if (tenureMonths != null) body.tenure_months = tenureMonths;
  const res = await postJson(key, "/v1/advisor-cases", body);
  if (!res.ok) throw new ApiError(await errorText(res));
  return res.json();
}

export async function fetchAdvisorCases(key) {
  const res = await fetch(`${API_BASE}/v1/advisor-cases`, { headers: authHeaders(key) });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) throw new ApiError("Couldn't load advisor cases. Please try again.");
  return res.json();
}

/**
 * An advisor picks up, closes or reopens a case.
 * @param {string} key @param {string} caseId
 * @param {{status: "open"|"in_review"|"closed", advisor?: string, note?: string}} change
 */
export async function updateAdvisorCase(key, caseId, change) {
  const res = await fetch(`${API_BASE}/v1/advisor-cases/${encodeURIComponent(caseId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders(key) },
    body: JSON.stringify(change),
  });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) throw new ApiError(await errorText(res), res.status);
  return res.json();
}

// ---------- user study (STUDY.md) ----------

async function getJson(key, path) {
  const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders(key) });
  if (res.status === 401) throw new Unauthorized();
  if (!res.ok) throw new ApiError(await errorText(res), res.status);
  return res;
}

/** One measured voice turn (milliseconds), stored for the Evaluation Lab. Never blocks the call. */
export function reportLatency(key, report) {
  postJson(key, "/v1/metrics/latency", report).catch(() => {});
}

export async function fetchEvaluation(key) {
  return (await getJson(key, "/v1/evaluation")).json();
}

export async function studyPersonas(key) {
  return (await (await getJson(key, "/v1/study/personas")).json()).personas;
}

export async function studyReport(key) {
  return (await getJson(key, "/v1/study/report")).json();
}

export async function studyCsv(key) {
  return (await getJson(key, "/v1/study/report.csv")).blob();
}

/** @param {string} key @param {object} survey */
export async function submitSurvey(key, survey) {
  const res = await postJson(key, "/v1/study/survey", survey);
  if (!res.ok) throw new ApiError(await errorText(res), res.status);
  return res.json();
}
