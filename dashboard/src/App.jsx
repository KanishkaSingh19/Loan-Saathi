import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { loadKey, saveKey, sendPrescreen, Unauthorized, wakeServer } from "./api";
import { usePrescreens } from "./usePrescreens";
import { FILTERS, SAMPLES } from "./constants";
import KeyGate from "./components/KeyGate";
import StatTiles from "./components/StatTiles";
import Feed from "./components/Feed";
import PrescreenDetail from "./components/PrescreenDetail";
import CallDetail from "./components/CallDetail";
import CallScreen from "./components/CallScreen";
import AdvisorCases from "./components/AdvisorCases";
import EvaluationLab from "./components/EvaluationLab";
import { StudyReport } from "./components/Study";

// ?study=1 turns on the user-study kit (consent, persona cards, survey, Study tab). See STUDY.md.
const STUDY_MODE = new URLSearchParams(window.location.search).has("study");

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
  const [view, setView] = useState("call");
  const logout = useCallback(() => setKey(""), []);
  const knownIds = useRef(null);

  // The API may be asleep (free hosting): start waking it while the landing page is read.
  useEffect(() => { wakeServer(); }, []);

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

  const openPrescreen = async (id) => {
    await refresh();
    setFilter("all");
    setFollowLive(false);
    setSelectedId(`p-${id}`);
    setView("feed");
  };

  // Pre-screens, advisor cases and the study report hold callers' personal data: staff key only.
  const staffView = view === "feed" || view === "cases" || view === "study";
  const locked = !key || status === "locked";

  const statusText = {
    connecting: "Connecting…",
    live: `Live · updated ${updatedAt?.toLocaleTimeString() ?? ""}`,
    offline: "Server unreachable, retrying…",
    error: "Server can't load data (check the database), retrying…",
  }[status];

  return (
    <div className="page">
      <header className="topbar">
        <button type="button" className="brand brand-home" onClick={() => setView("call")}
          aria-label="Loan-Saathi home">
          <div className="brand-mark" aria-hidden="true">₹</div>
          <div>
            <h1>
              Loan-<span>Saathi</span>
            </h1>
            <p className="muted">Voice loan pre-screening</p>
          </div>
        </button>
        {/* Borrowers see only the conversation. Staff views and the evidence get their own small nav. */}
        {view === "call" ? <span className="nav-spacer" /> : (
          <nav className="views" role="tablist" aria-label="Staff and evidence views">
            <button role="tab" aria-selected={view === "feed"} className={`chip${view === "feed" ? " active" : ""}`}
              onClick={() => setView("feed")}>Pre-screens</button>
            <button role="tab" aria-selected={view === "cases"} className={`chip${view === "cases" ? " active" : ""}`}
              onClick={() => setView("cases")}>Advisor cases</button>
            <button role="tab" aria-selected={view === "eval"} className={`chip${view === "eval" ? " active" : ""}`}
              onClick={() => setView("eval")}>Evaluation Lab</button>
            {STUDY_MODE && (
              <button role="tab" aria-selected={view === "study"} className={`chip${view === "study" ? " active" : ""}`}
                onClick={() => setView("study")}>Study</button>
            )}
          </nav>
        )}
        <div className="topbar-actions">
          {staffView && !locked && (
            <div className={`status ${status}`} role="status">
              <span className="dot" />
              {statusText}
            </div>
          )}
          {view === "call" ? (
            <button type="button" className="btn link-quiet" onClick={() => setView("feed")}>
              For advisors
            </button>
          ) : (
            <button type="button" className="btn primary cta" onClick={() => setView("call")}>
              Talk to Saathi
            </button>
          )}
        </div>
      </header>

      {staffView && locked ? (
        <main className="layout call-layout">
          <KeyGate retry={triedKey} onSubmit={submitKey} />
        </main>
      ) : view === "study" ? (
        <StudyReport apiKey={key} onUnauthorized={logout} />
      ) : view === "eval" ? (
        <EvaluationLab apiKey={key} onUnauthorized={logout} />
      ) : view === "cases" ? (
        <AdvisorCases apiKey={key} onUnauthorized={logout} />
      ) : view === "call" ? (
        <CallScreen apiKey={key} onUnauthorized={logout} onOpenPrescreen={openPrescreen} studyMode={STUDY_MODE} />
      ) : (
      <>
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
          <span className="muted small">Try a sample caller:</span>
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
              <PrescreenDetail prescreen={selected.data} apiKey={key} onUnauthorized={() => setKey("")} />
            )
          ) : (
            <div className="empty">
              <p className="muted">Select a caller to see the full breakdown.</p>
            </div>
          )}
        </section>
      </main>
      </>
      )}
      <footer className="site-footer">
        <span>Loan-Saathi gives an estimated pre-screening, not a lender approval.</span>
        {view !== "eval" && (
          <button type="button" className="link-quiet" onClick={() => setView("eval")}>
            How Saathi was tested → Evaluation Lab
          </button>
        )}
      </footer>
    </div>
  );
}
