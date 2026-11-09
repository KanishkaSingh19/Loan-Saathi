import { useEffect, useRef, useState } from "react";
import { ApiError, simulate, Unauthorized } from "../api";
import { BAND_TONE } from "../assessmentView";
import { change, cleanLoan, cleanTenure, isOriginal, loanSlider, tenureText } from "../simulatorView";
import IncomeBar from "./IncomeBar";

// "Explore another option". Every figure comes from /v1/simulate, which runs the same
// safety check, risk model and affordability rule as the assessment. The saved request never changes:
// "Your request" and "Current simulation" stay side by side.

const LABEL = "Simulation only — not a lender offer or approval.";

function Delta({ cmp, metric }) {
  const d = cmp?.[metric];
  if (!d || d.direction === "same") return null;
  const c = change(metric, d.direction);
  return (
    <span className={`delta ${c.tone}`}>
      <b aria-hidden="true">{c.arrow}</b> {d.text}
    </span>
  );
}

function burdenText(s) {
  if (s.emi_share == null) return "–";
  return `${Math.round(s.emi_share)}% of income`;
}

// One scenario: the saved request or the alternative. Same rows, so they read side by side.
// `risk` is what to show for the estimated risk (the alternative may keep the original's).
function ScenarioPanel({ title, note, s, cmp = null, risk, kind }) {
  const paused = s.status === "paused";
  return (
    <section className={`what-if-card ${kind}`} aria-label={title}>
      <header>
        <h4>{title}</h4>
        <p>{note}</p>
      </header>
      {paused ? (
        <p className="as-status info">Can&rsquo;t simulate this yet: {(s.needed || []).join(" ")}</p>
      ) : (
        <dl>
          <div>
            <dt>Loan amount</dt>
            <dd><strong>{s.loan_amount.text}</strong><Delta cmp={cmp} metric="loan_amount" /></dd>
          </div>
          <div>
            <dt>Repayment period</dt>
            <dd><strong>{tenureText(s.tenure.value)}</strong><Delta cmp={cmp} metric="tenure" /></dd>
          </div>
          <div>
            <dt>Estimated EMI</dt>
            <dd><strong>{s.emi.text}</strong><Delta cmp={cmp} metric="emi" /></dd>
          </div>
          <div>
            <dt>Monthly burden</dt>
            <dd>
              <strong className={s.burden?.level === "over" ? "tone-bad" : s.burden?.level === "stretched" ? "tone-warn" : ""}>
                {burdenText(s)}
              </strong>
              <small>all EMIs: {s.total_obligations?.text ?? "–"}</small>
            </dd>
          </div>
          <div>
            <dt>Assessment</dt>
            <dd><strong>{s.outcome?.title ?? s.status_text}</strong></dd>
          </div>
          <div>
            <dt>Estimated risk</dt>
            <dd>
              <strong className={risk.tone ? `tone-${risk.tone}` : ""}>{risk.text}</strong>
              {risk.note ? <small>{risk.note}</small> : null}
            </dd>
          </div>
        </dl>
      )}
    </section>
  );
}

export default function Simulator({
  apiKey,
  prescreenId,
  liveSimulation = null,
  onUnauthorized = undefined,
  onSimulationChange = undefined,
}) {
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

  // Tell the assessment which simulation is on screen (for the advisor summary).
  useEffect(() => {
    const s = result?.simulation;
    onSimulationChange?.(s && s.status !== "paused" ? { loan: s.loan_amount.value, tenure: s.tenure.value } : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

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
        <h3>Explore another option</h3>
        <p className="call-error" role="alert">{error}</p>
      </section>
    );
  }
  if (!base) {
    return (
      <section className="as-section simulator">
        <h3>Explore another option</h3>
        <p className="loading-line"><span className="dots" aria-hidden="true"><i /><i /><i /></span>
          Preparing the simulator…</p>
      </section>
    );
  }

  const original = base.original;
  const sim = result?.simulation;
  const slider = loanSlider(original.loan_amount.value);
  const tryOption = (o) => {
    setLoan(o.loan_amount.value);
    setTenure(o.tenure.value);
    setTyped(String(o.loan_amount.value));
  };
  const reset = () => tryOption(original);
  const safer = (base.scenarios || []).find((o) => o.kind === "safer");
  const onSafer = sim && safer && !safer.unavailable && isOriginal(loan, tenure, safer);

  const bandRisk = (x) => ({ text: x.risk?.text ?? "–", tone: BAND_TONE[x.risk?.band] });
  const cmpRisk = result?.compare?.risk;
  // The model re-scores the alternative, except where it can't be trusted: then the original stands.
  const altRisk = !sim || sim.status === "paused" ? null
    : cmpRisk && cmpRisk.recalculated === false
      ? { text: "Not recalculated", note: `Original estimate (${original.risk?.text}) still applies.` }
      : { ...bandRisk(sim), note: "Recalculated by the model for this amount" };

  return (
    <section className="as-section simulator" aria-labelledby="sim-title">
      <div className="sim-head">
        <div>
          <h3 id="sim-title">Explore another option</h3>
          <p className="muted small">
            Change the amount or the repayment period to see how your monthly budget would change. Your
            request stays saved as it is.
          </p>
        </div>
        <span className="sim-label">{LABEL}</span>
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
          <span>Repayment period: {tenureText(tenure)}</span>
          <input
            type="range"
            min={3}
            max={120}
            step={3}
            value={tenure}
            onChange={(e) => setTenure(cleanTenure(e.target.value))}
            aria-label="Repayment period in months"
          />
        </label>
      </div>

      <div className="sim-actions">
        {safer && !safer.unavailable ? (
          <button type="button" className="btn" onClick={() => tryOption(safer)} aria-pressed={!!onSafer}>
            Lower-burden option: {safer.loan_amount.text}
          </button>
        ) : null}
        {sim ? (
          <button type="button" className="btn primary" onClick={reset}>
            Back to my request
          </button>
        ) : null}
        {safer ? <span className="muted small">{safer.blurb}</span> : null}
      </div>

      <div className="what-if-pair" aria-live="polite" aria-busy={busy}>
        <ScenarioPanel title="Your request" note="Saved. Simulations never change it." s={original}
          risk={{ ...bandRisk(original), note: "Estimated by model" }} kind="request" />
        {sim ? (
          <ScenarioPanel title="Alternative" note={onSafer ? "Lower-burden option" : "Simulation"} s={sim}
            cmp={result.compare} risk={altRisk || { text: "–" }} kind="alternative" />
        ) : (
          <section className="what-if-card alternative empty" aria-label="Alternative">
            <header>
              <h4>Alternative</h4>
              <p>Move a slider, type an amount, or ask Saathi &ldquo;what if I borrow 2 lakh?&rdquo;</p>
            </header>
          </section>
        )}
      </div>

      {error && <p className="call-error" role="alert">{error}</p>}
      {sim && sim.status !== "paused" && sim.shares ? (
        <div className="burden">
          <p className="burden-line">
            <strong>Alternative: {sim.burden?.text}</strong>
            <span>{sim.burden?.detail}</span>
          </p>
          <IncomeBar shares={sim.shares} />
        </div>
      ) : null}
      <p className="muted small">
        EMIs: {original.emi.source_text.toLowerCase()}; a lender&rsquo;s real rate may differ. Monthly burden is
        all your EMIs as a share of income; the affordability rules allow up to 50%.
      </p>
    </section>
  );
}
