// Bar spans 0–30% repayment risk. Zones match the model's band cut-offs (5% / 12%).
const MAX = 30;
const LOW = 5;
const MEDIUM = 12;

export default function RiskMeter({ percent, band }) {
  const pos = Math.min(percent / MAX, 1) * 100;
  return (
    <div className="meter">
      <div className="meter-head">
        <span>Repayment risk</span>
        <strong className={`band ${band}`}>
          {percent.toFixed(1)}% · {band}
        </strong>
      </div>
      <div
        className="meter-bar"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={MAX}
        aria-valuenow={percent}
        aria-label={`Repayment risk ${percent.toFixed(1)} percent, ${band}`}
        style={{ "--low": `${(LOW / MAX) * 100}%`, "--medium": `${(MEDIUM / MAX) * 100}%` }}
      >
        <i style={{ left: `${pos}%` }} />
      </div>
      <div className="meter-scale">
        <span>0%</span>
        <span style={{ left: `${(LOW / MAX) * 100}%` }}>5%</span>
        <span style={{ left: `${(MEDIUM / MAX) * 100}%` }}>12%</span>
        <span>30%+</span>
      </div>
    </div>
  );
}
