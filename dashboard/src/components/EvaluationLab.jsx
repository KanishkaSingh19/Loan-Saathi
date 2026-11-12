import { useCallback, useEffect, useState } from "react";
import { ApiError, fetchEvaluation, Unauthorized } from "../api";

// Evaluation Lab: evidence for an evaluator. Everything comes from GET /v1/evaluation, which reads
// models/*.json, checks each safeguard exists in the code and joins the last recorded test run.
// Nothing is computed here except formatting; anything unmeasured is shown as such.

const fixed = (x, d = 4) => (x == null ? null : Number(x).toFixed(d));
const pct = (x, d = 1) => (x == null ? null : `${(Number(x) * 100).toFixed(d)}%`);
const when = (ts) => new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
const STATUS = {
  passed: { label: "Passed", tone: "good" },
  failed: { label: "Failed", tone: "bad" },
  not_found: { label: "Check not found in run", tone: "bad" },
  not_run: { label: "Not run", tone: "info" },
  not_measured: { label: "Not currently measured", tone: "muted" },
};

function Metric({ label, value, source = "", note = "", notMeasured }) {
  return (
    <div className="ev-metric">
      <dt>{label}</dt>
      <dd>
        {value == null ? <span className="muted">{notMeasured}</span> : <strong>{value}</strong>}
        {note ? <small>{note}</small> : null}
        {source ? <small className="ev-source">{source}</small> : null}
      </dd>
    </div>
  );
}

