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
      <div className="case-grid">
        <section>
          <h4>Financial profile</h4>
          <Rows rows={c.financial_profile} />
          <Rows rows={[{ label: "Requested loan", text: c.request.loan }, { label: "Tenure", text: c.request.tenure }]} />
        </section>
        <section>
          <h4>Applicant</h4>
          <Rows rows={c.applicant} />
        </section>
        <section>
          <h4>Assessment</h4>
          {c.assessment ? (
            <Rows
              rows={[
                { label: "Result", text: c.assessment.label },
                { label: "Estimated risk", text: c.assessment.risk },
                { label: "Estimated affordable EMI", text: c.assessment.affordable_emi },
                { label: "Requested EMI", text: c.assessment.requested_emi },
                { label: "Suggested range", text: c.assessment.suggested_range },
              ]}
            />
          ) : (
            <p className="muted small">Not assessed yet (the applicant hadn&rsquo;t confirmed a full profile).</p>
          )}
          {c.simulation ? (
            <>
              <h4>Current simulation</h4>
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
        <section>
          <h4>Conversation summary</h4>
          <ul className="case-list">
            {c.conversation.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
          <h4>Assessment completeness</h4>
          <ul className="checklist">
            {c.completeness.map((i) => (
              <li key={i.label} className={i.state === "known" ? "good" : "warn"}>
                <span className="mark" aria-hidden="true">{i.state === "known" ? "✓" : "⚠"}</span>
                <span>{i.label}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
      {c.recent?.length ? (
        <details>
          <summary>Last few turns of the conversation</summary>
          <ul className="case-list">
            {c.recent.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </details>
      ) : null}
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
