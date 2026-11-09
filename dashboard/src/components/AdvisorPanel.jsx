import { useState } from "react";
import { ApiError, createAdvisorCase, Unauthorized } from "../api";

// "Talk to a Loan Advisor": saves a structured summary of this pre-screen (and the current
// simulation) as an advisor case. Prototype: it appears in the Advisor cases view; nobody is notified.
export default function AdvisorPanel({
  apiKey,
  prescreenId = "",
  sessionId = "",
  simulation = null,
  existingCase = "",
  onUnauthorized = undefined,
}) {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(null);
  const [error, setError] = useState("");

  const caseId = saved?.id || existingCase;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const c = await createAdvisorCase(apiKey, {
        prescreenId,
        sessionId,
        question,
        loanAmount: simulation?.loan,
        tenureMonths: simulation?.tenure,
      });
      setSaved(c);
      setOpen(false);
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized?.();
      setError(err instanceof ApiError ? err.message : "Couldn't save your summary. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="advisor" aria-labelledby="advisor-title">
      <div className="advisor-head">
        <div>
          <h3 id="advisor-title">Want to talk it through with a person?</h3>
          <p className="muted small">
            A loan advisor can look at your answers, this assessment and any option you tried.
          </p>
        </div>
        {!open && (
          <button type="button" className="btn primary" onClick={() => setOpen(true)}>
            Talk to a Loan Advisor
          </button>
        )}
      </div>

      {caseId && !open && (
        <p className="as-status info" role="status">
          Your summary is saved as advisor case <b>#{caseId}</b>
          {saved?.simulation ? ` (including your simulation: ${saved.simulation.loan} over ${saved.simulation.tenure})` : ""}.
          {" "}Prototype: a loan advisor sees it in the Loan-Saathi dashboard; no one is notified automatically.
        </p>
      )}

      {open && (
        <form className="advisor-form" onSubmit={submit}>
          <label htmlFor="advisor-question">Your question for the advisor (optional)</label>
          <textarea
            id="advisor-question"
            rows={2}
            maxLength={500}
            value={question}
            placeholder="For example: Can I get a longer tenure?"
            onChange={(e) => setQuestion(e.target.value)}
          />
          {simulation ? (
            <p className="muted small">
              Your current simulation will be included as well.
            </p>
          ) : null}
          {error && <p className="call-error" role="alert">{error}</p>}
          <div className="advisor-actions">
            <button type="submit" className="btn primary" disabled={busy}>
              {busy ? "Saving…" : "Save my summary for an advisor"}
            </button>
            <button type="button" className="btn" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
