/* Forward calendar — what's COMING, not what happened: open-position
   expirations by date, next earnings for held + watchlist names, and the
   30-45 DTE entry window shaded. Inspired by the old optiontracker's
   pool-aware expiration calendar. */
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ForwardCalendar as FCal } from "../api/types";
import { fmtDayShort } from "../lib/format";
import { useAuth } from "../state/store";
import { Modal, Skeleton } from "./ui";

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function ForwardCalendar() {
  const { accountEpoch } = useAuth();
  const [data, setData] = useState<FCal | null>(null);
  const [error, setError] = useState("");
  const [monthOffset, setMonthOffset] = useState(0);
  const [open, setOpen] = useState(true);
  const [daySheet, setDaySheet] = useState<string | null>(null); // chips are too small to carry the story on touch

  useEffect(() => {
    setData(null);
    api<FCal>("/journal/calendar/forward")
      .then(setData)
      .catch((e) => setError((e as Error).message));
  }, [accountEpoch]);

  const grid = useMemo(() => {
    if (!data) return null;
    const today = new Date(data.today + "T00:00:00");
    const first = new Date(today.getFullYear(), today.getMonth() + monthOffset, 1);
    const label = first.toLocaleDateString("en-US", { month: "long", year: "numeric" });
    const start = new Date(first);
    start.setDate(1 - first.getDay()); // back to Sunday
    const weeks: Date[][] = [];
    const cur = new Date(start);
    while (weeks.length < 6 && (cur.getMonth() === first.getMonth() || weeks.length === 0 || cur <= new Date(first.getFullYear(), first.getMonth() + 1, 0))) {
      const week: Date[] = [];
      for (let i = 0; i < 7; i++) {
        week.push(new Date(cur));
        cur.setDate(cur.getDate() + 1);
      }
      weeks.push(week);
      if (cur.getMonth() !== first.getMonth() && cur > first) break;
    }
    return { weeks, label, month: first.getMonth() };
  }, [data, monthOffset]);

  if (error) return null; // the calendar is a bonus panel — never block Positions on it
  if (!data || !grid) return <Skeleton h={60} />;

  const hasAnything = Object.keys(data.expirations).length + Object.keys(data.earnings).length > 0;

  return (
    <div className="fcal">
      <div className="fcal-head">
        <h3 className="section-title" style={{ margin: 0 }}>
          Forward calendar{" "}
          <span className="muted small" style={{ fontWeight: 400 }}>
            — expirations · earnings · 30-45 DTE entry window
          </span>
        </h3>
        <div className="row">
          {open && (
            <>
              <button className="btn btn-ghost btn-sm" onClick={() => setMonthOffset(monthOffset - 1)} disabled={monthOffset <= 0}>
                ‹
              </button>
              <span className="muted small" style={{ minWidth: 110, textAlign: "center" }}>{grid.label}</span>
              <button className="btn btn-ghost btn-sm" onClick={() => setMonthOffset(monthOffset + 1)} disabled={monthOffset >= 2}>
                ›
              </button>
            </>
          )}
          <button className="btn btn-ghost btn-sm" onClick={() => setOpen(!open)}>
            {open ? "Hide" : "Show"}
          </button>
        </div>
      </div>
      {open && !hasAnything ? (
        <p className="muted small">Nothing ahead — no open expirations, no known earnings.</p>
      ) : null}
      {open && hasAnything ? (
        <>
          <div className="fcal-grid">
            {DOW.map((d) => (
              <div key={d} className="fcal-dow">
                {d}
              </div>
            ))}
            {grid.weeks.flat().map((d) => {
              const day = iso(d);
              const inMonth = d.getMonth() === grid.month;
              const isToday = day === data.today;
              const inWindow = day >= data.dte_window.start && day <= data.dte_window.end;
              const exps = data.expirations[day] || [];
              const earns = data.earnings[day] || [];
              const bySym = new Map<string, number>();
              for (const e of exps) bySym.set(e.underlying || "?", (bySym.get(e.underlying || "?") || 0) + 1);
              const busyDay = exps.length + earns.length > 0;
              const cls =
                "fcal-cell" +
                (inMonth ? "" : " out") +
                (isToday ? " today" : "") +
                (inWindow ? " window" : "");
              if (!busyDay)
                return (
                  <div key={day} className={cls}>
                    <span className="fcal-daynum num">{d.getDate()}</span>
                  </div>
                );
              return (
                <button key={day} className={cls} onClick={() => setDaySheet(day)}>
                  <span className="fcal-daynum num">{d.getDate()}</span>
                  {[...bySym.entries()].map(([sym, n]) => (
                    <span key={sym} className="fcal-chip exp" title={`${n} position${n > 1 ? "s" : ""} expiring`}>
                      {sym}
                      {n > 1 ? `×${n}` : ""}
                    </span>
                  ))}
                  {earns.map((e) => (
                    <span
                      key={e.symbol}
                      className={"fcal-chip earn" + (e.held ? " held" : "")}
                      title={e.held ? "earnings — you hold this name" : "earnings (watchlist)"}
                    >
                      ⚡{e.symbol}
                    </span>
                  ))}
                </button>
              );
            })}
          </div>
          <p className="muted small fcal-legend">
            <span className="fcal-chip exp">EXP</span> position expires · <span className="fcal-chip earn">⚡</span>{" "}
            earnings (bright = a name you hold) · shaded band = 30-45 DTE entry window · tap a day for detail
          </p>
        </>
      ) : null}
      {daySheet ? (
        <Modal dismissable title={fmtDayShort(daySheet)} onClose={() => setDaySheet(null)}>
          <div className="stack" style={{ gap: 8 }}>
            {(data.expirations[daySheet] || []).length > 0 ? (
              <div>
                <h3 style={{ marginBottom: 6 }}>Expirations</h3>
                {(data.expirations[daySheet] || []).map((e, i) => (
                  <p key={i} className="small" style={{ margin: "4px 0" }}>
                    <b style={{ color: "var(--ink)" }}>{e.underlying || "?"}</b>
                    {e.strikes ? <span className="num muted"> {e.strikes}</span> : null}
                  </p>
                ))}
              </div>
            ) : null}
            {(data.earnings[daySheet] || []).length > 0 ? (
              <div>
                <h3 style={{ marginBottom: 6 }}>Earnings</h3>
                {(data.earnings[daySheet] || []).map((e) => (
                  <p key={e.symbol} className="small" style={{ margin: "4px 0" }}>
                    ⚡ <b style={{ color: "var(--ink)" }}>{e.symbol}</b>
                    <span className="muted"> — {e.held ? "you hold this name" : "watchlist"}</span>
                  </p>
                ))}
              </div>
            ) : null}
            {daySheet >= data.dte_window.start && daySheet <= data.dte_window.end ? (
              <p className="muted small" style={{ margin: 0 }}>
                Inside the 30-45 DTE new-entry window.
              </p>
            ) : null}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
