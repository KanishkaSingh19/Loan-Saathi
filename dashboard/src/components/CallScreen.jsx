import { useEffect, useRef, useState } from "react";
import { ApiError, converse, reportLatency, speak, transcribe, Unauthorized } from "../api";
import { fetchClip, looksLikeEcho, openLiveMic, playPcm, speakStream, VOICE_LEVEL } from "../liveVoice";
import { startRecording } from "../recorder";
import { SAID_LABELS } from "../constants";
import AdvisorPanel from "./AdvisorPanel";
import Assessment from "./Assessment";
import OfferCheck from "./OfferCheck";
import { StudySetup, StudySurvey } from "./Study";
import DecisionBadge from "./DecisionBadge";

// Browser web call. Live: mic -> Gnani streaming STT (/ws/stt, its VAD ends each turn) -> Gemini + our
// model (/v1/converse) -> Gnani streaming TTS (/v1/tts/stream, plays from the first chunk); the caller can
// interrupt. Tap-to-talk (/v1/stt) is the fallback, and typing works too, for testing without speech keys.

// Said while the server works on a slow turn, so the line never goes silent.
const FILLER = { hi: "Achha, ek second.", en: "Okay, one moment." };
const FILLER_AFTER_MS = 900;
const fillerCache = {};

const ORDER = ["loan_amount", "tenure_months", "monthly_income", "work", "years_in_work", "other_loans_count",
  "other_loans_emi", "other_loans_outstanding", "past_loans", "ever_refused", "ever_late", "late_last_year",
  "late_over_month", "overdue_now", "housing", "age"];
