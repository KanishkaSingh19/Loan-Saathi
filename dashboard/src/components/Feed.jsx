import DecisionBadge from "./DecisionBadge";
import { isBlank } from "../constants";

const time = (ts) =>
  new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function Summary({ said }) {
  const parts = [];
  if (!isBlank(said.loan_amount)) parts.push(`wants ${said.loan_amount}`);
  if (!isBlank(said.monthly_income)) parts.push(`earns ${said.monthly_income}`);
  if (!isBlank(said.work)) parts.push(said.work);
  return <span className="feed-summary">{parts.join(" · ") || "No details yet"}</span>;
}

export default function Feed({ items, selectedId, freshIds, onSelect }) {
  if (!items.length) {
    return (
      <div className="empty">
        <p>Waiting for the first caller…</p>
        <p className="muted">Call the agent, or send a test caller above.</p>
      </div>
    );
  }
  return (
    <ul className="feed" aria-label="Pre-screens">
      {items.map((item) => (
        <li key={item.key}>
          <button
            className={`feed-item${item.key === selectedId ? " selected" : ""}${freshIds.has(item.key) ? " fresh" : ""}`}
            onClick={() => onSelect(item.key)}
          >
            <span className="feed-top">
              {item.kind === "call" ? (
                <span className="badge info">Call ended</span>
              ) : (
                <DecisionBadge decision={item.data.result.decision} />
              )}
              <time>{time(item.ts)}</time>
            </span>
            {item.kind === "call" ? (
              <span className="feed-summary">Post-call data from Gnani</span>
            ) : (
              <Summary said={item.data.said} />
            )}
          </button>
        </li>
      ))}
    </ul>
  );
}
