/* Signals — the watchtower alert feed, the daily Strategy #1 signal, and the
   market pulse, with taken-vs-not linkage into the journal. All three sources
   are cron-written files; any of them can be absent. */
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Pulse, S1Signal, SignalsFeed, WatchtowerCard } from "../api/types";
import { DDPanel } from "../components/DDPanel";
import { TradeEditor } from "../components/TradeEditor";
import { Empty, Modal, Skeleton } from "../components/ui";
import { fmtDayShort, money, num } from "../lib/format";
import { useAuth } from "../state/store";

/* trading days (Mon–Fri) strictly after `iso`, up to today */
export function tradingDaysSince(iso: string): number {
  const d = new Date(iso + "T00:00:00");
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  let n = 0;
  while (d < today) {
    d.setDate(d.getDate() + 1);
    const wd = d.getDay();
    if (wd !== 0 && wd !== 6 && d <= today) n++;
  }
  return n;
}

export function SPct({ v, digits = 1, suffix = "%" }: { v?: number | null; digits?: number; suffix?: string }) {
  if (v == null) return <span className="muted">—</span>;
  const cls = v > 0 ? "win" : v < 0 ? "loss" : "flat";
  return (
    <span className={"pnl " + cls}>
      {(v > 0 ? "+" : "") + v.toFixed(digits)}
      {suffix}
    </span>
  );
}

/* ---------------- Strategy #1 banner ---------------- */

function S1Chip({ s }: { s: S1Signal }) {
  const sell = s.signal === "SELL";
  const gapCut = s.fatgap_cutoff ?? 1.3;
  return (
    <div className={"sig-chip" + (sell ? " sell" : "")}>
      <div className="row" style={{ gap: 6 }}>
        <span className="t">{s.ticker}</span>
        <span className={"badge " + (sell ? "good" : "off")}>
          {s.signal}
          {sell && s.tier ? ` · ${s.tier}` : ""}
        </span>
        {s.quality ? (
          <span className="badge" title="data quality">
            {s.quality}
          </span>
        ) : null}
      </div>
      <div className="muted small num">
        {/* 3dp everywhere a verdict sits beside a number: at 2dp a 1.299 gap
            renders as "1.30/1.30" (a false ≥) and depth 0.941 as the exact
            0.94 STRONG bar — both read as the engine contradicting itself */}
        depth {s.depth != null ? num(s.depth, 3) : "—"}
        {" · "}gap {s.gap_ratio != null ? num(s.gap_ratio, 3) : "—"}/{num(gapCut)}
      </div>
    </div>
  );
}

export function S1Banner({ s1 }: { s1: SignalsFeed["strategy1"] }) {
  if (!s1 || !Array.isArray(s1.signals) || !s1.signals.length) {
    return (
      <div className="card" style={{ marginBottom: 12 }}>
        <h3>Strategy #1</h3>
        <p className="muted small" style={{ margin: 0 }}>
          No daily signal yet — the 08:00 ET cron hasn't written one.
        </p>
      </div>
    );
  }
  const stale = tradingDaysSince(s1.date) > 1;
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>Strategy #1 — daily signal</h3>
        <span className="muted small num">
          {s1.date}
          {stale ? (
            <span className="badge warn" style={{ marginLeft: 6 }}>
              stale
            </span>
          ) : null}
        </span>
      </div>
      <div className="row">
        {s1.signals.map((s) => (
          <S1Chip key={s.ticker} s={s} />
        ))}
      </div>
    </div>
  );
}

/* ---------------- pulse strip ---------------- */

