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

function Why({ why }) {
  if (!why) return null;
  const none = !why.helping?.length && !why.risk?.length;
  return (
    <section className="as-section">
      <h3>Why this result?</h3>
      {none ? <p className="muted">No strong factors either way.</p> : null}
      <div className="why-grid">
        {why.helping?.length ? (
          <div>
            <h4>Helping factors</h4>
            <ul className="why-list good">
              {why.helping.map((f, i) => (
                <li key={i}>
                  <span className="mark" aria-hidden="true">✓</span>
                  <span>
                    {f.text} <small className="muted">{SOURCE_TAGS[f.source]}</small>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {why.risk?.length ? (
          <div>
            <h4>Risk factors</h4>
            <ul className="why-list warn">
              {why.risk.map((f, i) => (
                <li key={i}>
                  <span className="mark" aria-hidden="true">⚠</span>
                  <span>
                    {f.text} <small className="muted">{SOURCE_TAGS[f.source]}</small>
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
          <h2 id="assessment-title">Your Loan-Saathi Assessment</h2>
          <p className="muted small">A pre-screening estimate from what you shared. Not a lending decision.</p>
        </div>
        <span className={`badge ${tone}`}>{a.decision_label}</span>
      </header>

      <div className="as-lead">
        <p className={`as-status ${tone}`} role="status">
          {a.status_text}
        </p>
        <ListenButton apiKey={apiKey} text={spokenText} onUnauthorized={onUnauthorized} />
      </div>

      {a.status === "paused" ? (
        <>
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
            <li className="source source-you"><i aria-hidden="true" />What you said</li>
            <li className="source source-rules"><i aria-hidden="true" />Calculated by Loan-Saathi</li>
            <li className="source source-model"><i aria-hidden="true" />Estimated by the risk model</li>
          </ul>
          <div className="figures">
            <Figure label="Estimated risk" figure={a.risk} tone={BAND_TONE[a.risk?.band]} />
            <Figure label="Estimated affordability" figure={a.affordability} />
            <Figure label="Requested loan" figure={a.requested_loan} />
            <Figure label="Requested EMI" figure={a.requested_emi} />
            {a.suggested_range ? (
              <Figure label="Suggested range" figure={a.suggested_range} wide />
            ) : (
              <div className="figure wide">
                <span className="figure-label">Suggested range</span>
                <strong className="muted">Not available right now</strong>
              </div>
            )}
          </div>

          <section className="as-section">
            <h3>Your financial picture</h3>
            <IncomeBar shares={a.picture?.shares} />
            <dl className="picture">
              {pictureRows(a.picture).map((r) => (
                <div key={r.key}>
                  <dt>{r.label}</dt>
                  <dd>
                    <strong className={r.negative ? "tone-bad" : ""}>{r.text}</strong>
                    <Source source={r.source} text={r.source_text} />
                  </dd>
                </div>
              ))}
            </dl>
          </section>

          <Why why={a.why} />
          {apiKey && prescreenId ? (
            <Simulator
              apiKey={apiKey}
              prescreenId={prescreenId}
              liveSimulation={liveSimulation}
              onUnauthorized={onUnauthorized}
              onSimulationChange={setCurrentSim}
            />
          ) : null}
          <div className="as-two">
            <Completeness items={a.completeness} />
            <Safety safety={a.safety} />
          </div>
        </>
      )}

      {apiKey && (prescreenId || sessionId) ? (
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
