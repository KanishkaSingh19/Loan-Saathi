// Pure helpers for the applicant-facing assessment (components/Assessment.jsx).
// The server sends every number with its `source`; these only shape it for display.
// Tested with `npm test` (node --test, no browser needed).

// Short tag shown next to a number; the full wording comes from the server's `source_text`.
export const SOURCE_TAGS = {
  you: "You",
  rules: "Rules",
  model: "Risk model",
  calc: "Calculated",
  estimate: "Estimated",
  default: "Default",
};

export const STATUS_TONE = { within: "good", pressure: "warn", not_now: "bad", paused: "info" };

export const CHECK_MARKS = {
  pass: { icon: "✓", label: "Passed", tone: "good" },
  flag: { icon: "!", label: "Flagged", tone: "warn" },
  fail: { icon: "✕", label: "Needs attention", tone: "bad" },
  na: { icon: "–", label: "Not applicable", tone: "muted" },
};

export const BAND_TONE = { low: "good", medium: "warn", high: "bad" };

const clamp = (x) => Math.max(0, Math.min(100, Number(x) || 0));

/**
 * Widths for the income bar: existing EMIs, the new EMI, and what is left, as % of income.
 * When EMIs exceed income the two EMI parts are scaled to fill the bar and `over` is true.
 * @param {{existing_emi?: number, new_emi?: number, limit?: number}} shares
 */
export function incomeBar(shares) {
  const existing = Math.max(0, Number(shares?.existing_emi) || 0);
  const fresh = Math.max(0, Number(shares?.new_emi) || 0);
  const used = existing + fresh;
  const scale = used > 100 ? 100 / used : 1;
  const e = existing * scale;
  const n = fresh * scale;
  return {
    existing: e,
    new: n,
    remaining: Math.max(0, 100 - e - n),
    used,
    over: used > 100,
    limit: clamp(shares?.limit ?? 50),
    overLimit: used > (shares?.limit ?? 50),
  };
}

/** One sentence that answers "can I handle this repayment?" for screen readers and the bar caption. */
export function barSummary(bar) {
  const used = Math.round(bar.used);
  if (bar.over) return `Your EMIs would be more than your whole income (${used}% of it).`;
  return `All EMIs together would use ${used}% of your monthly income; the safe limit is ${Math.round(bar.limit)}%.`;
}

const PICTURE_ROWS = [
  ["monthly_income", "Monthly income"],
  ["existing_emi", "Existing EMI"],
  ["existing_debt", "Existing obligations (still owed)"],
  ["new_emi", "New EMI (requested loan)"],
  ["remaining_income", "Remaining income after EMIs"],
  ["requested_loan", "Requested loan"],
  ["tenure", "Tenure"],
];

/** Rows for "Your financial picture", skipping anything the server didn't send. */
export function pictureRows(picture) {
  if (!picture) return [];
  return PICTURE_ROWS.filter(([key]) => picture[key]).map(([key, label]) => ({
    key,
    label,
    ...picture[key],
    negative: key === "remaining_income" && picture[key].value < 0,
  }));
}

/** Completeness split into what Loan-Saathi knows and what it doesn't. */
export function completenessGroups(items) {
  const list = items || [];
  return {
    known: list.filter((i) => i.state === "known"),
    gaps: list.filter((i) => i.state !== "known"),
  };
}

/** Safety checks with display marks, plus whether any check paused the assessment. */
export function safetyRows(safety) {
  const checks = (safety?.checks || []).map((c) => ({ ...c, mark: CHECK_MARKS[c.state] || CHECK_MARKS.na }));
  return { checks, paused: checks.some((c) => c.state === "fail") };
}
