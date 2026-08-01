import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Fill, SyncResult } from "../api/types";
import { syncMessage } from "../lib/format";
import { navigate } from "../lib/router";
import { DISPLAY_MODES, useAuth, useDisplay, useToast } from "../state/store";
import { FilterBar } from "./FilterBar";
import { Modal } from "./ui";

/** Views where the global scope bar (filters) applies — the journal-data pages. */
const FILTERED_VIEWS = new Set(["dashboard", "logbook", "analytics"]);

const I = {
  morning: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 18a5 5 0 0 0-10 0" />
      <line x1="12" y1="9" x2="12" y2="2" /><line x1="4.22" y1="10.22" x2="5.64" y2="11.64" />
      <line x1="1" y1="18" x2="3" y2="18" /><line x1="21" y1="18" x2="23" y2="18" />
      <line x1="18.36" y1="11.64" x2="19.78" y2="10.22" /><line x1="23" y1="22" x2="1" y2="22" />
    </svg>
  ),
  dashboard: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" />
      <rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" />
    </svg>
  ),
  positions: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  ),
  signals: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="2" />
      <path d="M16.24 7.76a6 6 0 0 1 0 8.49" /><path d="M7.76 16.24a6 6 0 0 1 0-8.49" />
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14" /><path d="M4.93 19.07a10 10 0 0 1 0-14.14" />
    </svg>
  ),
  research: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.35-4.35" />
      <path d="M8 11h6" /><path d="M11 8v6" />
    </svg>
  ),
  logbook: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
    </svg>
  ),
  analytics: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" />
      <line x1="2" y1="20" x2="22" y2="20" />
    </svg>
  ),
  edges: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" />
    </svg>
  ),
  chat: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  ),
  settings: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
};

export const NAV = [
  { id: "morning", label: "Morning", icon: I.morning },
  { id: "dashboard", label: "Dashboard", icon: I.dashboard },
  { id: "positions", label: "Positions", icon: I.positions },
  { id: "signals", label: "Signals", icon: I.signals },
  { id: "research", label: "Research", icon: I.research },
  { id: "logbook", label: "Logbook", icon: I.logbook },
  { id: "analytics", label: "Analytics", icon: I.analytics },
  { id: "edges", label: "Edges", icon: I.edges },
  { id: "chat", label: "Chat", icon: I.chat },
  { id: "settings", label: "Settings", icon: I.settings },
];

/* The bottom bar can't hold 10 items at 375px (~37px each) — the daily-loop
   views stay, everything else lives in a "More" sheet. */
const PRIMARY_IDS = new Set(["morning", "dashboard", "positions", "logbook"]);
const PRIMARY = NAV.filter((n) => PRIMARY_IDS.has(n.id));
const SECONDARY = NAV.filter((n) => !PRIMARY_IDS.has(n.id));

const MoreIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="5" cy="12" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="19" cy="12" r="1.6" />
  </svg>
);

function NavItems({
  items,
  active,
  repairCount,
  debtCount,
  onNavigate,
}: {
  items: typeof NAV;
  active: string;
  repairCount: number;
  debtCount: number;
  onNavigate?: (id: string) => void;
}) {
  return (
    <>
      {items.map((v) => (
        <button
          key={v.id}
          className={"nav-item" + (active === v.id ? " active" : "")}
          onClick={() => (onNavigate ? onNavigate(v.id) : navigate(v.id))}
        >
          {v.icon}
          <span>{v.label}</span>
          {v.id === "settings" && repairCount > 0 ? (
            <i className="nav-dot" title={`${repairCount} fill${repairCount === 1 ? "" : "s"} awaiting repair`} />
          ) : null}
          {v.id === "dashboard" && debtCount > 0 ? (
            <span className="nav-count" title={`${debtCount} trade${debtCount === 1 ? "" : "s"} owing journal work`}>
              {debtCount}
            </span>
          ) : null}
        </button>
      ))}
    </>
  );
}

