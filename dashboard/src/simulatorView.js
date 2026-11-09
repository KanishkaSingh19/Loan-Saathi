// Pure helpers for the what-if simulator (components/Simulator.jsx). Numbers come from /v1/simulate;
// these only pick slider ranges and describe changes. Tested with `npm test`.

export const MIN_LOAN = 1000;
export const MAX_LOAN = 100000000; // ₹10 crore, the server's limit
export const MIN_TENURE = 3;
export const MAX_TENURE = 120;

/** Slider range for the loan amount: from ₹1,000 to well above the request, in friendly steps. */
export function loanSlider(requested) {
  const req = Math.max(MIN_LOAN, Number(requested) || MIN_LOAN);
  const max = Math.min(MAX_LOAN, Math.max(req * 2, 50000));
  const step = max <= 200000 ? 1000 : max <= 2000000 ? 5000 : 50000;
  return { min: MIN_LOAN, max: Math.ceil(max / step) * step, step };
}

/** "36 months (3 years)" */
export function tenureText(months) {
  const m = Number(months);
  if (!Number.isFinite(m)) return "";
  const years = m / 12;
  if (m < 12) return `${m} months`;
  const y = Number.isInteger(years) ? `${years} year${years === 1 ? "" : "s"}` : `${years.toFixed(1)} years`;
  return `${m} months (${y})`;
}

/** Clamp what the user typed to the server's limits; null when it isn't a number. */
export function cleanLoan(value) {
  const n = Math.floor(Number(String(value).replace(/[₹,\s]/g, "")));
  if (!Number.isFinite(n) || n <= 0) return null;
  return Math.min(MAX_LOAN, Math.max(MIN_LOAN, n));
}

export function cleanTenure(value) {
  const n = Math.round(Number(value));
  if (!Number.isFinite(n)) return null;
  return Math.min(MAX_TENURE, Math.max(MIN_TENURE, n));
}

// For each compared figure, which direction is better for the borrower.
const BETTER = { emi: "down", total_repayment: "down", headroom: "up", risk: "down", loan_amount: null, tenure: null };
const ARROWS = { up: "↑", down: "↓", same: "→" };

/** Arrow + tone for a change: good (green), bad (warn) or neutral. */
export function change(metric, direction) {
  const better = BETTER[metric];
  const tone = !direction || direction === "same" || !better ? "neutral" : direction === better ? "good" : "bad";
  return { arrow: ARROWS[direction] || "", tone };
}

/** True when a simulation is just the original request again. */
export function isOriginal(loan, tenure, original) {
  return original && loan === original.loan_amount?.value && tenure === original.tenure?.value;
}