export function PulseStrip({ pulse, onOpenDD }: { pulse: Pulse | null; onOpenDD: (sym: string) => void }) {
  if (!pulse) {
    return (
      <div className="card" style={{ marginBottom: 12 }}>
        <h3>Pulse</h3>
        <p className="muted small" style={{ margin: 0 }}>
          No market pulse yet — the 20:30 UTC cron hasn't written one.
        </p>
      </div>
    );
  }
  const spy = pulse.indexes?.SPY;
  const u = pulse.universe;
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <h3 style={{ margin: 0 }}>Pulse</h3>
        <span className="muted small num">{pulse.date}</span>
      </div>
      <div className="pulse-strip num">
        {pulse.vix ? (
          <span>
            VIX <b>{pulse.vix.close ?? "—"}</b> <SPct v={pulse.vix.chg_1d} suffix="" />
            {pulse.vix.pctile_1y != null ? <span className="muted"> · {pulse.vix.pctile_1y}th pctile</span> : null}
          </span>
        ) : null}
        {spy ? (
          <span>
            SPY <SPct v={spy.d1} />
            {spy.from_52w_hi != null ? <span className="muted"> · {num(spy.from_52w_hi)}% off hi</span> : null}
          </span>
        ) : null}
        {u ? (
          <span>
            breadth <b>{u.pct_down_1d != null ? `${num(u.pct_down_1d, 0)}% down` : "—"}</b>
            {u.pct_above_50dma != null ? <span className="muted"> · {num(u.pct_above_50dma, 0)}% &gt;50dma</span> : null}
          </span>
        ) : null}
        {pulse.gld?.d1 != null ? (
          <span>
            GLD <SPct v={pulse.gld.d1} />
          </span>
        ) : null}
        {pulse.tlt?.d1 != null ? (
          <span>
            TLT <SPct v={pulse.tlt.d1} />
          </span>
        ) : null}
        {u?.washouts_rsi35?.length ? (
          <span className="row" style={{ gap: 4 }}>
            <span className="muted">washouts</span>
            {u.washouts_rsi35.map((t) => (
              <button key={t} className="badge-btn" title={`open deep dive on ${t}`} onClick={() => onOpenDD(t)}>
                {t}
              </button>
            ))}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/* ---------------- watchtower cards ---------------- */

function WtCard({
  c,
  onOpenTrade,
  onOpenDD,
}: {
  c: WatchtowerCard;
  onOpenTrade: (id: number) => void;
  onOpenDD: (c: WatchtowerCard) => void;
}) {
  const p = c.put;
  const floor = c.floor ?? null;
  const meterPct =
    p && floor ? Math.max(0, Math.min(100, (p.prem_pct / floor) * 100)) : null;
  const paysFloor = p != null && floor != null && p.prem_pct >= floor;
  const showEarnings = c.earnings && p?.expiry ? c.earnings <= p.expiry : !!c.earnings && !p;
  const notTaken =
    c.alert_class === "TRADE" && c.taken_trade_id == null && tradingDaysSince(c.date) > 2;
  return (
    <div
      className={"card wt-card " + (c.alert_class === "TRADE" ? "wt-trade" : "wt-watch")}
      style={{ cursor: "pointer" }}
      role="button"
      tabIndex={0}
      title={`open deep dive on ${c.symbol}`}
      onClick={() => onOpenDD(c)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpenDD(c);
        }
      }}
    >
      <div className="row" style={{ gap: 6 }}>
        <strong style={{ color: "var(--ink)", fontSize: 15 }}>{c.symbol}</strong>
        <span className={"badge " + (c.tier === "STRONG" ? "tier-strong" : "tier-marginal")}>{c.tier}</span>
        <span className={"badge " + (c.alert_class === "TRADE" ? "good" : "warn")}>
          {c.alert_class === "WATCH" ? "👀 " : ""}
          {c.alert_class}
        </span>
        {c.source === "discovery" ? <span className="badge">🔭 discovery</span> : null}
        <span style={{ marginLeft: "auto" }} className="muted small num">
          {fmtDayShort(c.date)}
        </span>
      </div>
      <div className="muted small num" style={{ marginTop: 4 }}>
        spot {c.spot != null ? money(c.spot, { sign: false }) : "—"}
        {c.rsi != null ? ` · RSI ${num(c.rsi, 1)}` : ""}
        {c.zone_lo != null && c.zone_hi != null
          ? ` · zone ${num(c.zone_lo)}–${num(c.zone_hi)}${c.zone_touches ? ` ×${c.zone_touches}` : ""}`
          : " · no defended level"}
      </div>
      {p ? (
        <>
          <div className="num small" style={{ marginTop: 6, color: "var(--ink-2)" }}>
            <b style={{ color: "var(--ink)" }}>
              {money(p.strike, { sign: false })}P
            </b>{" "}
            {p.expiry} ({p.dte} DTE) · mid {money(p.mid, { sign: false })} · {num(p.otm_pct, 1)}% OTM
            {p.iv != null ? ` · IV ${num(p.iv, 0)}` : ""}
            {p.oi != null ? ` · OI ${p.oi}` : ""}
          </div>
          {floor != null && (
            <div className="wt-meter-row">
              <div className="pt-bar wt-meter">
                <div
                  className={"pt-fill" + (paysFloor ? " hit" : "")}
                  style={{ width: `${meterPct}%` }}
                />
              </div>
              <span className="small num" style={{ color: paysFloor ? "var(--win)" : "var(--muted)" }}>
                pays {num(p.prem_pct, 1)}% vs {num(floor, 0)}% floor
              </span>
            </div>
          )}
        </>
      ) : (
        <div className="muted small" style={{ marginTop: 6 }}>
          no candidate put on the chain
        </div>
      )}
      {showEarnings ? (
        <div className="small" style={{ marginTop: 4, color: "var(--warn)" }}>
          earnings {c.earnings}
        </div>
      ) : null}
      <div className="row" style={{ marginTop: 8, gap: 6 }}>
        {c.taken_trade_id != null ? (
          <button
            className="badge-btn good"
            onClick={(e) => {
              e.stopPropagation();
              onOpenTrade(c.taken_trade_id!);
            }}
          >
            ✓ TAKEN — trade #{c.taken_trade_id}
          </button>
        ) : notTaken ? (
          <span className="badge off">not taken</span>
        ) : null}
        <span className="muted small" style={{ marginLeft: "auto" }}>
          deep dive →
        </span>
      </div>
    </div>
  );
}

/* ---------------- view ---------------- */

const DAY_CHOICES: [string, number][] = [
  ["Today", 1],
  ["7D", 7],
  ["14D", 14],
];

export function Signals() {
  const { accountEpoch } = useAuth();
  const [days, setDays] = useState(7);
  const [feed, setFeed] = useState<SignalsFeed | null>(null);
  const [error, setError] = useState("");
  const [openTrade, setOpenTrade] = useState<number | null>(null);
  const [dd, setDd] = useState<{ symbol: string; card: WatchtowerCard | null } | null>(null);

  const load = useCallback(async (d: number) => {
    try {
      const r = await api<SignalsFeed>(`/journal/signals?days=${d}`);
      setFeed(r);
      setError("");
    } catch (e) {
      setError((e as Error).message);
      setFeed({ watchtower: [], strategy1: null, pulse: null });
    }
  }, []);

  useEffect(() => {
    setFeed(null);
    load(days);
  }, [load, days, accountEpoch]);

  if (feed === null) return <Skeleton h={110} n={3} />;
  if (error) return <Empty>{error}</Empty>;

  const cards = feed.watchtower || [];

  return (
    <div>
      <S1Banner s1={feed.strategy1} />
      <PulseStrip pulse={feed.pulse} onOpenDD={(sym) => setDd({ symbol: sym, card: null })} />

      <div className="page-header" style={{ marginTop: 18 }}>
        <h1 style={{ fontSize: 16 }}>Watchtower</h1>
        <div className="seg" role="group" aria-label="Days">
          {DAY_CHOICES.map(([label, d]) => (
            <button key={d} className={days === d ? "on" : ""} onClick={() => setDays(d)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {cards.length === 0 ? (
        <Empty hint="The 20:45 UTC sweep writes alerts only when a washout meets the gates — silence is normal.">
          No watchtower alerts in this window.
        </Empty>
      ) : (
        <div className="wt-grid">
          {cards.map((c, i) => (
            <WtCard
              key={`${c.date}-${c.symbol}-${i}`}
              c={c}
              onOpenTrade={setOpenTrade}
              onOpenDD={(card) => card.symbol && setDd({ symbol: card.symbol, card })}
            />
          ))}
        </div>
      )}

      <p className="chart-note">
        WATCH cards are research, not endorsement. A TRADE card is priced off its cohort; the journal
        decides whether it was worth taking.
      </p>

      {openTrade != null && (
        <Modal wide title="Journal this trade" onClose={() => setOpenTrade(null)}>
          <TradeEditor tradeId={openTrade} onClose={() => setOpenTrade(null)} onChanged={() => load(days)} />
        </Modal>
      )}

      {dd != null && (
        <Modal
          wide
          dismissable
          title={<span>{dd.symbol} <span className="muted" style={{ fontWeight: 400 }}>· deep dive</span></span>}
          onClose={() => setDd(null)}
        >
          <DDPanel symbol={dd.symbol} card={dd.card} />
        </Modal>
      )}
    </div>
  );
}
