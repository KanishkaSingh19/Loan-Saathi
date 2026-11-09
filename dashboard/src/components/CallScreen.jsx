import { useEffect, useRef, useState } from "react";
import { ApiError, converse, speak, transcribe, Unauthorized } from "../api";
import { startRecording } from "../recorder";
import { SAID_LABELS } from "../constants";
import AdvisorPanel from "./AdvisorPanel";
import Assessment from "./Assessment";
import { StudySetup, StudySurvey } from "./Study";
import DecisionBadge from "./DecisionBadge";

// Browser web call: mic -> Gnani Prisma (/v1/stt) -> Gemini + our model (/v1/converse)
// -> Gnani Timbre (/v1/tts) -> speaker. Typing works too, for testing without speech keys.

const ORDER = ["loan_amount", "tenure_months", "monthly_income", "work", "years_in_work", "other_loans_count",
  "other_loans_emi", "other_loans_outstanding", "past_loans", "ever_refused", "ever_late", "late_last_year",
  "late_over_month", "overdue_now", "housing", "age"];
const YES_NO = new Set(["ever_refused", "ever_late", "late_last_year", "late_over_month", "overdue_now"]);

// Readable names for the server's conversation states (converse.py).
const STATE_LABELS = {
  COLLECTING_PROFILE: "Collecting answers",
  VERIFYING_INFORMATION: "Waiting for confirmation",
  CORRECTION_REQUIRED: "Correcting an answer",
  CONFIRMED: "Confirmed",
  ASSESSING_RISK: "Assessing risk",
  CHECKING_AFFORDABILITY: "Checking affordability",
  SHOWING_RESULT: "Result",
  NEXT_STEPS: "Next steps",
  COMPLETED: "Completed",
  ERROR_RECOVERY: "Recovering",
  HUMAN_HANDOFF: "Saved for a loan advisor",
  WHAT_IF: "Trying options",
};
const MONEY = new Set(["loan_amount", "monthly_income", "other_loans_emi", "other_loans_outstanding"]);
const rupees = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

function show(field, value) {
  if (MONEY.has(field)) return rupees.format(value);
  if (field === "years_in_work" || field === "age") return `${value} yrs`;
  if (field === "tenure_months") return `${value} months`;
  if (YES_NO.has(field)) return value ? "Yes" : "No";
  return String(value);
}

// Mirrors converse._skip(): questions that don't apply given earlier answers.
function notNeeded(field, answers) {
  if (field === "other_loans_emi" || field === "other_loans_outstanding") return answers.other_loans_count === 0;
  // Mirrors the history funnel in converse._skip().
  const neverBorrowed = answers.past_loans === 0 && !answers.other_loans_count;
  if (field === "ever_late") return neverBorrowed;
  if (field === "late_last_year" || field === "late_over_month") return neverBorrowed || answers.ever_late === 0;
  if (field === "overdue_now") return answers.other_loans_count === 0 || neverBorrowed;
  if (field === "years_in_work") return /retir|student|unemploy|pension/i.test(answers.work || "");
  return false;
}

// Per-month money fields on the confirmation card.
const PER_MONTH = new Set(["monthly_income", "other_loans_emi"]);

// One quiet status line for where the conversation is: listen -> understand -> verify -> confirm.
// Derived only from what the server and the recorder report; nothing is staged for show.
function callPhase(call, busy) {
  if (!call) return busy === "starting" ? { id: "listen", word: "Connecting", text: "Starting your call" } : null;
  if (busy === "listening") return { id: "listen", word: "Listening", text: "Tap the mic again when you finish" };
  if (busy === "transcribing") return { id: "listen", word: "Listening", text: "Turning your speech into text" };
  if (busy === "thinking") return { id: "understand", word: "Understanding", text: "Reading what you said" };
  if (call.done) return null;
  if (call.pending || call.state === "CORRECTION_REQUIRED")
    return { id: "verify", word: "Verifying", text: "Checking one detail with you" };
  if (call.state === "VERIFYING_INFORMATION")
    return { id: "confirm", word: "Confirm", text: "Check your details before anything is estimated" };
  if (busy === "speaking") return { id: "listen", word: "Saathi is speaking", text: "You can answer any time" };
  if (call.state === "COLLECTING_PROFILE" || call.state === "ERROR_RECOVERY")
    return { id: "listen", word: "Your turn", text: "Speak or type your answer" };
  return null;
}

const BUSY_TEXT = {
  starting: "Connecting…",
  listening: "Listening… tap again when you finish",
  transcribing: "Turning speech into text…",
  thinking: "Saathi is thinking…",
  speaking: "Saathi is speaking…",
};

