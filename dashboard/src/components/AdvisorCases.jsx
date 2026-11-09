import { useCallback, useEffect, useState } from "react";
import { ApiError, fetchAdvisorCases, Unauthorized, updateAdvisorCase } from "../api";

const STATUS = {
  open: { label: "Open", tone: "info" },
  in_review: { label: "In review", tone: "warn" },
  closed: { label: "Closed", tone: "good" },
};

function CaseActions({ c, apiKey, onChange, onUnauthorized }) {
  const [advisor, setAdvisor] = useState(c.advisor || "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (status) => {
    setBusy(true);
    setError("");
    try {
      onChange(await updateAdvisorCase(apiKey, c.id, { status, advisor, note }));
      setNote("");
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized();
      setError(err instanceof ApiError ? err.message : "Couldn't update the case. Please try again.");
    } finally {
      setBusy(false);
    }
  };
  const status = c.status || "open";
  return (
    <div className="case-actions">
      <input value={advisor} onChange={(e) => setAdvisor(e.target.value)} placeholder="Advisor name"
        aria-label="Advisor name" maxLength={80} />
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)"
        aria-label="Note" maxLength={1000} />
      {status === "open" && <button type="button" className="btn primary" disabled={busy} onClick={() => act("in_review")}>Pick up</button>}
      {status === "in_review" && <button type="button" className="btn primary" disabled={busy} onClick={() => act("closed")}>Close case</button>}
      {status === "closed" && <button type="button" className="btn" disabled={busy} onClick={() => act("open")}>Reopen</button>}
      {error && <p className="call-error" role="alert">{error}</p>}
    </div>
  );
}

// Advisor cases saved by "Talk to a Loan Advisor" (button or voice). For the advisor, not the applicant.

