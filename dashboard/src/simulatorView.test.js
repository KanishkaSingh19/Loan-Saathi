import assert from "node:assert/strict";
import { test } from "node:test";
import { change, cleanLoan, cleanTenure, isOriginal, loanSlider, tenureText } from "./simulatorView.js";

test("loan slider spans well above the request in friendly steps", () => {
  assert.deepEqual(loanSlider(300000), { min: 1000, max: 600000, step: 5000 });
  assert.deepEqual(loanSlider(20000), { min: 1000, max: 50000, step: 1000 });
  assert.equal(loanSlider(9e7).max, 100000000); // never above the server's ₹10 crore limit
  assert.equal(loanSlider(undefined).min, 1000);
});

test("typed amounts and tenures are clamped to the server's limits", () => {
  assert.equal(cleanLoan("₹2,50,000"), 250000);
  assert.equal(cleanLoan("50"), 1000);
  assert.equal(cleanLoan("999999999999"), 100000000);
  assert.equal(cleanLoan("abc"), null);
  assert.equal(cleanLoan("-5"), null);
  assert.equal(cleanTenure(1), 3);
  assert.equal(cleanTenure(500), 120);
  assert.equal(cleanTenure("x"), null);
});

test("tenure reads in months and years", () => {
  assert.equal(tenureText(6), "6 months");
  assert.equal(tenureText(12), "12 months (1 year)");
  assert.equal(tenureText(36), "36 months (3 years)");
  assert.equal(tenureText(18), "18 months (1.5 years)");
});

test("changes are judged from the borrower's side", () => {
  assert.deepEqual(change("emi", "down"), { arrow: "↓", tone: "good" });
  assert.deepEqual(change("emi", "up"), { arrow: "↑", tone: "bad" });
  assert.deepEqual(change("headroom", "up"), { arrow: "↑", tone: "good" });
  assert.deepEqual(change("total_repayment", "up"), { arrow: "↑", tone: "bad" });
  assert.deepEqual(change("loan_amount", "down"), { arrow: "↓", tone: "neutral" });
  assert.equal(change("risk", "same").tone, "neutral");
});

test("the original request is recognised", () => {
  const original = { loan_amount: { value: 300000 }, tenure: { value: 36 } };
  assert.equal(isOriginal(300000, 36, original), true);
  assert.equal(isOriginal(300000, 48, original), false);
  assert.ok(!isOriginal(300000, 36, null));
});
