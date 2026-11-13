// The simple result view: a big status, one sentence, the EMI the caller can afford. Everything comes from
// the server's assessment (src/assessment.py), so the wording matches the full assessment below it.

/** The first sentence of a paragraph ("A. B." -> "A."). @param {string} text */
export function firstSentence(text) {
  if (!text) return "";
  const m = String(text).match(/^.*?[.!?](?=\s|$)/);
  return (m ? m[0] : String(text)).trim();
}

/**
 * @param {any} result the decision returned with the call (result.assessment is the borrower-facing part)
 * @returns {{tone: string, title: string, sentence: string, emiLabel: string, emiText: string} | null}
 */
export function resultHero(result) {
  if (!result) return null;
  const a = result.assessment;
  if (!a || !a.outcome) {
    return { tone: "info", title: "Result ready", sentence: "", emiLabel: "", emiText: "" };
  }
  const hero = { tone: a.outcome.tone || "info", title: a.outcome.title, sentence: firstSentence(a.outcome.detail),
    emiLabel: "", emiText: "" };
  if (a.status === "paused") {
    hero.sentence = (a.needed && a.needed[0]) || hero.sentence;
    return hero;
  }
  const afford = a.affordability;
  if (afford && typeof afford.value === "number") {
    hero.emiLabel = "EMI you can afford for a new loan";
    hero.emiText = afford.value > 0 ? afford.text : "No room for a new EMI right now";
  }
  return hero;
}
