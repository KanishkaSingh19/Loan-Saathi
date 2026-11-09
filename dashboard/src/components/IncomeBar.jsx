import { barSummary, incomeBar } from "../assessmentView";

// Monthly income split into existing EMIs, the new EMI and what is left, with the 50% limit marked.
export default function IncomeBar({ shares }) {
  const bar = incomeBar(shares);
  const summary = barSummary(bar);
  return (
    <figure className="income-bar">
      <div className="bar" role="img" aria-label={summary}>
        <span className="seg existing" style={{ width: `${bar.existing}%` }} />
        <span className={`seg new${bar.overLimit ? " over" : ""}`} style={{ width: `${bar.new}%` }} />
        <span className="seg rest" style={{ width: `${bar.remaining}%` }} />
        <i className="limit" style={{ left: `${bar.limit}%` }} aria-hidden="true" />
      </div>
      <figcaption>
        <span className="key"><i className="existing" />Existing EMI</span>
        <span className="key"><i className={`new${bar.overLimit ? " over" : ""}`} />New EMI</span>
        <span className="key"><i className="rest" />Left for everything else</span>
        <span className="key"><i className="limit-key" />{Math.round(bar.limit)}% limit</span>
      </figcaption>
      <p className={`bar-summary${bar.overLimit ? " tone-warn" : ""}`}>{summary}</p>
    </figure>
  );
}
