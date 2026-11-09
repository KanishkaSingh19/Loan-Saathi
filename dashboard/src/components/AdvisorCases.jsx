import { useCallback, useEffect, useState } from "react";
import { fetchAdvisorCases, Unauthorized } from "../api";

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

function CaseCard({ c }) {
  const when = new Date(c.ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  return (
    <article className="panel case">
      <header className="case-head">
        <div>
          <h3>Case #{c.id}</h3>
          <p className="muted small">
            {when} · {c.channel === "web-call" ? "Web call" : c.channel === "phone" ? "Phone call" : "Dashboard"}
          </p>
        </div>
        <span className="badge info">Open</span>
      </header>
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
      <p className="muted small">{c.prototype_note}</p>
    </article>
  );
}

export default function AdvisorCases({ apiKey, onUnauthorized }) {
  const [cases, setCases] = useState(null);
  const [error, setError] = useState("");

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
      {cases?.map((c) => (
        <CaseCard key={c.id} c={c} />
      ))}
    </main>
  );
}
