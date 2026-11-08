import { useEffect, useRef, useState } from "react";
import { converse, speak, transcribe, Unauthorized } from "../api";
import { startRecording } from "../recorder";
import { SAID_LABELS } from "../constants";
import DecisionBadge from "./DecisionBadge";

// Browser web call: mic -> Gnani Prisma (/v1/stt) -> Gemini + our model (/v1/converse)
// -> Gnani Timbre (/v1/tts) -> speaker. Typing works too, for testing without speech keys.

const ORDER = ["loan_amount", "monthly_income", "work", "years_in_work", "other_loans_count",
  "other_loans_outstanding", "other_loans_emi", "housing", "age", "tenure_months"];
const OPTIONAL = new Set(["tenure_months", "other_loans_outstanding"]); // shown only once mentioned
const MONEY = new Set(["loan_amount", "monthly_income", "other_loans_emi", "other_loans_outstanding"]);
const rupees = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

function show(field, value) {
  if (MONEY.has(field)) return rupees.format(value);
  if (field === "years_in_work" || field === "age") return `${value} yrs`;
  if (field === "tenure_months") return `${value} months`;
  return String(value);
}

// Mirrors converse._skip(): questions that don't apply given earlier answers.
function notNeeded(field, answers) {
  if (field === "other_loans_emi") return answers.other_loans_count === 0;
  if (field === "years_in_work") return /retir|student|unemploy|pension/i.test(answers.work || "");
  return false;
}

const BUSY_TEXT = {
  starting: "Connecting…",
  listening: "Listening… tap again when you finish",
  transcribing: "Turning speech into text…",
  thinking: "Saathi is thinking…",
  speaking: "Saathi is speaking…",
};