function Rows({ rows }) {
  return (
    <dl className="said">
      {rows.map((r) => (
        <div key={r.label}>
          <dt>{r.label}</dt>
          <dd>
            {r.text}
            {r.status ? <small className="muted"> · {r.status}</small> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// The money fields an advisor reads first; loan-history answers fold away below them.
const PROFILE_FIRST = new Set(["Income", "Existing EMI", "Existing debt", "Other loans running"]);
const FLAG_TONE = {
  excessive_debt: "bad", contradiction: "warn", missing_critical: "bad", affordability: "warn",
  unconfirmed: "warn", extraction: "info", skipped: "info", unverified: "info", assessment_unavailable: "warn",
};
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function Reasons({ title, items, tone }) {
  if (!items?.length) return null;
  return (
    <>
      <h5 className="ws-sub">{title}</h5>
      <ul className={`why-list ${tone}`}>
        {items.map((t, i) => (
          <li key={i}>
            <span className="mark" aria-hidden="true">{tone === "good" ? "✓" : "⚠"}</span>
            <span>{t}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

function CaseCard({ c: initial, apiKey, onUnauthorized }) {
  const [c, setC] = useState(initial);
  const when = new Date(c.ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  const st = STATUS[c.status || "open"];
  const notified = c.notification?.delivered
    ? "Advisor notified"
    : c.notification?.configured
      ? "Notification failed"
      : "Not notified (no advisor channel set up)";
  return (
    <article className="panel case">
      <header className="case-head">
        <div>
          <h3>Case #{c.id}</h3>
          <p className="muted small">
            {when} · {c.channel === "web-call" ? "Web call" : c.channel === "phone" ? "Phone call" : "Dashboard"}
            {" · "}{notified}{c.advisor ? ` · ${c.advisor}` : ""}
          </p>
        </div>
        <span className={`badge ${st.tone}`}>{st.label}</span>
      </header>
      <CaseActions c={c} apiKey={apiKey} onChange={setC} onUnauthorized={onUnauthorized} />
      <p className="case-headline">{c.headline}</p>
      <blockquote className="hint">
        <small>Applicant&rsquo;s question</small>
        {c.question}
      </blockquote>

      <div className="ws-grid">
        <section className="ws-block">
          <h4>Applicant</h4>
          <Rows rows={c.applicant} />
        </section>
        <section className="ws-block">
          <h4>Financial profile</h4>
          <Rows
            rows={[
              ...c.financial_profile.filter((r) => PROFILE_FIRST.has(r.label)),
              { label: "Requested loan", text: c.request.loan },
              { label: "Tenure", text: c.request.tenure },
            ]}
          />
          <details>
            <summary>Loan history answers</summary>
            <Rows rows={c.financial_profile.filter((r) => !PROFILE_FIRST.has(r.label))} />
          </details>
        </section>
        <section className="ws-block">
          <h4>Assessment</h4>
          {c.assessment ? (
            <>
              <Rows
                rows={[
                  { label: "Status", text: c.assessment.outcome || c.assessment.label },
                  { label: "Estimated risk band", text: c.assessment.risk },
                  { label: "Affordability", text: c.assessment.status_text },
                  { label: "Estimated EMI", text: c.assessment.requested_emi },
                  { label: "Room for a new EMI", text: c.assessment.affordable_emi },
                  ...(c.assessment.burden ? [{ label: "Monthly burden", text: c.assessment.burden }] : []),
                  { label: "Suggested range", text: c.assessment.suggested_range },
                ]}
              />
              <Reasons title="Working in their favour" items={c.assessment.why_helping} tone="good" />
              <Reasons title="Holding it back" items={c.assessment.why_risk} tone="warn" />
            </>
          ) : (
            <p className="muted small">Not assessed (the applicant hadn&rsquo;t confirmed a full profile).</p>
          )}
          {c.simulation ? (
            <>
              <h4>Simulation on screen at handoff</h4>
              <Rows
                rows={[
                  { label: "Loan / tenure", text: `${c.simulation.loan} over ${c.simulation.tenure}` },
                  { label: "EMI", text: c.simulation.emi },
                  { label: "Affordability", text: c.simulation.fits },
                ]}
              />
            </>
          ) : null}
        </section>
      </div>

      <div className="ws-grid two">
        <section className="ws-block">
          <h4>Corrections</h4>
          {!c.corrections ? (
            <p className="muted small">Not recorded for this case (no web-call record).</p>
          ) : c.corrections.length === 0 ? (
            <p className="muted small">No answer was changed during the call.</p>
          ) : (
            <ul className="ws-corrections">
              {c.corrections.map((x) => (
                <li key={x.field}>
                  <span className="ws-field">{x.label}</span>
                  <span className="ws-change">
                    <s>{x.before}</s> <span aria-hidden="true">→</span>
                    <span className="sr-only"> changed to </span> <b>{x.after}</b>
                  </span>
                  <small className="muted">
                    {x.how} · {x.status}{x.said ? <> · said: &ldquo;{x.said}&rdquo;</> : null}
                  </small>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="ws-block">
          <h4>Safety flags</h4>
          {(c.flags || []).length === 0 ? (
            <p className="muted small">No flags were raised.</p>
          ) : (
            <ul className="ws-flags">
              {c.flags.map((f, i) => (
                <li key={i} className={`flag-${FLAG_TONE[f.kind] || "info"}`}>
                  <b>{f.label}</b>
                  <span>{f.detail}</span>
                  <small className="muted">Source: {f.source}</small>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="ws-block">
        <h4>Timeline</h4>
        {c.timeline?.length ? (
          <ol className="ws-timeline">
            {c.timeline.map((e, i) => (
              <li key={i}>
                <time>{clock(e.ts)}</time>
                <b>{e.label}</b>
                {e.detail ? <span className="muted"> · {e.detail}</span> : null}
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted small">No event record for this case.</p>
        )}
      </section>

      <section className="ws-block">
        <h4>Conversation</h4>
        <ul className="case-list">
          {c.conversation.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
        {c.transcript?.length ? (
          <details>
            <summary>Full conversation ({c.transcript.length} turns, the caller&rsquo;s exact words)</summary>
            <ol className="ws-transcript">
              {c.transcript.map((t, i) => (
                <li key={i} className={t.who}>
                  <span className="who">{t.who === "caller" ? "Caller" : "Saathi"}</span>
                  <span>{t.text}</span>
                </li>
              ))}
            </ol>
          </details>
        ) : c.recent?.length ? (
          <details>
            <summary>Last few turns of the conversation</summary>
            <ul className="case-list">
              {c.recent.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </details>
        ) : null}
        <details>
          <summary>Assessment completeness</summary>
          <ul className="checklist">
            {c.completeness.map((i) => (
              <li key={i.label} className={i.state === "known" ? "good" : "warn"}>
                <span className="mark" aria-hidden="true">{i.state === "known" ? "✓" : "⚠"}</span>
                <span>{i.label}</span>
              </li>
            ))}
          </ul>
        </details>
      </section>

      {c.events?.length ? (
        <details>
          <summary>Case history</summary>
          <ul className="case-list">
            {c.events.map((e, i) => (
              <li key={i}>
                {new Date(e.ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })} ·{" "}
                {STATUS[e.status]?.label || e.status} by {e.by}
                {e.note ? `: ${e.note}` : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <p className="muted small">{c.prototype_note}</p>
    </article>
  );
}

export default function AdvisorCases({ apiKey, onUnauthorized }) {
  const [cases, setCases] = useState(null);
  const [error, setError] = useState("");
  const [show, setShow] = useState("active"); // active = open + in review

  const load = useCallback(async () => {
    setError("");
    try {
      setCases((await fetchAdvisorCases(apiKey)).cases);
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized();
      setError("Couldn't load advisor cases. Please try again.");
    }
  }, [apiKey, onUnauthorized]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <main className="cases">
      <div className="cases-head">
        <div>
          <h2>Advisor cases</h2>
          <p className="muted small">
            Saved when an applicant chooses &ldquo;Talk to a Loan Advisor&rdquo;. Prototype: nobody is notified
            automatically.
          </p>
        </div>
        <button type="button" className="btn" onClick={load}>
          Refresh
        </button>
      </div>
      {error && <p className="call-error" role="alert">{error}</p>}
      {cases === null && !error ? <p className="muted">Loading…</p> : null}
      {cases?.length === 0 ? <p className="muted">No advisor cases yet.</p> : null}
      {cases?.length ? (
        <div className="chips" role="tablist" aria-label="Filter cases">
          {[["active", "Open & in review"], ["closed", "Closed"], ["all", "All"]].map(([id, label]) => (
            <button key={id} role="tab" aria-selected={show === id} className={`chip${show === id ? " active" : ""}`}
              onClick={() => setShow(id)}>{label}</button>
          ))}
        </div>
      ) : null}
      {cases
        ?.filter((c) => show === "all" || (show === "closed" ? c.status === "closed" : c.status !== "closed"))
        .map((c) => (
          <CaseCard key={c.id} c={c} apiKey={apiKey} onUnauthorized={onUnauthorized} />
        ))}
    </main>
  );
}
