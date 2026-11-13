// Run with: node --test src/offerView.test.js
import test from "node:test";
import assert from "node:assert/strict";
import { offerRows, offerTitle, rupees } from "./offerView.js";

const RESULT = {
  offer: { loan_amount: 300000, tenure_months: 24, emi: 18000, processing_fee: 2000 },
  effective_rate: 0.3812, total_paid: 432000, total_cost: 134000, verdict: "check_carefully",
  affordability: { known: true, share: 0.65, limit: 0.5, fits: false, max_emi: 13000 },
  flags: [{ code: "rate_far_above", text: "..." }],
};

test("rows show the offer, the real rate, totals and the income check", () => {
  const rows = offerRows(RESULT);
  assert.deepEqual(rows.map((r) => r.label), ["The offer", "Real yearly rate (fees included)", "You pay in all",
    "Interest and charges", "All your EMIs vs income"]);
  assert.equal(rows[1].text, "about 38%");
  assert.match(rows[0].text, /fee ₹2,000/);
  assert.match(rows[4].text, /65% of income \(limit 50%\); an EMI up to ₹13,000 fits/);
});

test("numbers that don't add up show no rate or totals, and unknown income says so", () => {
  const rows = offerRows({ ...RESULT, effective_rate: null, affordability: { known: false } });
  assert.deepEqual(rows.map((r) => r.label), ["The offer", "Fits your income?"]);
  assert.equal(offerTitle("numbers_dont_add_up"), "These numbers don't add up");
  assert.equal(offerTitle("looks_reasonable"), "Nothing unusual in these numbers");
});

test("indian rupee grouping", () => {
  assert.equal(rupees(432000), "₹4,32,000");
  assert.deepEqual(offerRows(null), []);
});