export default function CallScreen({ apiKey, onUnauthorized, onOpenPrescreen, studyMode = false }) {
  const [lang, setLang] = useState("hi");
  const [call, setCall] = useState(null);
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState("");
  const [voiceOn, setVoiceOnState] = useState(true);
  const voiceRef = useRef(true);
  const setVoiceOn = (on) => {
    voiceRef.current = on;
    setVoiceOnState(on);
  };
  const inputRef = useRef(null);
  const focusInput = useRef(false);
  const [voiceNote, setVoiceNote] = useState("");
  const [text, setText] = useState("");
  const recorder = useRef(null);
  const audio = useRef(null);
  const bottom = useRef(null);
  const assessmentRef = useRef(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" }); // returns a Promise in new Chromium: never return it
  }, [messages, busy]);
  useEffect(() => () => {
    recorder.current?.cancel();
    audio.current?.pause();
  }, []);

  useEffect(() => {
    if (call && focusInput.current && !busy) {
      focusInput.current = false;
      inputRef.current?.focus();
    }
  }, [call, busy]);

  const [retry, setRetry] = useState(null); // { text, action } of a turn the server never answered
  const [studyInfo, setStudyInfo] = useState(null); // { participant, persona, language } in study mode
  const [sttFailures, setSttFailures] = useState(0);

  // Applicants never see technical errors; details go to the console for whoever runs the demo.
  const fail = (err, friendly) => {
    if (err instanceof Unauthorized) return onUnauthorized();
    console.warn("[web call]", err);
    setError(friendly || "Saathi couldn't reach the server. Please check your connection and try again.");
  };

  const say = async (reply) => {
    if (!voiceRef.current || !reply) return;
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
      console.warn("[web call] voice reply failed", err);
      setVoiceNote("Voice replies aren't available right now, so Saathi will reply in text.");
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

  const begin = async ({ typing = false } = {}) => {
    if (typing) setVoiceOn(false);
    focusInput.current = typing;
    audio.current?.pause();
    setMessages([]);
    setError("");
    setVoiceNote("");
    setBusy("starting");
    try {
      const callLang = studyInfo?.language || lang;
      if (studyMode) {
        setLang(callLang);
        setSttFailures(0);
      }
      const r = await converse(apiKey, "", "", callLang, "",
        studyMode && studyInfo ? { participant: studyInfo.participant, persona: studyInfo.persona } : {});
      await handle(r.reply, r);
    } catch (err) {
      setBusy(null);
      fail(err);
    }
  };

  const send = async (said, action = "") => {
    const clean = said.trim();
    if ((!clean && !action) || !call || call.done) return;
    if (!retry) setMessages((m) => [...m, { who: "caller", text: clean }]);
    setText("");
    setError("");
    setRetry(null);
    setBusy("thinking");
    try {
      const r = await converse(apiKey, call.session_id, clean, lang, action);
      await handle(r.reply, r);
    } catch (err) {
      setBusy(null);
      if (err instanceof ApiError && err.status === 404) {
        fail(err, "This call has expired (it was idle too long or the server restarted). Tap New call to start again.");
        return;
      }
      setRetry({ text: clean, action });
      fail(err, "Saathi couldn't reach the server, so your last answer wasn't heard. Tap Try again.");
    }
  };

  const toggleMic = async () => {
    setError("");
    if (busy === "listening") {
      setBusy("transcribing");
      try {
        const { wav, seconds } = await recorder.current.stop();
        recorder.current = null;
        if (seconds < 0.5) {
          setBusy(null);
          setError("That was too short. Tap the mic, speak, then tap again.");
          return;
        }
        let heard = "";
        try {
          heard = await transcribe(apiKey, wav, lang);
        } catch (err) {
          setBusy(null);
          setSttFailures((n) => n + 1);
          fail(err, "I couldn't hear that clearly. Please tap the mic and try again, or type your answer.");
          return;
        }
        if (!heard.trim()) {
          setBusy(null);
          setSttFailures((n) => n + 1);
          setError("I couldn't hear anything. Please try again, or type your answer.");
          return;
        }
        await send(heard);
      } catch (err) {
        setBusy(null);
        fail(err, "Something went wrong with the recording. Please try again, or type your answer.");
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
  const fields = call?.fields || {};
  const buttonsOn = active && call?.buttons?.length > 0 && (busy === null || busy === "speaking");
  const verifying = call?.state === "VERIFYING_INFORMATION";
  const phase = callPhase(call, busy);
  const cardRows = verifying ? (call.summary || []).filter((r) => r.card ?? r.critical) : [];
  const confirmBtn = call?.buttons?.find((b) => b.action === "confirm");
  const changeBtn = call?.buttons?.find((b) => b.action === "change");
  const cardOn = verifying && cardRows.length > 0 && confirmBtn && changeBtn;

  if (!call && !busy && !studyMode) {
    return (
      <main className="layout call-layout">
        <section className="hero" aria-labelledby="hero-title">
          <span className="hero-brand">Loan-Saathi</span>
          <h2 id="hero-title">
            Understand your loan.
            <br />
            <span>Before you take it.</span>
          </h2>
          <p className="lede">
            Talk to Saathi in your own words. It will understand your financial situation, check affordability, and
            help you explore safer options.
          </p>
          <div className="hero-actions">
            <button type="button" className="wave-cta" onClick={() => begin()}>
              <span className="wave-badge" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="#fff">
                  <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z" />
                </svg>
              </span>
              Talk to Saathi
            </button>
            <button type="button" className="btn outline type-cta" onClick={() => begin({ typing: true })}>
              Type instead
            </button>
          </div>
          <div className="hero-options">
            <div className="hero-langs" role="group" aria-label="Language">
              {[["hi", "Hinglish"], ["en", "English"]].map(([id, label]) => (
                <button key={id} type="button" aria-pressed={lang === id} onClick={() => setLang(id)}>
                  {label}
                </button>
              ))}
            </div>
            <label className="toggle">
              <input type="checkbox" checked={voiceOn} onChange={(e) => setVoiceOn(e.target.checked)} />
              Speak replies aloud
            </label>
          </div>
          {error && <p className="call-error" role="alert">{error}</p>}
          <p className="hero-disclaimer">This is an estimated pre-screening, not a lender approval.</p>
        </section>
      </main>
    );
  }

  return (
    <main className="layout call-layout">
      <section className="panel call-panel">
        <div className="call-head">
          <div>
            <h2>
              Talk to Saathi
              {call?.state && <span className="state-pill">{STATE_LABELS[call.state] || call.state}</span>}
            </h2>
            <p className="muted small">
              Answer a few questions by voice or text. Saathi reads your details back before estimating anything.
            </p>
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
            <button className="btn primary" onClick={() => begin()}
              disabled={(busy !== null && busy !== "speaking") || (studyMode && !studyInfo)}>
              {call ? "New call" : "Start call"}
            </button>
          </div>
        </div>

        <div className="chat" aria-live="polite">
          {!call && !busy && (
            <div className="empty">
              <p>Find out how much you could comfortably borrow, in about two minutes.</p>
              <p className="muted small">Tap Start call, then speak with the mic or type. Hindi, Hinglish or English.</p>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`bubble ${m.who}`}>
              <span className="bubble-who">{m.who === "caller" ? "You said" : "Saathi"}</span>
              {m.text}
            </div>
          ))}
          {cardOn && (
            <section className="confirm-card" aria-labelledby="confirm-title">
              <header>
                <h3 id="confirm-title">Here&rsquo;s what I understood</h3>
                <p>From what you told Saathi. Nothing is estimated until you confirm.</p>
              </header>
              <dl>
                {cardRows.map((r) => (
                  <div key={r.field} className={r.status === "default" ? "is-default" : ""}>
                    <dt>{r.label}</dt>
                    <dd>
                      {r.text}
                      {r.status !== "default" && PER_MONTH.has(r.field) && <span className="per"> / month</span>}
                    </dd>
                  </div>
                ))}
              </dl>
              <div className="confirm-actions">
                <button type="button" className="btn primary" disabled={!buttonsOn}
                  onClick={() => send(confirmBtn.text, "confirm")}>
                  <span aria-hidden="true">✓</span> Everything looks correct
                </button>
                <button type="button" className="btn" disabled={!buttonsOn}
                  onClick={() => send(changeBtn.text, "change")}>
                  <span aria-hidden="true">✎</span> Correct something
                </button>
              </div>
            </section>
          )}
          {busy && busy !== "speaking" && (
            <div className="typing" role="status">
              <span className="dots" aria-hidden="true"><i /><i /><i /></span>
              {BUSY_TEXT[busy]}
            </div>
          )}
          <div ref={bottom} />
        </div>

        {buttonsOn && !cardOn && (
          <div className="quick-replies" role="group" aria-label="Quick replies">
            {call.buttons.map((b) => (
              <button
                key={b.action}
                type="button"
                className={`btn${["confirm", "yes"].includes(b.action) ? " primary" : ""}`}
                onClick={() => send(b.text, b.action)}
              >
                {b.label}
              </button>
            ))}
          </div>
        )}

        {error && (
          <div className="call-error" role="alert">
            <span>{error}</span>
            {retry && (
              <button type="button" className="btn" onClick={() => send(retry.text, retry.action)}>
                Try again
              </button>
            )}
          </div>
        )}
        {voiceNote && <p className="muted small call-note">{voiceNote}</p>}

        {phase && (
          <p className={`call-status phase-${phase.id}`} aria-live="polite">
            <i aria-hidden="true" />
            <b>{phase.word}</b>
            <span>{phase.text}</span>
          </p>
        )}
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
            ref={inputRef}
            aria-label="Your answer"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={active ? "Type your answer…" : call ? "Call finished. Start a new call to try again" : "Tap Start call first"}
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
          {verifying && !cardOn && (
            <section className="understood">
              <h3>Here&rsquo;s what I understood</h3>
              <dl className="said">
                {call.summary.filter((r) => r.card ?? r.critical).map((r) => (
                  <div key={r.field}>
                    <dt>{r.label}</dt>
                    <dd>{r.status === "default" ? <span className="muted">{r.text}</span> : r.text}</dd>
                  </div>
                ))}
              </dl>
              <p className="muted small">Is this information correct? Nothing is assessed until you confirm.</p>
            </section>
          )}
          <section>
            <h3>What you&rsquo;ve told Saathi</h3>
            <dl className="said">
              {ORDER.map((f) => (
                <div key={f} className={asking === f || call?.correcting === f ? "asking" : ""}>
                  <dt>{SAID_LABELS[f] || f}</dt>
                  <dd>
                    {answers[f] !== undefined ? (
                      <>
                        {show(f, answers[f])}
                        <span className={`field-status ${fields[f]?.status || ""}`}>
                          {fields[f]?.status === "confirmed" ? "confirmed ✓" : "to confirm"}
                        </span>
                      </>
                    ) : call?.pending?.field === f ? (
                      <span className="accent">checking…</span>
                    ) : f === "tenure_months" && call?.skipped?.includes(f) ? (
                      <span className="muted">not given (24 months used)</span>
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
                <h3>Result</h3>
                {result.assessment?.outcome ? (
                  <span className={`badge ${result.assessment.outcome.tone}`}>{result.assessment.outcome.title}</span>
                ) : (
                  <DecisionBadge decision={result.decision} />
                )}
              </div>
              {result.assessment && (
                <button className="btn" onClick={() => assessmentRef.current?.scrollIntoView({ block: "start" })}>
                  See your assessment
                </button>
              )}
              {result.assessment && (
                <button className="btn primary" onClick={() => {
                  const panel = assessmentRef.current?.querySelector(".advisor");
                  panel?.scrollIntoView({ block: "center" });
                  panel?.querySelector("button")?.focus();
                }}>
                  Talk to a Loan Advisor
                </button>
              )}
              {call.prescreen_id && (
                <button className="btn" onClick={() => onOpenPrescreen(call.prescreen_id)}>
                  Open in Pre-screens (advisor view)
                </button>
              )}
            </section>
          )}
          <p className="muted small">
            Saathi checks every number with you first. Calculations use fixed affordability rules; the risk estimate
            comes from a model, never from the chat itself.
          </p>
        </div>
      </aside>

      {studyMode && (!call || call.done) && !(call?.result || call?.advisor_case) ? (
        <section className="panel span-all advisor-only">
          <StudySetup apiKey={apiKey} onReady={setStudyInfo} onUnauthorized={onUnauthorized} disabled={!!active} />
        </section>
      ) : null}
      {studyMode && call && (call.result || call.done) ? (
        <section className="panel span-all advisor-only">
          <StudySurvey key={call.session_id} apiKey={apiKey} sessionId={call.session_id} sttFailures={sttFailures}
            onUnauthorized={onUnauthorized} />
        </section>
      ) : null}

      {call && !result?.assessment && (call.advisor_case || call.state === "COLLECTING_PROFILE"
        || call.state === "VERIFYING_INFORMATION" || call.state === "ERROR_RECOVERY") ? (
        <section className="panel span-all advisor-only">
          <AdvisorPanel
            apiKey={apiKey}
            sessionId={call.session_id}
            existingCase={call.advisor_case || ""}
            onUnauthorized={onUnauthorized}
          />
        </section>
      ) : null}

      {result?.assessment && (
        <section className="panel assessment-panel span-all" ref={assessmentRef}>
          <Assessment
            assessment={result.assessment}
            apiKey={apiKey}
            prescreenId={call.prescreen_id}
            liveSimulation={call.simulation}
            onUnauthorized={onUnauthorized}
            lang={call.language}
            sessionId={call.session_id}
            advisorCase={call.advisor_case || ""}
          />
        </section>
      )}
    </main>
  );
}
