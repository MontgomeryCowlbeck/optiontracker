/* Equity curve vs SPY — both indexed to 100 at the first shared snapshot,
   so one axis compares them honestly (never a dual-axis chart). */
import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { EquityPoint } from "../api/types";
import { fmtDayShort, money } from "../lib/format";
import { Empty } from "./ui";

const BLUE = "#3987e5";
const ORANGE = "#d95926";

export function EquityChart({ curve }: { curve: EquityPoint[] }) {
  const data = useMemo(() => {
    const pts = curve.filter((p) => p.portfolio_value != null);
    if (!pts.length) return [];
    const base = pts[0];
    const basePv = Number(base.portfolio_value);
    const firstSpy = pts.find((p) => p.spy_price != null);
    const baseSpy = firstSpy ? Number(firstSpy.spy_price) : null;
    return pts.map((p) => ({
      date: p.snapshot_date,
      portfolio: basePv ? (Number(p.portfolio_value) / basePv) * 100 : null,
      spy: baseSpy && p.spy_price != null ? (Number(p.spy_price) / baseSpy) * 100 : null,
      raw: p.portfolio_value,
    }));
  }, [curve]);

  if (!data.length) {
    return (
      <Empty hint="Snapshots are recorded daily after the close once a Tastytrade account is bound.">
        No equity snapshots yet.
      </Empty>
    );
  }

  return (
    <div>
      <div style={{ width: "100%", height: "clamp(180px, 50vw, 240px)" }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="#2c2c2a" strokeWidth={1} vertical={false} />
            <XAxis
              dataKey="date"
              tickFormatter={fmtDayShort}
              tick={{ fill: "#9a988e", fontSize: 11 }}
              axisLine={{ stroke: "#383835" }}
              tickLine={false}
              minTickGap={40}
            />
            <YAxis
              tick={{ fill: "#9a988e", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={34}
              domain={["auto", "auto"]}
              tickFormatter={(v: number) => v.toFixed(0)}
            />
            <Tooltip
              contentStyle={{
                background: "#232321",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: 8,
                fontSize: 12,
                color: "#ffffff",
              }}
              labelFormatter={(l) => String(l)}
              formatter={(value: number | string, name: string, entry) => {
                const v = Number(value);
                if (name === "Portfolio") {
                  const raw = (entry?.payload as { raw?: number })?.raw;
                  return [`${v.toFixed(1)} (${money(raw, { sign: false })})`, name];
                }
                return [v.toFixed(1), name];
              }}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: "#c3c2b7" }} iconType="plainline" />
            <Line
              type="monotone"
              dataKey="portfolio"
              name="Portfolio"
              stroke={BLUE}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, stroke: "#1a1a19", strokeWidth: 2 }}
              connectNulls
            />
            <Line
              type="monotone"
              dataKey="spy"
              name="SPY"
              stroke={ORANGE}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, stroke: "#1a1a19", strokeWidth: 2 }}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="chart-note">Indexed to 100 at the range start · unfiltered — full account history.</p>
    </div>
  );
}
