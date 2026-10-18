import DecisionBadge from "./DecisionBadge";
import RiskMeter from "./RiskMeter";
import { SAID_LABELS, friendly, isBlank } from "../constants";

function Reasons({ title, items, tone }) {
  if (!items?.length) return null;
  return (
    <div className="reasons">
      <h4>{title}</h4>
      <ul className={tone}>
        {items.map((i) => (
          <li key={i.feature}>
            <span>{friendly(`${i.label}: ${i.value}`)}</span>
            <small>typical {friendly(i.typical)}</small>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function PrescreenDetail({ prescreen }) {
  const { id, ts, said, applicant, result } = prescreen;
  const details = result.details || {};
  const saidRows = Object.entries(SAID_LABELS).filter(([k]) => !isBlank(said[k]));
  const when = new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "medium" });

  return (
    <article className="detail">
      <header className="detail-head">
        <DecisionBadge decision={result.decision} />
        <span className="muted mono">
          {when} · #{id}
        </span>
      </header>

      {result.offer_amount ? (
        <div className="offer">
          <div>
            <small>Offer</small>
            <strong>{result.offer_amount_text}</strong>
          </div>
          <div>
            <small>Tenure</small>
            <strong>{result.tenure_months} months</strong>
          </div>
          <div>
            <small>EMI</small>
            <strong>{result.offer_emi_text}/mo</strong>
          </div>
        </div>
      ) : null}

      {result.risk_percent ? (
        <RiskMeter percent={parseFloat(result.risk_percent)} band={result.risk_band} />
      ) : null}

      <blockquote className="hint">
        <small>Agent was told to say</small>
        {result.reply_hint}
      </blockquote>

      <div className="detail-grid">
        <section>
          <h3>What the caller said</h3>
          {saidRows.length ? (
            <dl className="said">
              {saidRows.map(([k, label]) => (
                <div key={k}>
                  <dt>{label}</dt>
                  <dd>{String(said[k])}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="muted">Nothing captured.</p>
          )}
        </section>
        <section>
          <h3>Why</h3>
          <Reasons title="Raises risk" items={details.raises_risk} tone="up" />
          <Reasons title="Lowers risk" items={details.lowers_risk} tone="down" />
          {result.missing_fields ? (
            <div className="reasons">
              <h4>Missing</h4>
              <p>{result.missing_fields}</p>
            </div>
          ) : null}
          {!details.raises_risk?.length && !details.lowers_risk?.length && !result.missing_fields ? (
            <p className="muted">No strong factors either way.</p>
          ) : null}
        </section>
      </div>

      {details.all_factors ? (
        <details>
          <summary>All model factors, including ones the agent doesn't speak</summary>
          <table className="factors">
            <thead>
              <tr>
                <th>Factor</th>
                <th>This caller</th>
                <th>Typical</th>
                <th>Effect</th>
              </tr>
            </thead>
            <tbody>
              {details.all_factors.map((f) => (
                <tr key={f.feature}>
                  <td>{f.label}</td>
                  <td>{friendly(f.value)}</td>
                  <td>{friendly(f.typical)}</td>
                  <td className={f.impact > 0 ? "pos" : f.impact < 0 ? "neg" : ""}>
                    {(f.impact >= 0 ? "+" : "") + (f.impact * 100).toFixed(1)} pts
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}

      <details>
        <summary>How the model understood the answers</summary>
        <pre>{JSON.stringify(applicant, null, 2)}</pre>
      </details>
    </article>
  );
}
