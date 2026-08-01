/* Morning — the pre-market digest: one page that answers "what's the market
   doing, did Strategy #1 fire, what needs my eyes, what's coming, where's the
   juice" before the open. Aggregates, never re-derives: every number is served
   by the canonical endpoint that owns it, and every card deep-links to the
   full view. The vol-watch and screener sections (absorbed from the old
   Strangles tab) fetch their live numbers separately so this page paints
   instantly from stored data. */
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { AttentionItem, Digest, LiveMarket, WatchtowerCard } from "../api/types";
import { DDPanel } from "../components/DDPanel";
import { MetricLegend, ScreenerSection, VolWatchSection } from "../components/StrangleTools";
import { TradeEditor } from "../components/TradeEditor";
import { Modal, Pnl, Skeleton, StatTile } from "../components/ui";
import { fmtDayShort, money, num, strategyLabel, timeShort } from "../lib/format";
import { navigate } from "../lib/router";
import { useAuth } from "../state/store";
import { PulseStrip, S1Banner, SPct, tradingDaysSince } from "./Signals";

/* reason → badge, in the backend's priority order */
const REASON_BADGE: Record<string, { label: (i: AttentionItem) => string; cls: string }> = {
  threatened: { label: (i) => (i.moneyness === "ITM" ? "ITM" : "ATM — tested"), cls: "warn" },
  expiring: { label: (i) => `${i.dte}d to expiry`, cls: "warn" },
  pt_hit: { label: (i) => `${num(i.pct_of_credit, 0)}% of credit — PT`, cls: "good" },
  earnings: { label: (i) => `earnings ${i.earnings ? fmtDayShort(i.earnings) : ""}`, cls: "warn" },
  clock: { label: (i) => `${i.dte} DTE — in the window`, cls: "" },
};

