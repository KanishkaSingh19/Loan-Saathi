import {
  BAND_TONE,
  completenessGroups,
  pictureRows,
  safetyRows,
  SOURCE_TAGS,
  STATUS_TONE,
} from "../assessmentView";
import { useState } from "react";
import AdvisorPanel from "./AdvisorPanel";
import IncomeBar from "./IncomeBar";
import ListenButton from "./ListenButton";
import Simulator from "./Simulator";

// The applicant-facing result of a pre-screen. Everything comes from result.assessment
// (src/assessment.py): numbers carry their own source, so nothing here invents a label.

function Source({ source, text }) {
  if (!source) return null;
  return (
    <span className={`source source-${source}`}>
      <i aria-hidden="true" />
      {text}
    </span>
  );
}

function Figure({ label, figure, tone = "", wide = false }) {
  if (!figure) return null;
  return (
    <div className={`figure${wide ? " wide" : ""}`}>
      <span className="figure-label">{label}</span>
      <strong className={tone ? `tone-${tone}` : ""}>{figure.text}</strong>
      {figure.help ? <span className="figure-help">{figure.help}</span> : null}
      <Source source={figure.source} text={figure.source_text} />
    </div>
  );
}

// The estimated pre-screening result, in the server's own careful words (assessment.outcome).
function Outcome({ outcome, fallback, tone }) {
  const t = outcome?.tone || tone;
  return (
    <div className={`outcome ${t}`} role="status">
      <strong>{outcome?.title || fallback}</strong>
      {outcome?.detail ? <span>{outcome.detail}</span> : null}
    </div>
  );
}

