import { DECISIONS } from "../constants";

export default function DecisionBadge({ decision }) {
  const d = DECISIONS[decision] || { label: decision, tone: "info" };
  return <span className={`badge ${d.tone}`}>{d.label}</span>;
}
