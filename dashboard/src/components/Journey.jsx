import { currentStage, stageStatus } from "../stages";

// LISTEN -> EXTRACT -> VALIDATE -> CONFIRM -> ASSESS -> EXPLAIN -> SIMULATE -> HANDOFF, with a caption
// for the current stage.
export default function Journey({ stage = undefined, busy = null }) {
  const current = currentStage(stage, busy);
  const steps = stageStatus(current);
  const now = steps.find((s) => s.status === "now");
  return (
    <div className="journey-wrap">
      <ol className="journey" aria-label="Loan-Saathi pipeline">
        {steps.map((s, i) => (
          <li key={s.id} className={s.status === "next" ? "" : s.status} aria-current={s.status === "now" ? "step" : undefined}>
            {i > 0 && <span className="arrow" aria-hidden="true">→</span>}
            <span className="step">{s.label}</span>
          </li>
        ))}
      </ol>
      {now ? (
        <p className="journey-caption" aria-live="polite">
          <b>{now.label}:</b> {now.caption}
        </p>
      ) : null}
    </div>
  );
}
