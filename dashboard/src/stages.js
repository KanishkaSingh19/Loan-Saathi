// The Loan-Saathi pipeline, as the call screen shows it. The server reports the stage
// (converse.stage()); while the mic records or Saathi is working, the screen moves ahead of it.

export const STAGES = [
  { id: "LISTEN", label: "Listen", caption: "Speak or type. Saathi listens in Hindi, Hinglish or English." },
  { id: "EXTRACT", label: "Extract", caption: "Picking out the amounts and answers you said." },
  { id: "VALIDATE", label: "Validate", caption: "Checking a number that needs a second look." },
  { id: "CONFIRM", label: "Confirm", caption: "You confirm what Saathi understood. Nothing is assessed before this." },
  { id: "ASSESS", label: "Assess", caption: "Risk model, affordability rule and safety checks." },
  { id: "EXPLAIN", label: "Explain", caption: "Your result, and why." },
  { id: "SIMULATE", label: "Simulate", caption: "Try other amounts and tenures. Simulation, not a lender offer." },
  { id: "HANDOFF", label: "Handoff", caption: "Next steps, or a summary saved for a loan advisor." },
];

/**
 * The stage to highlight.
 * @param {string | undefined} serverStage what the server says (call.stage)
 * @param {string | null} busy the call screen's activity: listening, transcribing, thinking, speaking...
 */
export function currentStage(serverStage, busy) {
  if (busy === "listening" || busy === "transcribing") return "LISTEN";
  if (busy === "thinking" && (!serverStage || ["LISTEN", "EXTRACT", "VALIDATE"].includes(serverStage))) {
    return "EXTRACT";
  }
  return serverStage || "LISTEN";
}

/** done / now / next for each stage. */
export function stageStatus(current) {
  const at = STAGES.findIndex((s) => s.id === current);
  return STAGES.map((s, i) => ({ ...s, status: i < at ? "done" : i === at ? "now" : "next" }));
}
