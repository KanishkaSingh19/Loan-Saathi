// Display rows for "Check an offer you got" (server: src/offer.py). Pure functions, so they're unit-tested.

const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

/** @param {number} n */
export const rupees = (n) => INR.format(Math.round(n));

/** @param {string} verdict */
export function offerTitle(verdict) {
  if (verdict === "numbers_dont_add_up") return "These numbers don't add up";
  if (verdict === "check_carefully") return "Check this offer carefully";
  return "Nothing unusual in these numbers";
}

/**
 * Rows in reading order; nothing the server didn't send.
 * @param {any} r the /v1/offer-check result
 * @returns {{label: string, text: string}[]}
 */
export function offerRows(r) {
  if (!r || !r.offer) return [];
  const rows = [];
  const o = r.offer;
  rows.push({ label: "The offer", text: `${rupees(o.loan_amount)} over ${o.tenure_months} months, EMI ${rupees(o.emi)}`
    + (o.processing_fee ? `, fee ${rupees(o.processing_fee)}` : "") });
  if (r.effective_rate != null) {
    rows.push({ label: "Real yearly rate (fees included)", text: `about ${Math.round(r.effective_rate * 100)}%` });
    rows.push({ label: "You pay in all", text: rupees(r.total_paid) });
    rows.push({ label: "Interest and charges", text: rupees(r.total_cost) });
  }
  const a = r.affordability || {};
  if (a.known) {
    rows.push({ label: "All your EMIs vs income",
      text: `${Math.round(a.share * 100)}% of income (limit ${Math.round(a.limit * 100)}%)`
        + (a.fits ? "" : `; an EMI up to ${rupees(a.max_emi)} fits`) });
  } else {
    rows.push({ label: "Fits your income?", text: "Tell Saathi your income to see" });
  }
  return rows;
}