export function Shell({ view, children }: { view: string; children: ReactNode }) {
  const { accounts, activeAccount, switchAccount, accountEpoch } = useAuth();
  const { mode, setMode } = useDisplay();
  const toast = useToast();
  const [syncing, setSyncing] = useState(false);
  const [repairCount, setRepairCount] = useState(0);
  const [debtCount, setDebtCount] = useState(0);
  const [moreOpen, setMoreOpen] = useState(false);
  const title = NAV.find((n) => n.id === view)?.label || "";
  const moreActive = SECONDARY.some((n) => n.id === view);

  useEffect(() => {
    let dead = false;
    const check = () => {
      api<{ fills: Fill[] }>("/journal/fills")
        .then((r) => !dead && setRepairCount((r.fills || []).length))
        .catch(() => {});
      api<{ count: number }>("/journal/debt")
        .then((r) => !dead && setDebtCount(r.count || 0))
        .catch(() => {});
    };
    check();
    window.addEventListener("pt:synced", check);
    return () => {
      dead = true;
      window.removeEventListener("pt:synced", check);
    };
  }, [accountEpoch, view]);

  const syncNow = async () => {
    setSyncing(true);
    try {
      const r = await api<SyncResult>("/journal/fills/sync", { method: "POST" });
      toast(syncMessage(r));
      window.dispatchEvent(new CustomEvent("pt:synced"));
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="tick">▞</span> Trading Desk
        </div>
        <nav>
          <NavItems items={NAV} active={view} repairCount={repairCount} debtCount={debtCount} />
        </nav>
        <div className="spacer" />
        <div className="foot">
          money is whispered,
          <br />
          discipline is shouted.
        </div>
      </aside>

      <div className="content">
        <header className="topbar">
          <span className="title">{title}</span>
          <div className="seg seg-display" role="group" aria-label="Display mode"
               title="How P&L renders everywhere: $ / R-multiples / % of account / privacy">
            {DISPLAY_MODES.map((m) => (
              <button
                key={m.id}
                className={mode === m.id ? "on" : ""}
                title={m.title}
                onClick={() => setMode(m.id)}
              >
                {m.label}
              </button>
            ))}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={syncNow} disabled={syncing}>
            {syncing ? "Syncing…" : "Sync"}
          </button>
          {accounts.length > 0 && (
            <select
              aria-label="Account"
              style={{ width: "auto", minHeight: 36, padding: "5px 8px" }}
              value={activeAccount ?? ""}
              onChange={(e) => switchAccount(Number(e.target.value))}
            >
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          )}
        </header>
        <main className="main">
          {FILTERED_VIEWS.has(view) ? <FilterBar /> : null}
          {children}
        </main>
      </div>

      <nav className="bottombar">
        <NavItems items={PRIMARY} active={view} repairCount={repairCount} debtCount={debtCount} />
        <button className={"nav-item" + (moreActive ? " active" : "")} onClick={() => setMoreOpen(true)}>
          {MoreIcon}
          <span>More</span>
          {repairCount > 0 ? <i className="nav-dot" /> : null}
        </button>
      </nav>

      {moreOpen ? (
        <Modal dismissable title="More" onClose={() => setMoreOpen(false)}>
          <nav className="more-nav">
            <NavItems
              items={SECONDARY}
              active={view}
              repairCount={repairCount}
              debtCount={debtCount}
              onNavigate={(id) => {
                setMoreOpen(false);
                navigate(id);
              }}
            />
          </nav>
          <div className="row" style={{ marginTop: 14, gap: 10 }}>
            <span className="muted small">Display</span>
            <div className="seg" role="group" aria-label="Display mode">
              {DISPLAY_MODES.map((m) => (
                <button key={m.id} className={mode === m.id ? "on" : ""} title={m.title} onClick={() => setMode(m.id)}>
                  {m.label}
                </button>
              ))}
            </div>
          </div>
          {accounts.length > 1 ? (
            <div className="row" style={{ marginTop: 10, gap: 10 }}>
              <span className="muted small">Account</span>
              <select
                aria-label="Account"
                style={{ width: "auto" }}
                value={activeAccount ?? ""}
                onChange={(e) => switchAccount(Number(e.target.value))}
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
        </Modal>
      ) : null}
    </div>
  );
}
