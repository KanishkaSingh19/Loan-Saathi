export default function StatTiles({ prescreens, calls }) {
  const count = (...ds) => prescreens.filter((p) => ds.includes(p.result.decision)).length;
  const offered = prescreens
    .filter((p) => p.result.offer_amount)
    .reduce((sum, p) => sum + p.result.offer_amount, 0);
  const tiles = [
    { value: prescreens.length, label: "Callers pre-screened" },
    { value: count("pre_approved"), label: "Pre-approved", tone: "good" },
    { value: count("counter_offer", "needs_review"), label: "Counter-offer / review", tone: "warn" },
    { value: count("not_eligible_now"), label: "Not eligible now", tone: "bad" },
    { value: offered ? `₹${(offered / 100000).toFixed(1)}L` : "–", label: "Total offered" },
    { value: calls.length, label: "Calls logged" },
  ];
  return (
    <section className="tiles" aria-label="Summary">
      {tiles.map((t) => (
        <div key={t.label} className={`tile ${t.tone || ""}`}>
          <strong>{t.value}</strong>
          <span>{t.label}</span>
        </div>
      ))}
    </section>
  );
}
