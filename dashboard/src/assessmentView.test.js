import assert from "node:assert/strict";
import { test } from "node:test";
import {
  barSummary,
  completenessGroups,
  incomeBar,
  pictureRows,
  safetyRows,
  SOURCE_TAGS,
  STATUS_TONE,
} from "./assessmentView.js";

test("income bar splits income into existing EMI, new EMI and the rest", () => {
  const bar = incomeBar({ existing_emi: 8, new_emi: 40, limit: 50 });
  assert.equal(bar.existing, 8);
  assert.equal(bar.new, 40);
  assert.equal(bar.remaining, 52);
  assert.equal(bar.over, false);
  assert.equal(bar.overLimit, false);
  assert.match(barSummary(bar), /48% of your monthly income; the safe limit is 50%/);
});

test("income bar flags EMIs above the 50% limit", () => {
  const bar = incomeBar({ existing_emi: 8, new_emi: 59.9, limit: 50 });
  assert.equal(bar.overLimit, true);
  assert.equal(bar.over, false);
});

test("EMIs above the whole income are scaled to fit and marked", () => {
  const bar = incomeBar({ existing_emi: 90, new_emi: 60 });
  assert.equal(bar.over, true);
  assert.equal(Math.round(bar.existing + bar.new), 100);
  assert.equal(bar.remaining, 0);
  assert.match(barSummary(bar), /more than your whole income \(150%/);
});

test("missing shares never produce NaN widths", () => {
  const bar = incomeBar(undefined);
  assert.deepEqual([bar.existing, bar.new, bar.remaining, bar.limit], [0, 0, 100, 50]);
});

test("picture rows keep provenance and skip absent values", () => {
  const rows = pictureRows({
    monthly_income: { value: 40000, text: "₹40,000", source: "you", source_text: "Provided by you" },
    existing_debt: null,
    remaining_income: { value: -500, text: "-₹500", source: "rules", source_text: "Calculated" },
  });
  assert.deepEqual(rows.map((r) => r.key), ["monthly_income", "remaining_income"]);
  assert.equal(rows[0].source_text, "Provided by you");
  assert.equal(rows[1].negative, true);
  assert.deepEqual(pictureRows(null), []);
});

test("completeness separates known facts from gaps", () => {
  const g = completenessGroups([
    { label: "Monthly income", state: "known" },
    { label: "Tenure", state: "missing", note: "Not given" },
    { label: "Credit history unavailable", state: "unavailable" },
  ]);
  assert.equal(g.known.length, 1);
  assert.deepEqual(g.gaps.map((i) => i.label), ["Tenure", "Credit history unavailable"]);
});

test("a failed safety check pauses the assessment", () => {
  const ok = safetyRows({ checks: [{ id: "required", state: "pass" }, { id: "debt", state: "flag" }] });
  assert.equal(ok.paused, false);
  assert.equal(ok.checks[1].mark.label, "Flagged");
  const held = safetyRows({ checks: [{ id: "consistency", state: "fail" }] });
  assert.equal(held.paused, true);
  assert.equal(held.checks[0].mark.icon, "✕");
  assert.equal(safetyRows(undefined).checks.length, 0);
});

test("every server source and status has a display mapping", () => {
  for (const s of ["you", "rules", "model", "calc", "estimate", "default"]) assert.ok(SOURCE_TAGS[s]);
  for (const s of ["within", "pressure", "not_now", "paused"]) assert.ok(STATUS_TONE[s]);
});

test("borrowing picture reads income, obligations, loan, then the totals", () => {
  const n = (value, source) => ({ value, text: String(value), source, source_text: source });
  const rows = pictureRows({
    remaining_income: n(17000, "math"),
    total_obligations: n(23000, "math"),
    new_emi: n(15000, "calc"),
    requested_loan: n(300000, "you"),
    existing_emi: n(8000, "you"),
    monthly_income: n(40000, "you"),
  });
  assert.deepEqual(rows.map((r) => r.key),
    ["monthly_income", "existing_emi", "requested_loan", "new_emi", "total_obligations", "remaining_income"]);
  assert.deepEqual(rows.filter((r) => r.total).map((r) => r.key), ["total_obligations", "remaining_income"]);
});