const YES_NO = new Set(["ever_refused", "ever_late", "late_last_year", "late_over_month", "overdue_now"]);

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
function callPhase(call, busy, live = false, hearing = false) {
  if (!call) return busy === "starting" ? { id: "listen", word: "Connecting", text: "Starting your call" } : null;
  if (hearing && busy !== "thinking") return { id: "listen", word: "Hearing you", text: "Saathi answers when you pause" };
  if (live && busy === "speaking") return { id: "listen", word: "Saathi is speaking", text: "Just speak to interrupt" };
  if (busy === "listening") return { id: "listen", word: "Listening", text: "Tap the circle again when you finish" };
  if (busy === "transcribing") return { id: "listen", word: "Listening", text: "Turning your speech into text" };
  if (busy === "thinking") return { id: "understand", word: "Understanding", text: "Reading what you said" };
  if (call.done) return null;
  if (call.pending || call.state === "CORRECTION_REQUIRED")
    return { id: "verify", word: "Verifying", text: "Checking one detail with you" };
  if (call.state === "VERIFYING_INFORMATION")
    return { id: "confirm", word: "Confirm", text: "Check your details before anything is estimated" };
  if (busy === "speaking") return { id: "listen", word: "Saathi is speaking", text: "You can answer any time" };
  if (call.state === "COLLECTING_PROFILE" || call.state === "ERROR_RECOVERY")
    return live ? { id: "listen", word: "Listening", text: "Just speak; Saathi answers when you pause" }
      : { id: "listen", word: "Your turn", text: "Speak or type your answer" };
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
  const consent = false; // nothing is stored until the caller says yes in the call room (DPDP)
  const [consentAsked, setConsentAsked] = useState(false);
  const [leaving, setLeaving] = useState(false); // the landing page fading out before the call room
  // Fade the landing page out, then start the call (instantly when reduced motion is preferred).
  const go = (opts) => {
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (still) return begin(opts);
    setLeaving(true);
    setTimeout(() => {
      setLeaving(false);
      begin(opts);
    }, 260);
  };
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
  const [live, setLive] = useState(false);       // hands-free: streaming mic open for the whole call
  const [hearing, setHearing] = useState(false); // the caller is speaking right now (Gnani heard speech)
  const mic = useRef(null);
  const micLang = useRef("");                    // the language live listening was opened with
  const playback = useRef(null);                 // the reply playing now (stoppable for barge-in)
  const speakingText = useRef("");
  const bargedIn = useRef(false);
  const turn = useRef(null);                     // timings of the current voice turn, for latency reports
  const busyRef = useRef(null);
  const sendRef = useRef(null);
  const langRef = useRef("hi");
  busyRef.current = busy;
  langRef.current = lang;
  const bottom = useRef(null);
  const assessmentRef = useRef(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" }); // returns a Promise in new Chromium: never return it
  }, [messages, busy]);
  useEffect(() => () => {
    recorder.current?.cancel();
    audio.current?.pause();
    playback.current?.stop();
    mic.current?.close();
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
    if (err instanceof Unauthorized) {  // a staff key that changed, or a call that has ended
      onUnauthorized();
      setError("This call has ended. Tap New call to start again.");
      return;
    }
    console.warn("[web call]", err);
    setError(friendly || "Saathi couldn't reach the server. Please check your connection and try again.");
  };

  // A measured voice turn goes to the Evaluation Lab (caller stops -> first sound of the reply).
  const report = (firstAudioAt) => {
    const t = turn.current;
    turn.current = null;
    if (!t || !firstAudioAt || !t.replyAt) return;
    const ms = (a, b) => (a && b && b >= a ? Math.round(b - a) : null);
    reportLatency(apiKey, {
      session_id: t.session || "", mode: t.mode, filler_played: !!t.filler,
      speech_end_to_transcript_ms: ms(t.speechEnd, t.transcriptAt),
      transcript_to_reply_ms: ms(t.transcriptAt, t.replyAt),
      reply_to_first_audio_ms: ms(t.replyAt, firstAudioAt),
      speech_end_to_first_audio_ms: ms(t.speechEnd, firstAudioAt),
    });
  };

  const stopSpeaking = () => {
    playback.current?.stop();
    playback.current = null;
    audio.current?.pause();
  };

  const say = async (reply) => {
    if (!voiceRef.current || !reply) {
      turn.current = null;
      return;
    }
    setBusy("speaking");
    speakingText.current = reply;
    bargedIn.current = false;
    try {
      const pb = await speakStream(apiKey, reply, langRef.current);
      playback.current = pb;
      pb.firstAudio.then(report);
      await pb.done;
      return;
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized();
      console.warn("[web call] streamed voice failed, trying the whole reply", err);
    } finally {
      playback.current = null;
      speakingText.current = "";
    }
    try {
      const blob = await speak(apiKey, reply, langRef.current);
      const url = URL.createObjectURL(blob);
      audio.current?.pause();
      audio.current = new Audio(url);
      await new Promise((resolve) => {
        audio.current.onended = audio.current.onerror = audio.current.onpause = resolve;
        audio.current.play().then(() => report(performance.now())).catch(resolve);
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
    if (state.new_call) await begin({ hands: !!mic.current });  // "start a new check": a fresh call
  };

  const begin = async ({ typing = false, hands = false } = {}) => {
    if (typing) setVoiceOn(false);
    focusInput.current = typing;
    stopSpeaking();
    if (hands) startLive(studyInfo?.language || lang);
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
        studyMode && studyInfo ? { participant: studyInfo.participant, persona: studyInfo.persona, consent: true }
          : { consent });
      await handle(r.reply, r);
      if (voiceRef.current && !fillerCache[callLang]) {
        fetchClip(apiKey, FILLER[callLang], callLang).then((c) => (fillerCache[callLang] = c)).catch(() => {});
      }
    } catch (err) {
      setBusy(null);
      fail(err);
    }
  };

  const send = async (said, action = "") => {
    const clean = said.trim();
    // After the call, the caller can still use the options Saathi offers (what-if, advisor, new check).
    if ((!clean && !action) || !call || (call.done && !(call.buttons?.length > 0))) return;
    if (!retry) setMessages((m) => [...m, { who: "caller", text: clean }]);
    setText("");
    setError("");
    setRetry(null);
    setBusy("thinking");
    setHearing(false);
    /** @type {{ done: Promise<void>, stop: () => void } | null} */
    let filler = null;
    const clip = fillerCache[lang];
    const fillerTimer = voiceRef.current && clip && turn.current
      ? setTimeout(() => {
        filler = playPcm(null, clip.rate, clip.pcm);
        if (turn.current) turn.current.filler = true;
      }, FILLER_AFTER_MS)
      : null;
    try {
      const r = await converse(apiKey, call.session_id, clean, lang, action);
      clearTimeout(fillerTimer);
      if (turn.current) Object.assign(turn.current, { replyAt: performance.now(), session: r.session_id });
      if (filler) await filler.done;
      await handle(r.reply, r);
    } catch (err) {
      clearTimeout(fillerTimer);
      turn.current = null;
      setBusy(null);
      if (err instanceof ApiError && err.status === 404) {
        fail(err, "This call has expired (it was idle too long or the server restarted). Tap New call to start again.");
        return;
      }
      setRetry({ text: clean, action });
      fail(err, "Saathi couldn't reach the server, so your last answer wasn't heard. Tap Try again.");
    }
  };

  // Gnani's events from the live microphone. Refs, not state: this handler outlives renders.
  const onLiveEvent = (e) => {
    const m = mic.current;
    if (e.type === "speech_start") {
      setHearing(true);
      const loud = m && performance.now() - m.lastVoiceAt < 400 && m.level > VOICE_LEVEL;
      if (busyRef.current === "speaking" && loud) {   // barge-in: the caller talks over Saathi
        bargedIn.current = true;
        stopSpeaking();
      }
    } else if (e.type === "transcript") {
      const heard = (e.text || "").trim();
      setHearing(false);
      if (!heard) return;
      const now = performance.now();
      if (busyRef.current === "speaking") {
        if (!bargedIn.current && looksLikeEcho(heard, speakingText.current)) return; // Saathi's own voice
        stopSpeaking();
      }
      if (busyRef.current === "thinking" || busyRef.current === "starting") return; // one turn at a time
      turn.current = { mode: "live", speechEnd: m?.lastVoiceAt || now, transcriptAt: now };
      sendRef.current?.(heard);
    } else if (e.type === "error") {
      console.warn("[web call] live speech", e.message);
    }
  };

  const startLive = async (callLang) => {
    if (mic.current) return true;
    try {
      micLang.current = callLang;
      mic.current = await openLiveMic({
        apiKey, lang: callLang, onEvent: (e) => onLiveEvent(e),
        onClose: () => {
          mic.current = null;
          setLive(false);
          setHearing(false);
          setVoiceNote("Live listening stopped. Tap the circle to talk again.");
        },
      });
      setLive(true);
      setVoiceNote("");
      return true;
    } catch (err) {
      console.warn("[web call] live mic unavailable, using tap-to-talk", err);
      mic.current = null;
      setLive(false);
      setVoiceNote("Live listening isn't available here, so tap the circle to talk (or type).");
      return false;
    }
  };

  const stopLive = () => {
    mic.current?.close();
    mic.current = null;
    setLive(false);
    setHearing(false);
  };

  // The call's language can change mid-call ("talk in English please", or the switch in the header).
  // Follow it, and reconnect live listening so speech-to-text hears the new language.
  useEffect(() => {
    if (call?.language && call.language !== langRef.current) setLang(call.language);
  }, [call?.language]);
  useEffect(() => {
    if (mic.current && micLang.current !== lang) {
      stopLive();
      startLive(lang);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang]);

  const toggleMic = async () => {
    setError("");
    if (live) {
      stopLive();
      return;
    }
    if (busy === "listening") {
      setBusy("transcribing");
      try {
        const { wav, seconds } = await recorder.current.stop();
        recorder.current = null;
        if (seconds < 0.5) {
          setBusy(null);
          setError("That was too short. Tap the circle, speak, then tap it again.");
          return;
        }
        let heard = "";
        const tapEnd = performance.now();
        try {
          heard = await transcribe(apiKey, wav, lang);
        } catch (err) {
          setBusy(null);
          setSttFailures((n) => n + 1);
          fail(err, "I couldn't hear that clearly. Please tap the circle and try again, or type your answer.");
          return;
        }
        if (!heard.trim()) {
          setBusy(null);
          setSttFailures((n) => n + 1);
          setError("I couldn't hear anything. Please try again, or type your answer.");
          return;
        }
        turn.current = { mode: "tap", speechEnd: tapEnd, transcriptAt: performance.now() };
        await send(heard);
      } catch (err) {
        setBusy(null);
        fail(err, "Something went wrong with the recording. Please try again, or type your answer.");
      }
      return;
    }
    stopSpeaking();
    try {
      recorder.current = await startRecording();
      setBusy("listening");
    } catch {
      setError("The microphone isn't available. Allow mic access, or type your answer below.");
    }
  };

  sendRef.current = send;
  const orbState = hearing ? "hearing" : busy === "speaking" ? "speaking"
    : ["thinking", "starting", "transcribing"].includes(busy) ? "thinking"
      : live || busy === "listening" ? "listening" : "idle";
  const lastSaathi = [...messages].reverse().find((m) => m.who === "saathi")?.text || "";

  const endCall = () => {  // back to the landing page; nothing about the call stays on screen
    stopLive();
    stopSpeaking();
    recorder.current?.cancel();
    recorder.current = null;
    setCall(null);
    setMessages([]);
    setBusy(null);
    setError("");
    setVoiceNote("");
    setConsentAsked(false);
  };
  const active = call && !call.done;
  const open = call && (!call.done || call.buttons?.length > 0);  // ended, but options are still on offer
  const result = call?.result;
  const answers = call?.answers || {};
  const asking = call?.asking;
  const fields = call?.fields || {};
  const buttonsOn = open && call?.buttons?.length > 0 && (busy === null || busy === "speaking");
  const verifying = call?.state === "VERIFYING_INFORMATION";
  const phase = callPhase(call, busy, live, hearing);
  const cardRows = verifying ? (call.summary || []).filter((r) => r.card ?? r.critical) : [];
  const confirmBtn = call?.buttons?.find((b) => b.action === "confirm");
  const changeBtn = call?.buttons?.find((b) => b.action === "change");
  const cardOn = verifying && cardRows.length > 0 && confirmBtn && changeBtn;

  if (!call && !busy && !studyMode) {
    return (
      <main className="layout call-layout">
        <section className={`hero${leaving ? " leaving" : ""}`} aria-labelledby="hero-title">
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
          <div className="landing-cta">
            <button type="button" className="talk-button" disabled={leaving} onClick={() => go({ hands: true })}>
              <span className="talk-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="30" height="30"><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z" /></svg>
              </span>
              Talk to Saathi
            </button>
            <button type="button" className="link-quiet" disabled={leaving} onClick={() => go({ typing: true })}>
              or type instead
            </button>
          </div>
          <div className="hero-langs" role="group" aria-label="Language">
            {[["hi", "Hinglish"], ["en", "English"]].map(([id, label]) => (
              <button key={id} type="button" aria-pressed={lang === id} onClick={() => setLang(id)}>
                {label}
              </button>
            ))}
          </div>
          {error && <p className="call-error" role="alert">{error}</p>}
          <p className="hero-disclaimer">This is an estimated pre-screening, not a lender approval.</p>
        </section>
      </main>
    );
  }

  return (
    <main className="layout call-layout room-enter">
      <section className="panel call-panel">
        <div className="room-head">
          <p className="room-title">
            {active ? <span className="live-dot" aria-hidden="true" /> : null}
            {active ? "Live call with Saathi" : call ? "Call ended" : "Talk to Saathi"}
            <small>{call ? (call.consent ? "Saved for 30 days" : "Not saved") : ""}</small>
          </p>
          <div className="room-tools">
            <div className="hero-langs room-langs" role="group" aria-label="Language">
              {[["hi", "Hinglish"], ["en", "English"]].map(([id, label]) => (
                <button key={id} type="button" aria-pressed={lang === id}
                  disabled={active && busy !== null && busy !== "speaking"}
                  onClick={() => {
                    if (id === lang) return;
                    setLang(id);
                    // Mid-call: Saathi switches and asks the same question again in that language.
                    if (active) send(id === "en" ? "English, please" : "Hinglish mein bolo", `lang:${id}`);
                  }}>
                  {label}
                </button>
              ))}
            </div>
            <button type="button" className={`tool${voiceOn ? " on" : ""}`} aria-pressed={voiceOn}
              onClick={() => { setVoiceOn(!voiceOn); if (voiceOn) stopSpeaking(); }}>
              {voiceOn ? "Speaker on" : "Speaker off"}
            </button>
            {call ? (
              <button type="button" className="btn end-call" onClick={endCall}>
                {active ? "End call" : "Back"}
              </button>
            ) : (
              <button className="btn primary" onClick={() => begin({ hands: true })}
                disabled={(busy !== null && busy !== "speaking") || (studyMode && !studyInfo)}>
                Start call
              </button>
            )}
          </div>
        </div>

        <div className={`room-stage orb-${orbState}`}>
          <button type="button" className="orb" onClick={toggleMic}
            disabled={!active || (!live && busy !== null && busy !== "listening" && busy !== "speaking")}
            aria-label={live ? "Saathi is listening. Tap to mute" : busy === "listening" ? "Stop recording and send"
              : "Tap to speak"}
            aria-pressed={live || busy === "listening"}>
            <span className="orb-ring" aria-hidden="true" />
            <svg viewBox="0 0 24 24" width="40" height="40" aria-hidden="true"><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z" /></svg>
          </button>
          <p className="orb-word" aria-live="polite">{phase ? phase.word : active ? (live ? "Listening" : "Tap to speak")
            : call ? "Call ended" : ""}</p>
          <p className="orb-text">{phase ? phase.text : active && !live ? "Tap the circle, speak, then tap again" : ""}</p>
          {lastSaathi ? <p className="caption">{lastSaathi}</p> : null}
        </div>

        {active && call && !call.consent && !consentAsked ? (
          <div className="consent-card" role="group" aria-label="Saving your answers">
            <p>Save your answers for 30 days, so a loan advisor can follow up if you need one? Either way, the call
              carries on.</p>
            <div>
              <button type="button" className="btn primary"
                onClick={() => { setConsentAsked(true); send(lang === "en" ? "Yes, save my answers" : "Haan, save karo", "consent:yes"); }}>
                Yes, save
              </button>
              <button type="button" className="btn"
                onClick={() => { setConsentAsked(true); send(lang === "en" ? "No, don't save" : "Nahi, save mat karo", "consent:no"); }}>
                No thanks
              </button>
            </div>
          </div>
        ) : null}

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
                key={`${b.action}:${b.label}`}
                type="button"
                className={`btn${["confirm", "yes"].includes(b.action) ? " primary" : ""}`}
                onClick={() => (b.action === "new_call" ? begin({ hands: !!mic.current }) : send(b.text, b.action))}
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

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send(text);
          }}
        >
          <input
            ref={inputRef}
            aria-label="Your answer"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={active ? "…or type your answer" : open ? "…or type, e.g. ‘what if 2 lakh’"
              : call ? "Call ended. Tap Back to start again" : "Start a call first"}
            disabled={!open || busy === "listening"}
            maxLength={1000}
          />
          <button type="submit" className="btn" disabled={!open || !text.trim() || (busy && busy !== "speaking")}>
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
          {call ? (
            <OfferCheck apiKey={apiKey} lang={call.language} fromCall={call.offer_result}
              onUnauthorized={onUnauthorized} />
          ) : null}
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