export default function CallScreen({ apiKey, onUnauthorized, onOpenPrescreen }) {
  const [lang, setLang] = useState("hi");
  const [call, setCall] = useState(null);
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState("");
  const [voiceOn, setVoiceOn] = useState(true);
  const [voiceNote, setVoiceNote] = useState("");
  const [text, setText] = useState("");
  const recorder = useRef(null);
  const audio = useRef(null);
  const bottom = useRef(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" }); // returns a Promise in new Chromium: never return it
  }, [messages, busy]);
  useEffect(() => () => {
    recorder.current?.cancel();
    audio.current?.pause();
  }, []);

  const fail = (err) => {
    if (err instanceof Unauthorized) return onUnauthorized();
    setError(err.message || String(err));
  };

  const say = async (reply) => {
    if (!voiceOn || !reply) return;
    setBusy("speaking");
    try {
      const blob = await speak(apiKey, reply);
      const url = URL.createObjectURL(blob);
      audio.current?.pause();
      audio.current = new Audio(url);
      await new Promise((resolve) => {
        audio.current.onended = audio.current.onerror = audio.current.onpause = resolve;
        audio.current.play().catch(resolve);
      });
      URL.revokeObjectURL(url);
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized();
      setVoiceNote(`Voice replies are off: ${err.message}. The call carries on in text.`);
      setVoiceOn(false);
    }
  };

  const handle = async (reply, state) => {
    setCall(state);
    setMessages((m) => [...m, { who: "saathi", text: reply, llm: state.llm }]);
    setBusy(null);
    await say(reply);
    setBusy(null);
  };

  const begin = async () => {
    audio.current?.pause();
    setMessages([]);
    setError("");
    setVoiceNote("");
    setBusy("starting");
    try {
      const r = await converse(apiKey, "", "", lang);
      await handle(r.reply, r);
    } catch (err) {
      setBusy(null);
      fail(err);
    }
  };

  const send = async (said) => {
    const clean = said.trim();
    if (!clean || !call || call.done) return;
    setMessages((m) => [...m, { who: "caller", text: clean }]);
    setText("");
    setError("");
    setBusy("thinking");
    try {
      const r = await converse(apiKey, call.session_id, clean, lang);
      await handle(r.reply, r);
    } catch (err) {
      setBusy(null);
      fail(err);
    }
  };

  const toggleMic = async () => {
    setError("");
    if (busy === "listening") {
      setBusy("transcribing");
      try {
        const { wav, seconds } = await recorder.current.stop();
        recorder.current = null;
        if (seconds < 0.5) throw new Error("That was too short. Tap the mic, speak, then tap again.");
        const heard = await transcribe(apiKey, wav, lang);
        if (!heard.trim()) throw new Error("I couldn't hear anything. Try again, or type your answer.");
        await send(heard);
      } catch (err) {
        setBusy(null);
        fail(err);
      }
      return;
    }
    audio.current?.pause();
    try {
      recorder.current = await startRecording();
      setBusy("listening");
    } catch {
      setError("The microphone isn't available. Allow mic access, or type your answer below.");
    }
  };

  const active = call && !call.done;
  const result = call?.result;
  const answers = call?.answers || {};
  const asking = call?.asking;

  return (
    <main className="layout call-layout">
      <section className="panel call-panel">
        <div className="call-head">
          <div>
            <h2>Web call</h2>
            <p className="muted small">Gnani Prisma hears, Gemini understands, our model decides, Timbre speaks.</p>
          </div>
          <div className="call-controls">
            <select value={lang} onChange={(e) => setLang(e.target.value)} disabled={!!active} aria-label="Language">
              <option value="hi">Hinglish</option>
              <option value="en">English</option>
            </select>
            <label className="toggle">
              <input type="checkbox" checked={voiceOn} onChange={(e) => setVoiceOn(e.target.checked)} />
              Speak replies
            </label>
            <button className="btn primary" onClick={begin} disabled={busy !== null && busy !== "speaking"}>
              {call ? "New call" : "Start call"}
            </button>
          </div>
        </div>

        <div className="chat" aria-live="polite">
          {!call && !busy && (
            <div className="empty">
              <p>Start a call to talk to Loan Saathi like a first-time borrower would.</p>
              <p className="muted small">Speak with the mic, or type your answers to test without speech keys.</p>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`bubble ${m.who}`}>
              {m.text}
              {m.who === "saathi" && m.llm === "fallback" && i > 0 && (
                <small title="Gemini was unavailable or its answer was rejected, so a scripted reply was used">scripted</small>
              )}
            </div>
          ))}
          {busy && busy !== "speaking" && <div className="typing muted small">{BUSY_TEXT[busy]}</div>}
          {busy === "speaking" && <div className="typing muted small">{BUSY_TEXT.speaking}</div>}
          <div ref={bottom} />
        </div>

        {error && <p className="call-error" role="alert">{error}</p>}
        {voiceNote && <p className="muted small call-note">{voiceNote}</p>}

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send(text);
          }}
        >
          <button
            type="button"
            className={`mic${busy === "listening" ? " on" : ""}`}
            onClick={toggleMic}
            disabled={!active || (busy !== null && busy !== "listening" && busy !== "speaking")}
            aria-label={busy === "listening" ? "Stop recording and send" : "Speak your answer"}
            aria-pressed={busy === "listening"}
          >
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z" />
            </svg>
          </button>
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={active ? "…or type what the caller says" : call ? "Call finished. Start a new call to try again" : "Start a call first"}
            disabled={!active || busy === "listening"}
            maxLength={1000}
          />
          <button type="submit" className="btn" disabled={!active || !text.trim() || (busy && busy !== "speaking")}>
            Send
          </button>
        </form>
      </section>

      <aside className="panel detail-panel">
        <div className="detail">
          <section>
            <h3>Answers so far</h3>
            <dl className="said">
              {ORDER.filter((f) => !OPTIONAL.has(f) || answers[f] !== undefined).map((f) => (
                <div key={f} className={asking === f ? "asking" : ""}>
                  <dt>{SAID_LABELS[f] || f}</dt>
                  <dd>
                    {answers[f] !== undefined ? (
                      show(f, answers[f])
                    ) : call?.skipped?.includes(f) ? (
                      <span className="muted">skipped</span>
                    ) : notNeeded(f, answers) ? (
                      <span className="muted">not needed</span>
                    ) : asking === f ? (
                      <span className="accent">asking…</span>
                    ) : (
                      <span className="muted">–</span>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </section>

          {result && (
            <section className="call-result">
              <div className="detail-head">
                <h3>Decision</h3>
                <DecisionBadge decision={result.decision} />
              </div>
              {result.offer_amount > 0 && (
                <div className="offer">
                  <div><small>Offer</small><strong>{result.offer_amount_text}</strong></div>
                  <div><small>Tenure</small><strong>{result.tenure_months} months</strong></div>
                  <div><small>EMI</small><strong>{result.offer_emi_text}/mo</strong></div>
                </div>
              )}
              {call.prescreen_id && (
                <button className="btn" onClick={() => onOpenPrescreen(call.prescreen_id)}>
                  Open full breakdown in the live feed
                </button>
              )}
            </section>
          )}
          <p className="muted small">
            Gemini only understands answers and phrases replies. The loan amount, EMI and decision come
            from our model and the 50% affordability rule, and every value is checked before it's used.
          </p>
        </div>
      </aside>
    </main>
  );
}
