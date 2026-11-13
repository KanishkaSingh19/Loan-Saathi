import { useState } from "react";
import { ApiError, checkOffer, Unauthorized } from "../api";
import { offerRows, offerTitle } from "../offerView";

/**
 * "Got a loan offer? Check it": the borrower types the offer's numbers (or says them on the call) and sees
 * the real yearly rate, what it costs in all, and whether the EMI fits their income. An estimate from
 * Loan-Saathi's own calculation; it never names a lender. Collapsed until opened, so the page stays simple.
 * @param {{apiKey: string, lang?: string, fromCall?: any, onUnauthorized?: () => void}} props
 */
export default function OfferCheck({ apiKey, lang = "en", fromCall = null, onUnauthorized }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ loanAmount: "", tenureMonths: "", emi: "", processingFee: "" });
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const shown = result || fromCall;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      setResult(await checkOffer(apiKey, { ...form, language: lang }));
    } catch (err) {
      if (err instanceof Unauthorized) onUnauthorized?.();
      setError(err instanceof ApiError ? err.message : "Couldn't check the offer. Please try again.");
    } finally {
      setBusy(false);
    }
  };
  const field = (name, label, hint) => (
    <label className="offer-field">
      <span>{label}</span>
      <input value={form[name]} placeholder={hint} maxLength={40}
        onChange={(e) => setForm({ ...form, [name]: e.target.value })} />
    </label>
  );

  return (
    <section className="offer-check">
      {!open && !shown ? (
        <button type="button" className="btn" onClick={() => setOpen(true)}>Got a loan offer? Check it</button>
      ) : null}
      {open ? (
        <form onSubmit={submit} aria-label="Check an offer you got">
          <h3>Check an offer you got</h3>
          {field("loanAmount", "Loan amount", "e.g. 2 lakh")}
          {field("tenureMonths", "Time to repay", "e.g. 24 months or 2 saal")}
          {field("emi", "Monthly EMI", "e.g. 11,000")}
          {field("processingFee", "Processing fee (if any)", "e.g. 2,000")}
          <div className="offer-actions">
            <button type="submit" className="btn primary"
              disabled={busy || !form.loanAmount.trim() || !form.tenureMonths.trim() || !form.emi.trim()}>
              {busy ? "Checking…" : "Check"}
            </button>
            <button type="button" className="btn" onClick={() => setOpen(false)}>Close</button>
          </div>
          {error ? <p className="error small" role="alert">{error}</p> : null}
        </form>
      ) : null}
      {shown ? (
        <div className={`offer-result ${shown.verdict}`} aria-live="polite">
          <h4>{offerTitle(shown.verdict)}</h4>
          <dl className="said">
            {offerRows(shown).map((r) => (
              <div key={r.label}><dt>{r.label}</dt><dd>{r.text}</dd></div>
            ))}
          </dl>
          {shown.flags?.length ? (
            <ul className="offer-flags">{shown.flags.map((f) => <li key={f.code}>{f.text}</li>)}</ul>
          ) : null}
          <p className="muted small">{lang === "hi" ? shown.label_hi : shown.label}</p>
        </div>
      ) : null}
    </section>
  );
}
