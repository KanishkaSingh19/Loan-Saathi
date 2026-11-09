import { useEffect, useRef, useState } from "react";
import { ApiError, simulate, Unauthorized } from "../api";
import { BAND_TONE } from "../assessmentView";
import { change, cleanLoan, cleanTenure, isOriginal, loanSlider, tenureText } from "../simulatorView";
import IncomeBar from "./IncomeBar";

// "What if you changed the loan?" Every figure comes from /v1/simulate, which runs the same
// safety check, risk model and affordability rule as the assessment. The saved request never changes:
// "Your request" and "Current simulation" stay side by side.

const ROWS = [
  ["loan_amount", "Loan amount"],
  ["tenure", "Tenure"],
  ["emi", "Monthly EMI"],
  ["total_repayment", "Total repayment (estimated)"],
  ["headroom", "Room left in your estimated affordability"],
  ["risk", "Estimated risk"],
];

function cell(sim, key) {
  if (!sim || sim.status === "paused") return "–";
  if (key === "risk") return sim.risk?.text ?? "–";
  if (key === "tenure") return tenureText(sim.tenure?.value);
  return sim[key]?.text?.replace("/month", "") ?? "–";
}

function Delta({ cmp, metric }) {
  const d = cmp?.[metric];
  if (!d || d.direction === "same") return <span className="muted">no change</span>;
  const c = change(metric, d.direction);
  return (
    <span className={`delta ${c.tone}`}>
      <b aria-hidden="true">{c.arrow}</b> {d.text}
    </span>
  );
}

function ScenarioCard({ s, active, onTry }) {
  if (s.unavailable) {
    return (
      <div className="scenario unavailable">
        <h4>{s.title}</h4>
        <p className="muted small">{s.blurb}</p>
      </div>
    );
  }
  const paused = s.status === "paused";
  return (
    <div className={`scenario${active ? " active" : ""} ${s.kind}`}>
      <h4>{s.title}</h4>
      <p className="muted small">{s.blurb}</p>
      <dl>
        <div><dt>Loan</dt><dd>{s.loan_amount.text}</dd></div>
        <div><dt>Tenure</dt><dd>{s.tenure.value} months</dd></div>
        {!paused && <div><dt>EMI</dt><dd>{s.emi.text.replace("/month", "")}/mo</dd></div>}
        {!paused && (
          <div>
            <dt>Affordability</dt>
            <dd className={s.fits ? "tone-good" : "tone-warn"}>{s.fits ? "Within estimate" : "Above estimate"}</dd>
          </div>
        )}
        {!paused && (
          <div>
            <dt>Estimated risk</dt>
            <dd className={`tone-${BAND_TONE[s.risk.band]}`}>{s.risk.text}</dd>
          </div>
        )}
      </dl>
      <button type="button" className="btn" onClick={() => onTry(s)} aria-pressed={active}>
        {active ? "Showing this option" : s.kind === "request" ? "Back to your request" : `Try ${s.title.toLowerCase()} option`}
      </button>
    </div>
  );
}

