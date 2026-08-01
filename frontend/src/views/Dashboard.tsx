import { Suspense, lazy, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Analytics, CalendarDay, EquityPoint, JournalDebtItem, Motd, Position, Score, SignalsFeed } from "../api/types";
import { CalendarHeatmap } from "../components/CalendarHeatmap";
import { ScoreCard } from "../components/ScoreCard";
import { TradeEditor } from "../components/TradeEditor";
import { Empty, Modal, Pnl, Skeleton, StatTile } from "../components/ui";
import { num, pct, pnlClass } from "../lib/format";
import { navigate } from "../lib/router";
import { useAuth, useDisplay, useFilters } from "../state/store";

const EquityChart = lazy(() =>
  import("../components/EquityChart")
    .then((m) => ({ default: m.EquityChart }))
    // a redeploy deletes the old hashed chunk out from under a session that's
    // still open — degrade to a note instead of crashing the dashboard
    .catch(() => ({
      default: () => (
        <div className="empty">The app was updated under this session — reload to get the chart back.</div>
      ),
    })),
);

/* The journal queue: every trade owing work, one tap from its editor.
   Hidden entirely when the record is clean — absence is the reward. */
function JournalQueue() {
  const { accountEpoch } = useAuth();
  const [debt, setDebt] = useState<{ count: number; items: JournalDebtItem[] } | null>(null);
  const [openTrade, setOpenTrade] = useState<number | null>(null);

  const load = () =>
    api<{ count: number; items: JournalDebtItem[] }>("/journal/debt").then(setDebt).catch(() => {});
  useEffect(() => {
    load();
    window.addEventListener("pt:synced", load);
    return () => window.removeEventListener("pt:synced", load);
  }, [accountEpoch]);

  if (!debt || debt.count === 0) return null;
  return (
    <div className="card debt-queue" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 className="section-title" style={{ margin: 0 }}>
          Needs journaling <span className="badge warn">{debt.count}</span>
        </h3>
        <span className="muted small">tap a trade — the record should say</span>
      </div>
      {debt.items.slice(0, 8).map((it) => (
        <button key={it.id} className="debt-row" onClick={() => setOpenTrade(it.id)}>
          <span className="sym">{it.underlying || "?"}</span>
          {it.direction ? <span className="badge">{it.direction.replace(/_/g, " ")}</span> : null}
          {it.strikes ? <span className="muted small num">{it.strikes}</span> : null}
          <span className={"badge " + (it.status === "open" ? "sys" : "off")}>{it.status}</span>
          <span className="muted small missing">missing {it.missing.join(", ")}</span>
        </button>
      ))}
      {debt.count > 8 ? <p className="muted small" style={{ margin: "6px 0 0" }}>+{debt.count - 8} more…</p> : null}
      {openTrade != null ? (
        <Modal title="Journal this trade" onClose={() => setOpenTrade(null)}>
          <TradeEditor
            tradeId={openTrade}
            onClose={() => setOpenTrade(null)}
            onChanged={() => {
              load();
              window.dispatchEvent(new CustomEvent("pt:synced"));
            }}
          />
        </Modal>
      ) : null}
    </div>
  );
}

export function MotdBanner({ m }: { m: Motd }) {
  const stats = m.trades_today ? (
    <>
      {m.trades_today} closed today · {m.system} system / {m.discretionary} discretionary
      {m.avg_conviction ? ` · conviction ${m.avg_conviction}` : ""}
      {" · "}
      <Pnl v={m.realized_pnl} />
    </>
  ) : (
    <>streak holding at {m.current_streak}</>
  );
  return (
    <div className="motd">
      <div className="motd-streak">
        <span className="n num">{m.current_streak}</span>
        <span className="l">streak</span>
      </div>
      <div>
        <div className="motd-headline">{m.headline}</div>
        <div className="motd-stats">{stats}</div>
      </div>
    </div>
  );
}

/* One slim line: today's Strategy #1 calls + watchtower TRADE-alert count → Signals. */
function SignalsChipRow({ feed }: { feed: SignalsFeed | null }) {
  if (!feed) return null;
  const s1 = feed.strategy1?.signals || [];
  const tradeCards = (feed.watchtower || []).filter((c) => c.alert_class === "TRADE").length;
  if (!s1.length && !tradeCards) return null;
  return (
    <div className="sig-row">
      <span>Signals{feed.strategy1?.date ? ` ${feed.strategy1.date}` : ""}:</span>
      {s1.map((s) => (
        <button
          key={s.ticker}
          className={"badge-btn " + (s.signal === "SELL" ? "good" : "off")}
          onClick={() => navigate("signals")}
        >
          {s.ticker} {s.signal}
        </button>
      ))}
      {tradeCards > 0 && (
        <button className="badge-btn warn" onClick={() => navigate("signals")}>
          {tradeCards} trade alert{tradeCards === 1 ? "" : "s"}
        </button>
      )}
      <a href="#/signals">details →</a>
    </div>
  );
}

