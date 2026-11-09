// Where the applicant is in the journey: Talk → Profile → Confirm → Assessment → What-if → Next steps → Advisor.
const STEPS = [
  { id: "talk", label: "Talk" },
  { id: "profile", label: "Your details" },
  { id: "confirm", label: "Confirm" },
  { id: "assessment", label: "Assessment" },
  { id: "whatif", label: "What-if" },
  { id: "next", label: "Next steps" },
  { id: "advisor", label: "Advisor" },
];

const STATE_STEP = {
  START: "talk",
  COLLECTING_PROFILE: "profile",
  CORRECTION_REQUIRED: "confirm",
  VERIFYING_INFORMATION: "confirm",
  CONFIRMED: "assessment",
  ASSESSING_RISK: "assessment",
  CHECKING_AFFORDABILITY: "assessment",
  SHOWING_RESULT: "assessment",
  WHAT_IF: "whatif",
  NEXT_STEPS: "next",
  COMPLETED: "next",
  HUMAN_HANDOFF: "advisor",
  ERROR_RECOVERY: "profile",
};

export function journeyStep(state) {
  return state ? STATE_STEP[state] || "profile" : "talk";
}

export default function Journey({ state }) {
  const current = journeyStep(state);
  const at = STEPS.findIndex((s) => s.id === current);
  return (
    <ol className="journey" aria-label="Your progress">
      {STEPS.map((s, i) => (
        <li key={s.id} className={i < at ? "done" : i === at ? "now" : ""} aria-current={i === at ? "step" : undefined}>
          {s.label}
        </li>
      ))}
    </ol>
  );
}
