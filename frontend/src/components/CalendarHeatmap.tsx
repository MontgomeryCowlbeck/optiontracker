/* P&L month calendar — diverging win/loss fill scaled by |P&L|, dot markers
   for note / AI review, a weekly rail (net P&L + traded days), and a monthly
   total in the header. Cell values follow the global display mode; R mode
   uses the server-computed per-day summed R. Click a day → the Logbook. */
import { useMemo, useState } from "react";
import type { CalendarDay } from "../api/types";
import { money } from "../lib/format";
import { useDisplay } from "../state/store";

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function monthKey(d: string): string {
  return d.slice(0, 7);
}

export function CalendarHeatmap({
  days,
  onDayClick,
}: {
  days: CalendarDay[];
  onDayClick: (date: string) => void;
}) {
  const { mode, fmtPnl } = useDisplay();
  const byDate = useMemo(() => new Map(days.map((d) => [d.date, d])), [days]);
  const months = useMemo(() => {
    const s = new Set(days.map((d) => monthKey(d.date)));
    const now = new Date();
    s.add(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`);
    return [...s].sort();
  }, [days]);
  const [mi, setMi] = useState(months.length - 1);
  const month = months[Math.max(0, Math.min(mi, months.length - 1))];

  // one scale across the whole filtered dataset so paging months stays comparable
  const maxAbs = useMemo(
    () => days.reduce((m, d) => Math.max(m, Math.abs(d.pnl || 0)), 0),
    [days],
  );

  const [y, m] = month.split("-").map(Number);
  const first = new Date(y, m - 1, 1);
  const cells: (string | null)[] = [];
  for (let i = 0; i < first.getDay(); i++) cells.push(null);
  const daysInMonth = new Date(y, m, 0).getDate();
  for (let d = 1; d <= daysInMonth; d++) {
    cells.push(`${month}-${String(d).padStart(2, "0")}`);
  }
  while (cells.length % 7 !== 0) cells.push(null);
  const weeks: (string | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));

  const fill = (pnl: number): string => {
    if (!maxAbs || pnl === 0) return "";
    const a = 0.16 + 0.55 * Math.sqrt(Math.abs(pnl) / maxAbs);
    return pnl > 0 ? `rgba(12, 163, 12, ${a.toFixed(3)})` : `rgba(208, 59, 59, ${a.toFixed(3)})`;
  };

  /** cell/rail value under the active display mode (R uses the day's summed R) */
  const dayValue = (pnl: number, r: number | null | undefined): string =>
    mode === "R" ? (r == null ? "·" : `${r > 0 ? "+" : ""}${r.toFixed(1)}R`) : fmtPnl(pnl);

  const monthDays = cells.filter((c): c is string => !!c).map((c) => byDate.get(c)).filter(Boolean) as CalendarDay[];
  const monthPnl = monthDays.reduce((s2, d) => s2 + (d.pnl || 0), 0);
  const monthRs = monthDays.filter((d) => d.r != null);
  const monthR = monthRs.reduce((s2, d) => s2 + (d.r || 0), 0);

  const label = new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });

  return (
    <div>
      <div className="cal-head">
        <button className="btn btn-ghost btn-sm" disabled={mi <= 0} onClick={() => setMi(mi - 1)}>
          ‹
        </button>
        <span>
          <strong style={{ color: "var(--ink)" }}>{label}</strong>
          {monthDays.length ? (
            <span className={"cal-month-total num " + (monthPnl > 0 ? "win" : monthPnl < 0 ? "loss" : "")}>
              {mode === "R" && monthRs.length
                ? `${monthR > 0 ? "+" : ""}${monthR.toFixed(1)}R`
                : fmtPnl(monthPnl)}
              <span className="muted"> · {monthDays.length} day{monthDays.length === 1 ? "" : "s"}</span>
            </span>
          ) : null}
        </span>
        <button
          className="btn btn-ghost btn-sm"
          disabled={mi >= months.length - 1}
          onClick={() => setMi(mi + 1)}
        >
          ›
        </button>
      </div>
      <div className="cal-grid rail">
        {DOW.map((d) => (
          <div key={d} className="cal-dow">
            {d}
          </div>
        ))}
        <div className="cal-dow cal-dow-wk">Wk</div>
        {weeks.map((week, wi) => {
          const wdays = week.map((c) => (c ? byDate.get(c) : undefined)).filter(Boolean) as CalendarDay[];
          const wPnl = wdays.reduce((s2, d) => s2 + (d.pnl || 0), 0);
          const wRs = wdays.filter((d) => d.r != null);
          const wR = wRs.reduce((s2, d) => s2 + (d.r || 0), 0);
          return [
            ...week.map((date, i) => {
              if (!date) return <div key={`x${wi}-${i}`} className="cal-cell out" />;
              const d = byDate.get(date);
              const title = d
                ? `${date}: ${money(d.pnl)}${d.r != null ? ` (${d.r > 0 ? "+" : ""}${d.r}R)` : ""} · ${d.trades} trade${d.trades === 1 ? "" : "s"} · ${d.wins} win${d.wins === 1 ? "" : "s"}`
                : date;
              return (
                <button
                  key={date}
                  className={"cal-cell" + (d ? " has-data" : "")}
                  style={d ? { background: fill(d.pnl) || undefined } : undefined}
                  title={title}
                  onClick={d ? () => onDayClick(date) : undefined}
                >
                  <span className="d">{Number(date.slice(8))}</span>
                  {d ? (
                    <span className="dots">
                      {d.has_note ? <i className="dot note" /> : null}
                      {d.has_review ? <i className="dot review" /> : null}
                    </span>
                  ) : null}
                  {d ? <span className="p">{dayValue(d.pnl, d.r)}</span> : null}
                </button>
              );
            }),
            <div
              key={`w${wi}`}
              className={"cal-week num " + (wPnl > 0 ? "win" : wPnl < 0 ? "loss" : "")}
              title={wdays.length ? `week: ${money(wPnl)} over ${wdays.length} traded day${wdays.length === 1 ? "" : "s"}` : "no closes this week"}
            >
              {wdays.length ? (
                <>
                  <span className="wp">{mode === "R" && wRs.length ? `${wR > 0 ? "+" : ""}${wR.toFixed(1)}R` : fmtPnl(wPnl)}</span>
                  <span className="wd">{wdays.length}d</span>
                </>
              ) : (
                <span className="muted">—</span>
              )}
            </div>,
          ];
        })}
      </div>
      <div className="cal-legend">
        <span>fill depth = day P&L size (green win / red loss, sign in the cell)</span>
        <span>
          <i className="dot note" />
          note
        </span>
        <span>
          <i className="dot review" />
          AI review
        </span>
      </div>
    </div>
  );
}
