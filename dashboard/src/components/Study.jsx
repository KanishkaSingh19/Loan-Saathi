import { useEffect, useState } from "react";
import { ApiError, studyCsv, studyPersonas, studyReport, submitSurvey, Unauthorized } from "../api";

// User-study mode (open the dashboard with ?study=1). See STUDY.md for the protocol.

const CONSENT = [
  "This is a research test of Loan-Saathi, not a real loan application. No loan will be given or refused.",
  "You will speak from a persona card, so please don't share your own financial details.",
  "We record the conversation text, timings and your survey answers under a participant code, not your name.",
  "You can stop at any time without giving a reason.",
];

/** Before the call: consent, participant code, persona card. Calls onReady({participant, persona}). */
export function StudySetup({ apiKey, onReady, onUnauthorized, disabled = false }) {
  const [personas, setPersonas] = useState([]);
  const [persona, setPersona] = useState("");
  const [participant, setParticipant] = useState("");
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    studyPersonas(apiKey)
      .then((p) => {
        setPersonas(p);
        setPersona(p[0]?.id || "");
      })
      .catch((err) => (err instanceof Unauthorized ? onUnauthorized() : setError("Couldn't load persona cards.")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey]);

  const card = personas.find((p) => p.id === persona);
  const ok = consent && persona && /^[A-Za-z0-9_-]{1,20}$/.test(participant);
  useEffect(() => {
    onReady(ok ? { participant, persona, language: card?.language || "hi" } : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ok, participant, persona]);

  return (
    <section className="study-setup" aria-labelledby="study-title">
      <h3 id="study-title">User study</h3>
      <ul className="case-list">
        {CONSENT.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <label className="toggle">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} disabled={disabled} />
        The participant has read this (or had it read to them) and agrees to take part.
      </label>
      <div className="study-row">
        <label>
          <span>Participant code</span>
          <input value={participant} onChange={(e) => setParticipant(e.target.value.trim())} placeholder="e.g. T07"
            maxLength={20} disabled={disabled} />
        </label>
        <label>
          <span>Persona card</span>
          <select value={persona} onChange={(e) => setPersona(e.target.value)} disabled={disabled}>
            {personas.map((p) => (
              <option key={p.id} value={p.id}>{p.id} · {p.title}</option>
            ))}
          </select>
        </label>
      </div>
      {card && (
        <blockquote className="hint persona-card">
          <small>Read this card, then tell Saathi about &ldquo;yourself&rdquo; in your own words</small>
          {card.card}
        </blockquote>
      )}
      {error && <p className="call-error" role="alert">{error}</p>}
      {!ok && <p className="muted small">Tick consent and enter a participant code to start.</p>}
    </section>
  );
}

/** After the call: three questions and a comment. */
export function StudySurvey({ apiKey, sessionId, sttFailures = 0, onUnauthorized }) {
  const [ease, setEase] = useState(0);
  const [understood, setUnderstood] = useState("");
  const [trust, setTrust] = useState(0);
  const [comment, setComment] = useState("");
  const [state, setState] = useState("open"); // open | saving | done
  const [error, setError] = useState("");

  if (state === "done") return <p className="as-status good" role="status">Thank you! Your answers are saved.</p>;

  const scale = (value, set, name) => (
    <div className="scale" role="radiogroup" aria-label={name}>
      {[1, 2, 3, 4, 5].map((n) => (
        <label key={n} className={`chip${value === n ? " active" : ""}`}>
          <input type="radio" name={name} value={n} checked={value === n} onChange={() => set(n)} />
          {n}
        </label>
      ))}
    </div>
  );

  const submit = async (e) => {
    e.preventDefault();
    setState("saving");
    setError("");
    try {
      await submitSurvey(apiKey, { session_id: sessionId, ease, understood, trust, comment, stt_failures: sttFailures });
      setState("done");
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized();
      setError(err instanceof ApiError ? err.message : "Couldn't save the answers. Please try again.");
      setState("open");
    }
  };

  return (
    <form className="study-survey" onSubmit={submit}>
      <h3>Three quick questions</h3>
      <p>1. How easy was it to talk to Saathi? (1 = very hard, 5 = very easy)</p>
      {scale(ease, setEase, "Ease")}
      <p>2. Did you understand the result?</p>
      <div className="scale" role="radiogroup" aria-label="Understood">
        {[["yes", "Yes"], ["partly", "Partly"], ["no", "No"]].map(([v, l]) => (
          <label key={v} className={`chip${understood === v ? " active" : ""}`}>
            <input type="radio" name="understood" value={v} checked={understood === v} onChange={() => setUnderstood(v)} />
            {l}
          </label>
        ))}
      </div>
      <p>3. How much would you trust this result? (1 = not at all, 5 = completely)</p>
      {scale(trust, setTrust, "Trust")}
      <label className="study-comment">
        <span>Anything confusing or helpful? (optional)</span>
        <textarea rows={2} maxLength={1000} value={comment} onChange={(e) => setComment(e.target.value)} />
      </label>
      {error && <p className="call-error" role="alert">{error}</p>}
      <button type="submit" className="btn primary" disabled={!ease || !understood || !trust || state === "saving"}>
        {state === "saving" ? "Saving…" : "Save answers"}
      </button>
    </form>
  );
}

function Block({ title, b }) {
  if (!b) return null;
  const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
  const rows = [
    ["Calls", b.calls],
    ["Reached a result", b.reached_result],
    ["Median time to result", b.median_seconds_to_result == null ? "–" : `${b.median_seconds_to_result} s`],
    ["Median time to read-back", b.median_seconds_to_summary == null ? "–" : `${b.median_seconds_to_summary} s`],
    ["Median caller turns", b.median_caller_turns ?? "–"],
    ["Extraction accuracy, first pass", pct(b.first_pass_accuracy)],
    ["Accuracy after read-back and corrections", pct(b.final_accuracy)],
    ["Read-backs per call", b.readbacks_per_call ?? "–"],
    ["Corrections per call", b.corrections_per_call ?? "–"],
    ["Gemini used (vs scripted)", pct(b.gemini_share)],
    ["Asked for an advisor", b.handoffs],
    ["Surveys", b.surveys],
    ["Ease (1–5)", b.ease_1to5 ?? "–"],
    ["Trust (1–5)", b.trust_1to5 ?? "–"],
    ["Understood result (yes / partly / no)",
      `${b.understood_result?.yes ?? 0} / ${b.understood_result?.partly ?? 0} / ${b.understood_result?.no ?? 0}`],
    ["Speech-to-text failures reported", b.stt_failures_reported],
  ];
  return (
    <section className="panel case">
      <h3>{title}</h3>
      <dl className="said">
        {rows.map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
        ))}
      </dl>
      {Object.keys(b.field_first_pass_accuracy || {}).length ? (
        <>
          <h4>First-pass accuracy by field</h4>
          <dl className="said">
            {Object.entries(b.field_first_pass_accuracy).map(([f, v]) => (
              <div key={f}><dt>{f.replaceAll("_", " ")}</dt><dd>{pct(v)}</dd></div>
            ))}
          </dl>
        </>
      ) : null}
    </section>
  );
}

/** Researcher view: aggregates for real participants and synthetic dry runs, CSV export. */
export function StudyReport({ apiKey, onUnauthorized }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const load = () =>
    studyReport(apiKey)
      .then(setReport)
      .catch((err) => (err instanceof Unauthorized ? onUnauthorized() : setError("Couldn't load the study report.")));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey]);
  const download = async () => {
    const url = URL.createObjectURL(await studyCsv(apiKey));
    const a = document.createElement("a");
    a.href = url;
    a.download = "loan-saathi-study.csv";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <main className="cases">
      <div className="cases-head">
        <div>
          <h2>User study</h2>
          <p className="muted small">{report?.note}</p>
        </div>
        <div className="advisor-actions">
          <button type="button" className="btn" onClick={load}>Refresh</button>
          <button type="button" className="btn primary" onClick={download} disabled={!report}>Download CSV</button>
        </div>
      </div>
      {error && <p className="call-error" role="alert">{error}</p>}
      {report && (
        <div className="case-grid">
          <Block title="Participants" b={report.participants} />
          <Block title="Synthetic dry runs (not user results)" b={report.synthetic_dry_runs} />
        </div>
      )}
    </main>
  );
}
