import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { Analytics as AnalyticsT, OverallStats, RowStats } from "../api/types";
import { SortableTable, type Column } from "../components/SortableTable";
import { Empty, Pnl, Skeleton, StatTile } from "../components/ui";
import { num, pct, strategyLabel, tagBadgeStyle } from "../lib/format";
import { useAuth, useDisplay, useFilters } from "../state/store";

/** Stat-table columns; fmtPnl comes from the display-mode context so tables
 *  follow the global $ / R / % / privacy switch. */
function makeStatColumns(fmtPnl: (v: number | null | undefined) => string) {
  return function statColumns<T extends OverallStats>(nameCol: Column<T>): Column<T>[] {
    // priority 3 = hidden on phones behind the "all columns" toggle
    return [
      { ...nameCol, priority: 1 },
      { key: "count", label: "n", numeric: true, priority: 3, render: (r) => r.count },
      { key: "total_pnl", label: "P&L", numeric: true, priority: 1, render: (r) => <Pnl v={r.total_pnl} /> },
      { key: "win_rate", label: "Win%", numeric: true, priority: 2, render: (r) => pct(r.win_rate) },
      { key: "avg_pnl", label: "Avg", numeric: true, priority: 3, render: (r) => fmtPnl(r.avg_pnl) },
      {
        key: "payoff_ratio",
        label: "Payoff",
        numeric: true,
        priority: 3,
        render: (r) => (r.payoff_ratio == null ? "—" : num(r.payoff_ratio)),
      },
      {
        key: "profit_factor",
        label: "PF",
        numeric: true,
        priority: 2,
        render: (r) => (r.profit_factor == null ? "—" : num(r.profit_factor)),
      },
      { key: "expectancy", label: "Expect.", numeric: true, priority: 3, render: (r) => fmtPnl(r.expectancy) },
    ];
  };
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <>
      <h3 className="section-title">{title}</h3>
      <div className="card tight">{children}</div>
    </>
  );
}

export function Analytics() {
  const { qs } = useFilters();
  const { accountEpoch } = useAuth();
  const { fmtPnl } = useDisplay();
  const [a, setA] = useState<AnalyticsT | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let dead = false;
    setA(null);
    api<AnalyticsT>("/journal/analytics" + qs)
      .then((r) => !dead && setA(r))
      .catch((e) => !dead && setError((e as Error).message));
    return () => {
      dead = true;
    };
  }, [qs, accountEpoch]);

  if (error) return <Empty>{error}</Empty>;
  if (!a) return <Skeleton h={120} n={4} />;

  const statColumns = makeStatColumns(fmtPnl);
  const d = a.discipline;
  const o = a.overall;
  const r = a.r_multiples;
  const svd = a.system_vs_discretionary;
  const svdRows: RowStats[] = [
    { key: "System", ...svd.system },
    { key: "Discretionary", ...svd.discretionary },
  ];

  return (
    <div>
      <div className="discipline">
        <div className="streak-n num">{d.current_streak}</div>
        <div className="streak-label">
          <b style={{ color: "var(--ink)" }}>trade discipline streak</b> — consecutive trades exited by
          their own plan, system or discretionary alike, regardless of P&L. Longest: {d.longest_streak}.
          This is the metric this desk rewards; the money is downstream of it.
        </div>
        <div className="dissonance">
          <div className="diss-item">
            <span className="n num">{d.dissonance.won_but_off_plan}</span>
            <span className="l">won off-plan</span>
          </div>
          <div className="diss-item">
            <span className="n num">{d.dissonance.lost_but_well_executed}</span>
            <span className="l">lost but well executed</span>
          </div>
        </div>
      </div>

      <div className="tiles" style={{ marginTop: 14 }}>
        <StatTile label="Closed trades" value={o.count} />
        <StatTile label="Total P&L" value={<Pnl v={o.total_pnl} />} />
        <StatTile label="Win rate" value={pct(o.win_rate)} sub={`${o.wins}W / ${o.losses}L`} />
        <StatTile
          label="Avg win / avg loss"
          value={
            <span className="num" style={{ fontSize: 15, display: "inline-flex", flexDirection: "column", lineHeight: 1.3 }}>
              <span className="pnl win">{fmtPnl(o.avg_win)}</span>
              <span className="pnl loss">{fmtPnl(o.avg_loss)}</span>
            </span>
          }
          sub={`payoff ${o.payoff_ratio == null ? "—" : num(o.payoff_ratio)}`}
        />
        <StatTile label="Expectancy" value={fmtPnl(o.expectancy)} sub="per trade" />
      </div>

      <h3 className="section-title">R-multiples</h3>
      <div className="card">
        {r.count ? (
          <div className="tiles" style={{ margin: 0 }}>
            <StatTile label="Avg R" value={r.avg_r == null ? "—" : num(r.avg_r) + "R"} />
            <StatTile label="Total R" value={r.total_r == null ? "—" : num(r.total_r) + "R"} />
            <StatTile label="Best / worst" value={`${num(r.best_r)} / ${num(r.worst_r)}`} />
            <StatTile
              label="Coverage"
              value={pct(r.coverage_pct, 0)}
              sub={r.coverage_pct < 80 ? "set planned risk at entry — R is unmeasurable after the fact" : "of closed trades"}
            />
          </div>
        ) : (
          <Empty hint="Set “Planned risk $” when you journal a trade — every R-stat flows from it.">
            No trades carry a planned risk yet.
          </Empty>
        )}
      </div>

      <Section title="By edge">
        <SortableTable
          rows={a.by_edge || []}
          defaultSort="total_pnl"
          columns={statColumns<RowStats>({
            key: "edge_name",
            label: "Edge",
            render: (r2) => r2.edge_name ?? "—",
          })}
        />
      </Section>

      <Section title="By tag">
        <SortableTable
          rows={a.by_tag}
          defaultSort="total_pnl"
          columns={statColumns<RowStats>({
            key: "tag",
            label: "Tag",
            render: (r2) => (
              <span className="badge" style={tagBadgeStyle(r2.color)}>
                {r2.tag}
              </span>
            ),
            value: (r2) => r2.tag,
          })}
        />
      </Section>

      <Section title="By mechanism">
        <SortableTable
          rows={a.by_mechanism || []}
          defaultSort="total_pnl"
          columns={statColumns<RowStats>({
            key: "mechanism_name",
            label: "Mechanism",
            render: (r2) =>
              r2.mechanism_name?.includes("_")
                ? strategyLabel(r2.mechanism_name)
                : r2.mechanism_name ?? "—",
            value: (r2) => r2.mechanism_name,
          })}
        />
      </Section>

      <Section title="System vs discretionary">
        <SortableTable
          rows={svdRows}
          columns={statColumns<RowStats>({ key: "key", label: "", render: (r2) => r2.key, sortable: false })}
        />
      </Section>

      <Section title="By instrument">
        <SortableTable
          rows={a.by_instrument}
          defaultSort="total_pnl"
          columns={statColumns<RowStats>({ key: "key", label: "Underlying", render: (r2) => r2.key })}
        />
      </Section>

      <Section title="By regime">
        <SortableTable
          rows={a.by_regime}
          defaultSort="total_pnl"
          columns={statColumns<RowStats>({ key: "key", label: "Regime", render: (r2) => r2.key })}
        />
      </Section>
    </div>
  );
}