/* Compact open-book strip: top 3 by |unrealized| + total, linking to Positions. */
function PositionsStrip({ positions }: { positions: Position[] }) {
  if (!positions.length) return null;
  const marked = positions.filter((p) => p.unrealized_pnl != null);
  const total = marked.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
  const top = [...positions]
    .sort((a, b) => Math.abs(b.unrealized_pnl ?? 0) - Math.abs(a.unrealized_pnl ?? 0))
    .slice(0, 3);
  return (
    <div className="card" style={{ marginBottom: 14, padding: "12px 14px" }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <h3 style={{ margin: 0 }}>Open positions ({positions.length})</h3>
        <span className="small num">
          unrealized <Pnl v={marked.length ? total : null} />
          {marked.length < positions.length ? (
            <span className="muted"> · {positions.length - marked.length} awaiting marks</span>
          ) : null}
        </span>
      </div>
      <div className="pos-strip">
        {top.map((p) => (
          <button key={p.id} className="pos-chip" onClick={() => navigate("positions")}>
            <span className="t">
              {p.underlying} {p.strikes}
            </span>
            {p.unrealized_pnl != null ? (
              <Pnl v={p.unrealized_pnl} />
            ) : (
              <span className="muted small">no mark yet</span>
            )}
            {p.pct_of_credit != null && p.unrealized_pnl != null ? (
              <span className="muted small num">{Math.round(p.pct_of_credit)}% / 50% PT</span>
            ) : null}
          </button>
        ))}
        <button className="pos-chip" onClick={() => navigate("positions")} style={{ justifyContent: "center" }}>
          <span className="muted small">all positions →</span>
        </button>
      </div>
    </div>
  );
}

export function Dashboard() {
  const { fmtPnl } = useDisplay();
  const { accountEpoch } = useAuth();
  const { qs, active } = useFilters();
  const [motd, setMotd] = useState<Motd | null>(null);
  const [score, setScore] = useState<Score | null>(null);
  const [cal, setCal] = useState<CalendarDay[] | null>(null);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [equity, setEquity] = useState<EquityPoint[] | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [signals, setSignals] = useState<SignalsFeed | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let dead = false;
    setError("");
    (async () => {
      try {
        const [m, s, c, a, e, p, sig] = await Promise.all([
          api<Motd>("/journal/motd").catch(() => null),
          api<Score>("/journal/score" + qs),
          api<{ days: CalendarDay[] }>("/journal/calendar" + qs),
          api<Analytics>("/journal/analytics" + qs),
          api<{ curve: EquityPoint[] }>("/journal/equity"),
          api<{ positions: Position[] }>("/journal/positions").catch(() => ({ positions: [] })),
          api<SignalsFeed>("/journal/signals?days=1").catch(() => null),
        ]);
        if (dead) return;
        setMotd(m);
        setScore(s);
        setCal(c.days || []);
        setAnalytics(a);
        setEquity(e.curve || []);
        setPositions(p.positions || []);
        setSignals(sig);
      } catch (err) {
        if (!dead) setError((err as Error).message);
      }
    })();
    return () => {
      dead = true;
    };
  }, [qs, accountEpoch]);

  if (error) return <Empty>{error}</Empty>;
  if (!score || !cal || !analytics || !equity) return <Skeleton h={140} n={3} />;

  const o = analytics.overall;
  const r = analytics.r_multiples;
  const d = analytics.discipline;
  const emptyBook = o.count === 0 && cal.length === 0 && equity.length === 0;

  return (
    <div>
      {motd ? <MotdBanner m={motd} /> : null}
      <JournalQueue />
      <SignalsChipRow feed={signals} />
      <PositionsStrip positions={positions} />

      {emptyBook && !active ? (
        <Empty
          hint={
            <>
              Head to <a href="#/settings">Settings</a> to bind your Tastytrade account, then Sync — trades
              build themselves from your fills; you add the judgment.
            </>
          }
        >
          The journal is empty — nothing measured yet.
        </Empty>
      ) : null}

      <div className="tiles">
        <StatTile label="Total P&L" value={<Pnl v={o.total_pnl} />} sub={`${o.count} closed`} />
        <StatTile
          label="Win rate"
          value={pct(o.win_rate)}
          sub={`${o.wins}W / ${o.losses}L — wins are not the goal`}
        />
        <StatTile label="Profit factor" value={o.profit_factor == null ? "—" : num(o.profit_factor)} />
        <StatTile
          label="Expectancy"
          value={<span className={"pnl " + pnlClass(o.expectancy)}>{fmtPnl(o.expectancy)}</span>}
          sub="per trade"
        />
        <StatTile
          label="Avg R"
          value={r.avg_r == null ? "—" : num(r.avg_r) + "R"}
          sub={`${pct(r.coverage_pct, 0)} of trades have planned risk`}
        />
        <StatTile
          label="Max drawdown"
          value={
            score.max_drawdown != null ? (
              <span className="pnl loss">{fmtPnl(-Math.abs(score.max_drawdown))}</span>
            ) : (
              "—"
            )
          }
        />
        <StatTile
          label="Discipline streak"
          value={d.current_streak}
          sub={`longest ${d.longest_streak} — this is the number that matters`}
        />
        <StatTile
          label="Journal streak"
          value={d.journal_streak}
          sub="closed trades with a complete record"
        />
      </div>

      <div className="grid-2">
        <div className="card">
          <h3>Score</h3>
          <ScoreCard score={score} />
        </div>
        <div className="card">
          <h3>P&L calendar</h3>
          {cal.length ? (
            <CalendarHeatmap days={cal} onDayClick={(date) => navigate("logbook", date)} />
          ) : (
            <Empty hint="Days with closed trades will paint themselves here.">Nothing closed in this slice.</Empty>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <h3>Equity vs SPY</h3>
        <Suspense fallback={<Skeleton h={240} />}>
          <EquityChart curve={equity} />
        </Suspense>
      </div>
    </div>
  );
}