function AttentionCard({ items, onOpen }: { items: AttentionItem[]; onOpen: (id: number) => void }) {
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <h3 className="section-title" style={{ margin: 0 }}>Needs eyes</h3>
        <button className="btn btn-ghost btn-sm" onClick={() => navigate("positions")}>
          Positions →
        </button>
      </div>
      {items.length === 0 ? (
        <p className="muted small" style={{ margin: 0 }}>
          Quiet book — nothing threatened, expiring, or at target. The best positions are the
          ones never touched.
        </p>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {items.map((i) => (
            <button
              key={i.trade_id}
              className="row"
              style={{ background: "none", border: 0, color: "inherit", font: "inherit", cursor: "pointer", gap: 8, flexWrap: "wrap", textAlign: "left", minHeight: 44 }}
              title="open in the journal editor"
              onClick={() => onOpen(i.trade_id)}
            >
              <span className="sym">{i.underlying}</span>
              <span className="muted small">
                {strategyLabel(i.direction)}
                {i.strikes ? ` ${i.strikes}` : ""}
              </span>
              {i.reasons.map((r) =>
                REASON_BADGE[r] ? (
                  <span key={r} className={"badge " + REASON_BADGE[r].cls}>
                    {REASON_BADGE[r].label(i)}
                  </span>
                ) : null
              )}
              <span className="num small" style={{ marginLeft: "auto" }}>
                <Pnl v={i.unrealized_pnl} />
                {i.day_pnl != null ? (
                  <span className="muted"> · day <Pnl v={i.day_pnl} /></span>
                ) : null}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const QUOTE_STRIP: [string, string][] = [
  ["SPY", "SPY"], ["QQQ", "QQQ"], ["DIA", "DIA"], ["IWM", "IWM"], ["^VIX", "VIX"],
];

function sentimentClass(label: string): string {
  if (label.includes("risk-on")) return "good";
  if (label === "cautious" || label === "risk-off") return "warn";
  return "";
}

/* The Refresh button's payload: latest tape + the code-computed sentiment
   gauges. Every chip is a served number — nothing is derived client-side. */
function LiveReadCard({ live, error }: { live: LiveMarket | null; error: string }) {
  // each gauge's explanation lived only in a hover title — dead on touch
  const [gauge, setGauge] = useState<string | null>(null);
  if (error) {
    return (
      <div className="card" style={{ marginBottom: 12 }}>
        <h3 className="section-title" style={{ marginTop: 0 }}>Live read</h3>
        <p className="muted small" style={{ margin: 0 }}>live pull failed — {error}</p>
      </div>
    );
  }
  if (!live) return null;
  const s = live.sentiment;
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <h3 className="section-title" style={{ margin: 0 }}>Live read</h3>
        <span className="muted small num">
          {live.session} · as of {timeShort(live.as_of)}
        </span>
      </div>
      <div className="pulse-strip num" style={{ marginBottom: 8 }}>
        {QUOTE_STRIP.map(([sym, label]) => {
          const q = live.quotes[sym];
          if (!q || q.last == null) return null;
          return (
            <span key={sym}>
              {label} <b>{num(q.last)}</b> <SPct v={q.d1_pct} />
            </span>
          );
        })}
      </div>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <span className={"badge " + sentimentClass(s.label)} title={s.score != null ? `composite ${s.score} on [-1,+1]` : undefined}>
          sentiment: {s.label}
        </span>
        {s.components.map((c) => (
          <button
            key={c.key}
            className="badge-btn"
            style={gauge === c.key ? { borderColor: "var(--accent)" } : undefined}
            onClick={() => setGauge(gauge === c.key ? null : c.key)}
          >
            {c.label}{c.value != null ? ` ${c.value}` : ""} · {c.read}
          </button>
        ))}
      </div>
      {gauge ? (
        <p className="hint-line" style={{ marginBottom: 0 }}>
          {s.components.find((c) => c.key === gauge)?.detail}
        </p>
      ) : (
        <p className="hint-line" style={{ marginBottom: 0 }}>
          Code-computed gauges (VIX, term structure, breadth, haven bid) — tap one for what it means.
        </p>
      )}
    </div>
  );
}

function CalendarCard({ cal }: { cal: Digest["calendar"] }) {
  const days = Array.from(
    new Set([...Object.keys(cal.expirations), ...Object.keys(cal.earnings)])
  ).sort();
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <h3 className="section-title" style={{ margin: 0 }}>Next {cal.horizon_days} days</h3>
        <span className="muted small num">
          new-entry window {fmtDayShort(cal.dte_window.start)}–{fmtDayShort(cal.dte_window.end)}
        </span>
      </div>
      {days.length === 0 ? (
        <p className="muted small" style={{ margin: 0 }}>
          No expirations or known earnings inside the window.
        </p>
      ) : (
        <div style={{ display: "grid", gap: 4 }}>
          {days.map((d) => (
            <div key={d} className="row small" style={{ gap: 8, flexWrap: "wrap" }}>
              <span className="num" style={{ minWidth: 64, color: "var(--ink-2)" }}>{fmtDayShort(d)}</span>
              {(cal.expirations[d] || []).map((e) => (
                <span key={e.id} className="badge warn" title={strategyLabel(e.direction)}>
                  exp {e.underlying} {e.strikes || ""}
                </span>
              ))}
              {(cal.earnings[d] || []).map((e) => (
                <span key={e.symbol} className={"badge " + (e.held ? "warn" : "")}
                      title={e.held ? "you hold this name" : "watchlist name"}>
                  ⚡ {e.symbol}{e.held ? " (held)" : ""}
                </span>
              ))}
            </div>
          ))}
        </div>
      )}
      <p className="hint-line" style={{ marginBottom: 0 }}>
        Earnings dates are last night's stored snapshot (research + vol watch names).
      </p>
    </div>
  );
}

function WatchtowerStrip({ cards }: { cards: WatchtowerCard[] }) {
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <h3 className="section-title" style={{ margin: 0 }}>Watchtower — last 3 days</h3>
        <button className="btn btn-ghost btn-sm" onClick={() => navigate("signals")}>
          Signals →
        </button>
      </div>
      {cards.length === 0 ? (
        <p className="muted small" style={{ margin: 0 }}>
          No alerts — the sweep writes cards only when a washout meets the gates; silence is normal.
        </p>
      ) : (
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {cards.map((c, i) => (
            <span key={`${c.date}-${c.symbol}-${i}`} className="row" style={{ gap: 4 }}>
              <span className={"badge " + (c.alert_class === "TRADE" ? "good" : "warn")}>
                {c.symbol} · {c.tier}
                {c.taken_trade_id != null ? " ✓" : ""}
              </span>
              <span className="muted small num">{fmtDayShort(c.date)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function PlansCard({ d }: { d: Digest }) {
  if (!d.intents.length && !d.research_flags.length) return null;
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      {d.intents.length > 0 ? (
        <>
          <h3 className="section-title" style={{ marginTop: 0 }}>Plans on deck</h3>
          <div style={{ display: "grid", gap: 4, marginBottom: d.research_flags.length ? 10 : 0 }}>
            {d.intents.map((i) => (
              <div key={i.id} className="row small" style={{ gap: 8 }}>
                <span className="sym">{i.underlying}</span>
                <span className="muted">{strategyLabel(i.direction)}</span>
                {i.edge_name ? <span className="badge sys">{i.edge_name}</span> : null}
                {i.planned_risk != null ? (
                  <span className="muted num">risk {money(i.planned_risk, { sign: false })}</span>
                ) : null}
                {i.note ? <span className="muted">— {i.note}</span> : null}
              </div>
            ))}
          </div>
        </>
      ) : null}
      {d.research_flags.length > 0 ? (
        <>
          <h3 className="section-title" style={{ marginTop: 0 }}>Research flags</h3>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {d.research_flags.map((r) => (
              <button
                key={r.symbol}
                className="badge-btn"
                title={`open ${r.symbol} research page`}
                onClick={() => navigate("research", r.symbol)}
              >
                {r.symbol}: {r.flags.join(" · ").replaceAll("_", " ")}
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

export function Morning() {
  const { accountEpoch } = useAuth();
  const [d, setD] = useState<Digest | null>(null);
  const [error, setError] = useState("");
  const [live, setLive] = useState<LiveMarket | null>(null);
  const [liveError, setLiveError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [openTrade, setOpenTrade] = useState<number | null>(null);
  const [dd, setDd] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setD(await api<Digest>("/journal/digest"));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  /* Live layer — never blocks the stored paint; failure degrades to a note. */
  const loadLive = useCallback(async () => {
    try {
      setLive(await api<LiveMarket>("/journal/digest/live"));
      setLiveError("");
    } catch (e) {
      setLiveError((e as Error).message);
    }
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await Promise.all([load(), loadLive()]);
    setRefreshing(false);
  }, [load, loadLive]);

  useEffect(() => {
    setD(null);
    setLive(null);
    load().then(loadLive); // stored digest first, then the network pull
  }, [load, loadLive, accountEpoch]);

  if (error) return <div className="card">{error}</div>;
  if (d === null) return <Skeleton h={110} n={4} />;

  const spy = d.market?.indexes?.SPY;
  const liveSpy = live?.quotes["SPY"];
  const liveVix = live?.quotes["^VIX"];
  const marketD1 = liveSpy?.d1_pct ?? spy?.d1;
  const s1 = d.strategy1;
  const fired = (s1?.signals || []).filter((s) => s.signal === "SELL");
  const s1Stale = s1 ? tradingDaysSince(s1.date) > 1 : false;

  return (
    <div>
      <div className="page-header">
        <h1>Morning</h1>
        <span className="muted small num">{d.date}</span>
        <button className="btn btn-ghost btn-sm" style={{ marginLeft: "auto" }} disabled={refreshing} onClick={refresh}>
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      <div className="tiles" style={{ marginBottom: 12 }}>
        <StatTile
          label="Market"
          value={marketD1 != null ? (marketD1 > 0 ? "+" : "") + num(marketD1) + "%" : "—"}
          valueClass={marketD1 != null ? (marketD1 >= 0 ? "pnl win" : "pnl loss") : ""}
          sub={
            liveSpy?.d1_pct != null
              ? `SPY live ${timeShort(live?.as_of)} · VIX ${liveVix?.last ?? d.market?.vix?.close ?? "—"}`
              : d.market
                ? `SPY d1 · VIX ${d.market.vix?.close ?? "—"} · pulse ${fmtDayShort(d.market.date || "")}`
                : "no pulse yet"
          }
        />
        <StatTile
          label="Strategy #1"
          value={s1 ? (fired.length ? "SELL " + fired.map((s) => s.ticker).join("+") : "FLAT") : "—"}
          valueClass={fired.length ? "pnl win" : ""}
          sub={s1 ? `${fmtDayShort(s1.date)}${s1Stale ? " · stale" : ""}` : "no signal file"}
        />
        <StatTile
          label="Open book"
          value={<Pnl v={d.book.total_unrealized} />}
          sub={<>{d.book.count} open · day <Pnl v={d.book.total_day_pnl} /></>}
        />
        <StatTile
          label="Theta / day"
          value={d.book.net_theta != null ? money(d.book.net_theta) : "—"}
          sub={d.book.net_delta != null ? `net Δ ${num(d.book.net_delta, 0)}` : undefined}
        />
        <StatTile
          label="Needs eyes"
          value={String(d.attention.length)}
          valueClass={d.attention.length ? "pnl loss" : ""}
          sub={d.debt.count ? `${d.debt.count} owing journal` : "journal clean"}
        />
      </div>

      <AttentionCard items={d.attention} onOpen={setOpenTrade} />
      <LiveReadCard live={live} error={liveError} />
      <S1Banner s1={d.strategy1} />
      <PulseStrip pulse={d.market} onOpenDD={setDd} />
      <WatchtowerStrip cards={d.watchtower} />
      <CalendarCard cal={d.calendar} />
      <PlansCard d={d} />
      <VolWatchSection />
      {/* the vol-watch column headers are abbreviations (IV/RV, IVR, Strdl%) —
          the legend must be reachable without expanding the screener */}
      <MetricLegend />
      <ScreenerSection />

      {openTrade != null && (
        <Modal wide title="Journal this trade" onClose={() => setOpenTrade(null)}>
          <TradeEditor tradeId={openTrade} onClose={() => setOpenTrade(null)} onChanged={load} />
        </Modal>
      )}
      {dd != null && (
        <Modal wide dismissable title={<span>{dd} <span className="muted" style={{ fontWeight: 400 }}>· deep dive</span></span>} onClose={() => setDd(null)}>
          <DDPanel symbol={dd} card={null} />
        </Modal>
      )}
    </div>
  );
}