function Bands({ bands }) {
  if (!bands) return null;
  return (
    <table className="ev-table">
      <thead>
        <tr><th scope="col">Risk band</th><th scope="col">Share of validation set</th><th scope="col">Actual default rate</th></tr>
      </thead>
      <tbody>
        {["low", "medium", "high"].map((b) => (
          <tr key={b}><th scope="row">{b[0].toUpperCase() + b.slice(1)}</th><td>{pct(bands[b].share)}</td><td>{pct(bands[b].default_rate)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

function Model({ m, nm }) {
  if (m.error) return <p className="call-error">{m.error}</p>;
  const b = m.behaviour;
  return (
    <section className="ev-section" aria-labelledby="ev-model">
      <h3 id="ev-model">Model</h3>
      <p className="ev-callout">{m.purpose}</p>
      <dl className="ev-metrics">
        <Metric label="Voice model AUC (validation)" value={fixed(m.voice.auc.value)} source={m.voice.auc.source}
          note={m.voice.what} notMeasured={nm} />
        <Metric label="Voice model Brier score" value={fixed(m.voice.brier.value)} source={m.voice.brier.source}
          note={m.voice.brier.note} notMeasured={nm} />
        <Metric label="Voice model calibration error (ECE)" value={fixed(m.voice.ece.value, 5)} source={m.voice.ece.source}
          notMeasured={nm} />
        <Metric label="Before the repayment-history questions" value={fixed(m.voice.previous_auc.value, 3)}
          source={m.voice.previous_auc.source} note="Voice model AUC" notMeasured={nm} />
        <Metric label="Full benchmark AUC" value={fixed(m.full.auc.value, 3)} source={m.full.auc.source}
          note={m.full.what} notMeasured={nm} />
        <Metric label="Full benchmark Brier score" value={null} note={m.full.brier.note} notMeasured={nm} />
        <Metric label="Accuracy on Indian borrowers" value={null} note={m.indian_accuracy.note} notMeasured={nm} />
      </dl>
      <p className="muted small">
        Data: {m.dataset.name}, {m.dataset.train_rows.toLocaleString("en-IN")} training and{" "}
        {m.dataset.validation_rows.toLocaleString("en-IN")} validation applications, {pct(m.dataset.default_rate)} defaulted.
        AUC: how well the model ranks a defaulter above a non-defaulter (0.5 is chance). Brier: mean squared error of
        the predicted probability (lower is better).
      </p>

      <div className="ev-two">
        <div>
          <h4>Risk bands (voice model)</h4>
          <Bands bands={m.voice.bands} />
        </div>
        <div>
          <h4>Calibration by decile (voice model)</h4>
          <table className="ev-table">
            <thead><tr><th scope="col">Decile</th><th scope="col">Predicted</th><th scope="col">Actual</th></tr></thead>
            <tbody>
              {m.voice.deciles.map((d, i) => (
                <tr key={i}><th scope="row">{i + 1}</th><td>{pct(d.predicted)}</td><td>{pct(d.actual)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {b ? (
        <>
          <h4>Behaviour checks on synthetic Indian profiles</h4>
          <dl className="ev-metrics">
            <Metric label="Violations: risk fell as debt rose" value={`${b.debt_model_violations} of ${b.debt_steps_checked.toLocaleString("en-IN")} checks`}
              source={b.source} note={`Decision improves with more debt: ${b.debt_end_to_end_violations} cases`} notMeasured={nm} />
            <Metric label="Violations: a 'yes' to a repayment problem lowered risk" value={`${b.history_violations} of ${b.history_checks} checks`}
              source={b.source} notMeasured={nm} />
            <Metric label="Band changes when income moves 5%" value={pct(b.band_flip_rate_income_5pct)} source={b.source}
              note={`Gate: at most ${pct(b.gates.band_flip_rate, 0)}`} notMeasured={nm} />
          </dl>
          <p className="muted small">
            {b.synthetic_profiles} made-up applicant profiles. These test behaviour, not accuracy. {b.not_measured}
          </p>
        </>
      ) : null}

      {m.comparison ? (
        <details className="ev-details">
          <summary>Model comparison: LightGBM, XGBoost and CatBoost</summary>
          <p className="muted small">{m.comparison.note} Source: {m.comparison.source}.</p>
          {Object.entries(m.comparison.tables).map(([name, rows]) => (
            <div key={name}>
              <h4>{name}</h4>
              <table className="ev-table">
                <thead>
                  <tr><th scope="col">Model</th><th scope="col">AUC</th><th scope="col">Brier</th><th scope="col">Log loss</th><th scope="col">Train time</th><th scope="col">Score one caller</th></tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.model}>
                      <th scope="row">{r.model}</th><td>{fixed(r.auc)}</td><td>{fixed(r.brier)}</td><td>{fixed(r.log_loss)}</td>
                      <td>{r.train_seconds != null ? `${r.train_seconds} s` : "–"}</td>
                      <td>{r.predict_one_ms != null ? `${r.predict_one_ms} ms` : "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </details>
      ) : null}
    </section>
  );
}

const secs = (ms) => (ms == null ? null : `${(ms / 1000).toFixed(2)} s`);

function Measurements({ m, nm }) {
  const lat = m.latency;
  const b = lat.benchmark;
  const live = lat.browser;
  const ex = m.extraction;
  return (
    <section className="ev-section" aria-labelledby="ev-measure">
      <h3 id="ev-measure">Measurements</h3>

      <h4>Voice latency</h4>
      <table className="ev-table">
        <thead>
          <tr>
            <th scope="col">Stage</th>
            <th scope="col">Benchmark median</th><th scope="col">Benchmark 90th pct</th>
            <th scope="col">Real browser calls median</th><th scope="col">Real browser calls 90th pct</th>
          </tr>
        </thead>
        <tbody>
          {lat.stages.map((st) => (
            <tr key={st.key}>
              <th scope="row">{st.label}</th>
              <td>{secs(b?.summary[st.key]?.median) ?? nm}</td>
              <td>{secs(b?.summary[st.key]?.p90) ?? nm}</td>
              <td>{secs(live?.[st.key]?.median) ?? nm}</td>
              <td>{secs(live?.[st.key]?.p90) ?? nm}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        {b ? <>Benchmark: {b.turns} caller turns, {when(b.ts)}. {b.method} Replies by provider:{" "}
          {b.llm_turns ? Object.entries(b.llm_turns).map(([p, n]) => `${p} ${n}`).join(", ")
            : `gemini ${b.gemini_turns}`}. Source: <code>{b.source}</code>. </> : <>Benchmark: {nm} </>}
        {m.latency?.stall ? <>If the language model hangs, a turn waits{" "}
          {Object.entries(m.latency.stall).map(([label, r]) =>
            `${Math.round(Math.max(...r.turn_ms) / 100) / 10} s (${label})`).join(" vs ")}{" "}
          before the parser answers. Source: <code>{m.latency.stall_source}</code>. </> : null}
        {live ? `Real browser calls: ${live.turns} live turns recorded by the call screen.`
          : "Real browser calls: no live voice turns recorded yet."}
      </p>

      <h4>Extraction accuracy</h4>
      <dl className="ev-metrics">
        {[["parser", "Saathi's own parser (no language model)"], ["gemini", "With Gemini"]].map(([k, label]) => (
          <div className="ev-metric" key={k}>
            <dt>{label}</dt>
            <dd>
              {ex[k] ? <strong>{ex[k].correct} of {ex[k].cases}</strong> : <span className="muted">{nm}</span>}
              {ex[k] && k === "gemini" ? <small>Gemini answered {ex[k].gemini_turns} of the {ex[k].cases} turns;
                the rest fell back to the parser.</small> : null}
              {ex[k]?.wrong?.length ? <small>Wrong: {ex[k].wrong.map((w) => `"${w.said}" → ${JSON.stringify(w.got)}`).join("; ")}</small> : null}
              {ex[k] ? <small className="ev-source">{ex[k].source}</small> : null}
            </dd>
          </div>
        ))}
        <div className="ev-metric">
          <dt>Real first-time borrowers</dt>
          <dd><span className="muted">{nm}</span><small>{m.real_users.note}</small></dd>
        </div>
      </dl>
      {ex.parser ? (
        <details className="ev-details">
          <summary>By kind of answer</summary>
          <table className="ev-table">
            <thead><tr><th scope="col">Kind</th><th scope="col">Parser</th>{ex.gemini ? <th scope="col">Gemini</th> : null}</tr></thead>
            <tbody>
              {Object.entries(ex.parser.by_kind).map(([kind, v]) => (
                <tr key={kind}>
                  <th scope="row">{kind}</th>
                  <td>{v.correct} / {v.cases}</td>
                  {ex.gemini ? <td>{ex.gemini.by_kind[kind] ? `${ex.gemini.by_kind[kind].correct} / ${ex.gemini.by_kind[kind].cases}` : "–"}</td> : null}
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}
      <p className="muted small">{ex.about}</p>
    </section>
  );
}

function Safeguards({ items }) {
  return (
    <section className="ev-section" aria-labelledby="ev-safety">
      <h3 id="ev-safety">Safety architecture</h3>
      <p className="muted small">Each safeguard below was checked to exist in the code when this page loaded.</p>
      <ul className="ev-guards">
        {items.map((g) => (
          <li key={g.name}>
            <b>{g.name}</b>
            <span>{g.what}</span>
            <small className={g.verified ? "ev-source" : "tone-bad"}>
              {g.verified ? `Found in ${g.where}` : `Not found in ${g.where}`}
            </small>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Coverage({ groups, run }) {
  return (
    <section className="ev-section" aria-labelledby="ev-tests">
      <h3 id="ev-tests">Test coverage</h3>
      {run ? (
        <p className="ev-run">
          Last recorded run: {when(run.ts)} at commit <code>{run.commit || "unknown"}</code> (Python {run.python}).{" "}
          <b>{run.suites_passed} of {run.suites} suites passed</b>, {run.checks_passed} named checks and unit tests.
          {run.skipped?.length ? ` Skipped: ${run.skipped.map((s) => `${s.suite} (${s.why})`).join(", ")}.` : ""}
        </p>
      ) : (
        <p className="ev-run">No recorded test run yet. Results below show as &ldquo;Not run&rdquo;.</p>
      )}
      <p className="muted small">
        A category counts as passed only when every listed check printed its result in that run. Re-run with{" "}
        <code>python -I -X utf8 src/evaluation.py run</code>.
      </p>
      <div className="ev-groups">
        {groups.map((g) => (
          <div key={g.title} className="ev-group">
            <h4>{g.title}</h4>
            <ul>
              {g.items.map((i) => {
                const st = STATUS[i.status];
                return (
                  <li key={i.label}>
                    <details>
                      <summary>
                        <span>{i.label}</span>
                        <span className={`ev-status ${st.tone}`}>{st.label}</span>
                      </summary>
                      {i.checks.length ? (
                        <ul className="ev-checks">
                          {i.checks.map((c, k) => (
                            <li key={k}>
                              <code>{c.suite}</code> {c.check}{" "}
                              <span className={`ev-status ${STATUS[c.state].tone}`}>{STATUS[c.state].label}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="muted small">No test measures this.</p>
                      )}
                    </details>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

function Failures({ cases }) {
  return (
    <section className="ev-section" aria-labelledby="ev-fail">
      <h3 id="ev-fail">Failure cases</h3>
      <p className="muted small">Problems the system had, how they were found and what changed.</p>
      <div className="ev-cases">
        {cases.map((f, i) => (
          <article key={f.title} className={`ev-case${i === 0 ? " lead" : ""}`}>
            <h4>{f.title}</h4>
            <dl>
              <div><dt>Found</dt><dd>{f.found}</dd></div>
              <div><dt>What happened</dt><dd>{f.what}</dd></div>
              {f.measured_before ? <div><dt>Measured before</dt><dd>{f.measured_before}</dd></div> : null}
              <div><dt>Fix</dt><dd>{f.fix}</dd></div>
              {f.measured_after ? <div><dt>Measured after</dt><dd>{f.measured_after}</dd></div> : null}
              {f.still ? <div><dt>Still true</dt><dd>{f.still}</dd></div> : null}
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}

export default function EvaluationLab({ apiKey, onUnauthorized }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    try {
      setData(await fetchEvaluation(apiKey));
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized();
      setError(err instanceof ApiError ? err.message : "Couldn't load the evaluation evidence.");
    }
  }, [apiKey, onUnauthorized]);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <main className="evaluation">
      <header className="ev-head">
        <div>
          <h2>Evaluation Lab</h2>
          <p className="muted">
            Evidence for how Loan-Saathi behaves. Each number names the file it came from, and anything not
            measured says so.
          </p>
        </div>
        <button type="button" className="btn" onClick={load}>Refresh</button>
      </header>
      {error && <p className="call-error" role="alert">{error}</p>}
      {!data && !error ? <p className="loading-line"><span className="dots" aria-hidden="true"><i /><i /><i /></span>Loading evidence…</p> : null}
      {data ? (
        <>
          <Model m={data.model} nm={data.not_measured_text} />
          {data.measurements ? <Measurements m={data.measurements} nm={data.not_measured_text} /> : null}
          <Safeguards items={data.safeguards} />
          <Coverage groups={data.coverage} run={data.run} />
          <Failures cases={data.failure_cases} />
          <section className="ev-section" aria-labelledby="ev-limits">
            <h3 id="ev-limits">Limitations</h3>
            <ul className="ev-limits">
              {data.limitations.map((l) => (
                <li key={l.title}><b>{l.title}</b><span>{l.detail}</span></li>
              ))}
            </ul>
          </section>
        </>
      ) : null}
    </main>
  );
}