export default function Simulator({ apiKey, prescreenId, liveSimulation = null, onUnauthorized = undefined }) {
  const [base, setBase] = useState(null); // { original, scenarios }
  const [loan, setLoan] = useState(null);
  const [tenure, setTenure] = useState(null);
  const [typed, setTyped] = useState("");
  const [result, setResult] = useState(null); // { simulation, compare }
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef(null);
  const latest = useRef(0);

  const fail = (err) => {
    if (err instanceof Unauthorized) return onUnauthorized?.();
    setError(err instanceof ApiError ? err.message : "The simulator couldn't reach the server. Please try again.");
  };

  // Original request + the three scenarios, once per pre-screen.
  useEffect(() => {
    let alive = true;
    setBase(null);
    setResult(null);
    setError("");
    simulate(apiKey, prescreenId, { scenarios: true })
      .then((r) => {
        if (!alive) return;
        setBase(r);
        setLoan(r.original.loan_amount.value);
        setTenure(r.original.tenure.value);
        setTyped(String(r.original.loan_amount.value));
      })
      .catch((err) => alive && fail(err));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey, prescreenId]);

  // A what-if asked by voice ("what if I take 2 lakh?") becomes the active simulation.
  useEffect(() => {
    const sim = liveSimulation?.simulation;
    if (!sim || sim.status === "paused") return;
    setLoan(sim.loan_amount.value);
    setTenure(sim.tenure.value);
    setTyped(String(sim.loan_amount.value));
    setResult({ simulation: sim, compare: liveSimulation.compare });
  }, [liveSimulation]);

  // Recalculate on the server whenever the amount or tenure changes (debounced).
  useEffect(() => {
    if (!base || loan == null || tenure == null) return;
    if (isOriginal(loan, tenure, base.original)) {
      setResult(null);
      return;
    }
    if (result?.simulation && isOriginal(loan, tenure, result.simulation)) return;
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      const ticket = ++latest.current;
      setBusy(true);
      setError("");
      try {
        const r = await simulate(apiKey, prescreenId, { loanAmount: loan, tenureMonths: tenure });
        if (ticket === latest.current) setResult({ simulation: r.simulation, compare: r.compare });
      } catch (err) {
        if (ticket === latest.current) fail(err);
      } finally {
        if (ticket === latest.current) setBusy(false);
      }
    }, 250);
    return () => clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loan, tenure, base]);

  if (error && !base) {
    return (
      <section className="as-section simulator">
        <h3>What if you changed the loan?</h3>
        <p className="call-error" role="alert">{error}</p>
      </section>
    );
  }
  if (!base) {
    return (
      <section className="as-section simulator">
        <h3>What if you changed the loan?</h3>
        <p className="muted">Preparing the simulator…</p>
      </section>
    );
  }

  const original = base.original;
  const sim = result?.simulation;
  const showing = sim || original;
  const slider = loanSlider(original.loan_amount.value);
  const tryOption = (s) => {
    setLoan(s.loan_amount.value);
    setTenure(s.tenure.value);
    setTyped(String(s.loan_amount.value));
  };
  const reset = () => tryOption(original);
  const activeKind = sim ? (base.scenarios || []).find((s) => !s.unavailable && isOriginal(loan, tenure, s))?.kind
    : "request";

  return (
    <section className="as-section simulator" aria-labelledby="sim-title">
      <div className="sim-head">
        <h3 id="sim-title">What if you changed the loan?</h3>
        <span className="sim-label">Simulation — not a lender offer.</span>
      </div>

      <div className="scenarios">
        {(base.scenarios || []).map((s) => (
          <ScenarioCard key={s.kind} s={s} active={activeKind === s.kind} onTry={tryOption} />
        ))}
      </div>

      <div className="sim-controls">
        <label>
          <span>Loan amount</span>
          <div className="sim-input">
            <input
              type="range"
              min={slider.min}
              max={slider.max}
              step={slider.step}
              value={Math.min(loan, slider.max)}
              onChange={(e) => {
                setLoan(Number(e.target.value));
                setTyped(e.target.value);
              }}
              aria-label="Loan amount slider"
            />
            <input
              type="text"
              inputMode="numeric"
              className="sim-number"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onBlur={() => {
                const v = cleanLoan(typed);
                if (v == null) setTyped(String(loan));
                else {
                  setLoan(v);
                  setTyped(String(v));
                }
              }}
              onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
              aria-label="Loan amount in rupees"
            />
          </div>
        </label>
        <label>
          <span>Tenure: {tenureText(tenure)}</span>
          <input
            type="range"
            min={3}
            max={120}
            step={3}
            value={tenure}
            onChange={(e) => setTenure(cleanTenure(e.target.value))}
            aria-label="Tenure in months"
          />
        </label>
      </div>

      <div className="sim-result" aria-live="polite" aria-busy={busy}>
        {error && <p className="call-error" role="alert">{error}</p>}
        {showing.status === "paused" ? (
          <p className="as-status info">Can&rsquo;t simulate this yet: {(showing.needed || []).join(" ")}</p>
        ) : (
          <>
            <p className={`as-status ${showing.fits && !showing.debt_over_limit ? "good" : "warn"}`}>
              {sim ? "This simulation: " : "Your request: "}
              {showing.fits
                ? `EMI of ${showing.emi.text} is within your estimated affordability of ${showing.affordability.text}.`
                : `EMI of ${showing.emi.text} is above your estimated affordability of ${showing.affordability.text}.`}
              {showing.debt_over_limit ? " Existing loans are still more than 2 years of income." : ""}
            </p>
            <table className="sim-table">
              <thead>
                <tr>
                  <th scope="col"><span className="sr-only">Figure</span></th>
                  <th scope="col">Your request</th>
                  <th scope="col">Current simulation</th>
                  <th scope="col">Change</th>
                </tr>
              </thead>
              <tbody>
                {ROWS.map(([key, label]) => (
                  <tr key={key}>
                    <th scope="row">{label}</th>
                    <td>{cell(original, key)}</td>
                    <td>{sim ? cell(sim, key) : <span className="muted">same as request</span>}</td>
                    <td>{sim ? <Delta cmp={result.compare} metric={key} /> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {showing.shares ? <IncomeBar shares={showing.shares} /> : null}
            <p className="muted small">
              EMIs and totals use an assumed 18% yearly interest; a lender&rsquo;s real rate may differ.
              Estimated affordability keeps all your EMIs within half of your income.
            </p>
          </>
        )}
        {sim ? (
          <button type="button" className="btn" onClick={reset}>
            Back to my request
          </button>
        ) : null}
      </div>
    </section>
  );
}