function Why({ why }) {
  if (!why) return null;
  const none = !why.helping?.length && !why.risk?.length;
  return (
    <section className="as-section">
      <h3>Why did I get this result?</h3>
      {none ? <p className="muted">No strong factors either way.</p> : null}
      <div className="why-grid">
        {why.helping?.length ? (
          <div>
            <h4>Working in your favour</h4>
            <ul className="why-list good">
              {why.helping.map((f, i) => (
                <li key={i}>
                  <span className="mark" aria-hidden="true">✓</span>
                  <span>
                    {f.text} <Source source={f.source} text={SOURCE_TAGS[f.source]} />
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {why.risk?.length ? (
          <div>
            <h4>Holding it back</h4>
            <ul className="why-list warn">
              {why.risk.map((f, i) => (
                <li key={i}>
                  <span className="mark" aria-hidden="true">⚠</span>
                  <span>
                    {f.text} <Source source={f.source} text={SOURCE_TAGS[f.source]} />
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
      <p className="muted small">{why.note}</p>
    </section>
  );
}

function Completeness({ items }) {
  const { known, gaps } = completenessGroups(items);
  if (!known.length && !gaps.length) return null;
  return (
    <section className="as-section">
      <h3>Assessment completeness</h3>
      <ul className="checklist">
        {known.map((i) => (
          <li key={i.label} className="good">
            <span className="mark" aria-hidden="true">✓</span>
            <span>{i.label}</span>
          </li>
        ))}
        {gaps.map((i) => (
          <li key={i.label} className="warn">
            <span className="mark" aria-hidden="true">⚠</span>
            <span>
              {i.label}
              {i.note ? <small className="muted"> · {i.note}</small> : null}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Safety({ safety }) {
  const { checks } = safetyRows(safety);
  if (!checks.length) return null;
  return (
    <section className="as-section">
      <h3>Safety check</h3>
      <ul className="checklist">
        {checks.map((c) => (
          <li key={c.id} className={c.mark.tone}>
            <span className="mark" aria-hidden="true">{c.mark.icon}</span>
            <span>
              {c.label}
              <span className="sr-only"> ({c.mark.label})</span>
              <small className="muted"> · {c.detail}</small>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function Assessment({
  assessment,
  apiKey = "",
  prescreenId = "",
  liveSimulation = null,
  onUnauthorized = undefined,
  lang = "en",
  sessionId = "",
  advisorCase = "",
}) {
  const [currentSim, setCurrentSim] = useState(null); // { loan, tenure } while a simulation is shown
  if (!assessment) return null;
  const a = assessment;
  const tone = STATUS_TONE[a.status] || "info";
  const spokenText = a.spoken?.[lang] || a.spoken?.en;

  return (
    <article className="assessment" aria-labelledby="assessment-title">
      <header className="as-head">
        <div>
          <h2 id="assessment-title">Your Loan-Saathi assessment</h2>
          <p className="muted small">An estimate from what you shared. Not a lender&rsquo;s decision.</p>
        </div>
        <ListenButton apiKey={apiKey} text={spokenText} onUnauthorized={onUnauthorized} />
      </header>

      {a.status === "paused" ? (
        <>
          <Outcome outcome={a.outcome} fallback={a.status_text} tone={tone} />
          <section className="as-section">
            <h3>What&rsquo;s needed</h3>
            <ul className="checklist">
              {(a.needed || []).map((n) => (
                <li key={n} className="bad">
                  <span className="mark" aria-hidden="true">✕</span>
                  <span>{n}</span>
                </li>
              ))}
            </ul>
          </section>
          <Safety safety={a.safety} />
        </>
      ) : (
        <>
          <ul className="source-key" aria-label="Where each number comes from">
            <li className="source source-you"><i aria-hidden="true" />You told us</li>
            <li className="source source-math"><i aria-hidden="true" />Calculated by Loan-Saathi</li>
            <li className="source source-rules"><i aria-hidden="true" />Based on affordability rules</li>
            <li className="source source-model"><i aria-hidden="true" />Estimated by model</li>
          </ul>

          <section className="as-section borrowing" aria-labelledby="picture-title">
            <h3 id="picture-title">Your borrowing picture</h3>
            <dl className="ledger">
              {pictureRows(a.picture).map((r) => (
                <div key={r.key} className={r.total ? "total" : ""}>
                  <dt>{r.label}</dt>
                  <dd>
                    <strong className={r.negative ? "tone-bad" : ""}>{r.text}</strong>
                    <Source source={r.source} text={r.source_text} />
                  </dd>
                </div>
              ))}
            </dl>
            {a.picture?.burden ? (
              <div className={`burden burden-${a.picture.burden.level}`}>
                <p className="burden-line">
                  <strong>{a.picture.burden.text}</strong>
                  <span>{a.picture.burden.detail}</span>
                </p>
                <IncomeBar shares={a.picture?.shares} />
                <Source source={a.picture.burden.source} text={a.picture.burden.source_text} />
              </div>
            ) : (
              <IncomeBar shares={a.picture?.shares} />
            )}
          </section>

          <section className="as-section" aria-labelledby="result-title">
            <h3 id="result-title">Estimated pre-screening result</h3>
            <Outcome outcome={a.outcome} fallback={a.status_text} tone={tone} />
            <div className="figures three">
              <Figure label="Room for a new EMI" figure={a.affordability} />
              {a.suggested_range ? <Figure label="Suggested loan range" figure={a.suggested_range} /> : null}
              <Figure label="Estimated repayment risk" figure={a.risk} tone={BAND_TONE[a.risk?.band]} />
            </div>
          </section>

          <Why why={a.why} />
          {a.path_to_yes?.length ? (
            <section className="as-section" aria-labelledby="path-title">
              <h3 id="path-title">What would change this</h3>
              <ul className="why-list good">
                {a.path_to_yes.map((p, i) => (
                  <li key={i}>
                    <span className="mark" aria-hidden="true">→</span>
                    <span>
                      {lang === "hi" && p.text_hi ? p.text_hi : p.text}{" "}
                      <Source source={p.source} text={SOURCE_TAGS[p.source]} />
                    </span>
                  </li>
                ))}
              </ul>
              <p className="muted small">Worked out from the affordability and debt rules, at the same assumed
                18% yearly interest. Still a pre-screening, not an offer.</p>
            </section>
          ) : null}
          {prescreenId ? (
            <Simulator
              apiKey={apiKey}
              prescreenId={prescreenId}
              liveSimulation={liveSimulation}
              onUnauthorized={onUnauthorized}
              onSimulationChange={setCurrentSim}
            />
          ) : null}
          <details className="as-details">
            <summary>What this estimate did and didn&rsquo;t check</summary>
            <div className="as-two">
              <Completeness items={a.completeness} />
              <Safety safety={a.safety} />
            </div>
          </details>
        </>
      )}

      {prescreenId || sessionId ? (
        <AdvisorPanel
          apiKey={apiKey}
          prescreenId={prescreenId}
          sessionId={sessionId}
          simulation={currentSim}
          existingCase={advisorCase}
          onUnauthorized={onUnauthorized}
        />
      ) : null}
    </article>
  );
}
