import { useEffect, useMemo, useRef, useState } from "react";
import { loadKey, saveKey, sendPrescreen, Unauthorized } from "./api";
import { usePrescreens } from "./usePrescreens";
import { FILTERS, SAMPLES } from "./constants";
import KeyGate from "./components/KeyGate";
import StatTiles from "./components/StatTiles";
import Feed from "./components/Feed";
import PrescreenDetail from "./components/PrescreenDetail";
import CallDetail from "./components/CallDetail";

const FRESH_MS = 4000;

export default function App() {
  const [key, setKey] = useState(loadKey);
  const [triedKey, setTriedKey] = useState(false);
  const { prescreens, calls, status, updatedAt, refresh } = usePrescreens(key);
  const [filter, setFilter] = useState("all");
  const [selectedId, setSelectedId] = useState(null);
  const [followLive, setFollowLive] = useState(true);
  const [freshIds, setFreshIds] = useState(new Set());
  const [sending, setSending] = useState(null);
  const knownIds = useRef(null);

  // One feed of pre-screens and post-call logs, newest first.
  const items = useMemo(
    () =>
      [
        ...prescreens.map((p) => ({ key: `p-${p.id}`, kind: "prescreen", ts: p.ts, data: p })),
        ...calls.map((c) => ({ key: `c-${c.id}`, kind: "call", ts: c.ts, data: c })),
      ].sort((a, b) => b.ts - a.ts),
    [prescreens, calls],
  );

  // Spot new arrivals: highlight them briefly and, in follow-live mode, open the newest.
  useEffect(() => {
    if (status !== "live") return;
    const ids = items.map((i) => i.key);
    if (knownIds.current === null) {
      knownIds.current = new Set(ids);
      if (!selectedId && ids.length) setSelectedId(ids[0]);
      return;
    }
    const fresh = ids.filter((id) => !knownIds.current.has(id));
    if (!fresh.length) return;
    fresh.forEach((id) => knownIds.current.add(id));
    setFreshIds((prev) => new Set([...prev, ...fresh]));
    setTimeout(() => {
      setFreshIds((prev) => new Set([...prev].filter((id) => !fresh.includes(id))));
    }, FRESH_MS);
    if (followLive) setSelectedId(fresh[0]);
  }, [items, status, followLive, selectedId]);

  const visible = useMemo(() => {
    if (filter === "all") return items;
    const f = FILTERS.find((x) => x.id === filter);
    const match = f.match || [f.id];
    return items.filter((i) => i.kind === "prescreen" && match.includes(i.data.result.decision));
  }, [items, filter]);

  const selected = items.find((i) => i.key === selectedId) || visible[0];

  const submitKey = (k) => {
    saveKey(k);
    knownIds.current = null;
    setTriedKey(true);
    setKey(k);
  };

  const sendSample = async (name) => {
    setSending(name);
    try {
      await sendPrescreen(key, SAMPLES[name]);
      await refresh();
    } catch (err) {
      if (err instanceof Unauthorized) setKey("");
    } finally {
      setSending(null);
    }
  };

  if (status === "locked") {
    return (
      <div className="page">
        <KeyGate retry={triedKey} onSubmit={submitKey} />
      </div>
    );
  }

  const statusText = {
    connecting: "Connecting…",
    live: `Live · updated ${updatedAt?.toLocaleTimeString() ?? ""}`,
    offline: "Server unreachable, retrying…",
  }[status];

  return (
    <div className="page">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">₹</div>
          <div>
            <h1>
              Loan <span>Saathi</span>
            </h1>
            <p className="muted">Live voice pre-screens</p>
          </div>
        </div>
        <div className={`status ${status}`} role="status">
          <span className="dot" />
          {statusText}
        </div>
      </header>

      <StatTiles prescreens={prescreens} calls={calls} />

      <div className="toolbar">
        <div className="chips" role="tablist" aria-label="Filter by decision">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              role="tab"
              aria-selected={filter === f.id}
              className={`chip${filter === f.id ? " active" : ""}`}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="toolbar-right">
          <label className="toggle">
            <input type="checkbox" checked={followLive} onChange={(e) => setFollowLive(e.target.checked)} />
            Follow live
          </label>
          <span className="muted small">Test caller:</span>
          {Object.keys(SAMPLES).map((name) => (
            <button key={name} className="btn" disabled={sending !== null} onClick={() => sendSample(name)}>
              {sending === name ? "Sending…" : name}
            </button>
          ))}
        </div>
      </div>

      <main className="layout">
        <aside className="panel feed-panel">
          <Feed items={visible} selectedId={selected?.key} freshIds={freshIds} onSelect={setSelectedId} />
        </aside>
        <section className="panel detail-panel">
          {selected ? (
            selected.kind === "call" ? (
              <CallDetail call={selected.data} />
            ) : (
              <PrescreenDetail prescreen={selected.data} />
            )
          ) : (
            <div className="empty">
              <p className="muted">Select a caller to see the full breakdown.</p>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
