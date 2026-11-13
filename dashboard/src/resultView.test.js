// Run with: node --test src/resultView.test.js
import test from "node:test";
import assert from "node:assert/strict";
import { firstSentence, resultHero } from "./resultView.js";

const assessment = (over = {}) => ({
  status: "within",
  outcome: { tone: "good", title: "Further review recommended",
    detail: "The loan you asked for fits within the affordability rules, and the model's estimated risk is low. "
      + "A lender would still check your documents before any decision." },
  affordability: { value: 13000, text: "₹13,000/month" },
  ...over,
});

test("big status, one sentence and the affordable EMI", () => {
  const h = resultHero({ assessment: assessment() });
  assert.equal(h.title, "Further review recommended");
  assert.equal(h.tone, "good");
  assert.equal(h.sentence, "The loan you asked for fits within the affordability rules, and the model's estimated risk is low.");
  assert.equal(h.emiLabel, "EMI you can afford for a new loan");
  assert.equal(h.emiText, "₹13,000/month");
});

test("no room for an EMI is said plainly, not as ₹0", () => {
  const h = resultHero({ assessment: assessment({ outcome: { tone: "bad", title: "Not right now", detail: "x." },
    affordability: { value: 0, text: "₹0/month" } }) });
  assert.equal(h.emiText, "No room for a new EMI right now");
});

test("a paused assessment shows what's needed, no EMI", () => {
  const h = resultHero({ assessment: { status: "paused", needed: ["Your EMI can't be more than your income."],
    outcome: { tone: "info", title: "More information needed", detail: "Saathi needs a few details." } } });
  assert.equal(h.sentence, "Your EMI can't be more than your income.");
  assert.equal(h.emiText, "");
});

test("decimals and amounts don't end a sentence; nothing in, nothing out", () => {
  assert.equal(firstSentence("EMI of ₹4.5 lakh is too high. Try less."), "EMI of ₹4.5 lakh is too high.");
  assert.equal(firstSentence(""), "");
  assert.equal(resultHero(null), null);
  assert.equal(resultHero({ decision: "x" }).title, "Result ready");
});
