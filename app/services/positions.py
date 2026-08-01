"""Open-book valuation — the one place that assembles open trades with their
latest polled marks/greeks. Used by the /positions endpoint and by the consult
agent (so "review my open position" sees the same numbers the Positions tab
shows). All math is calculations.py; this module only assembles."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.database import fetch_all
from app.services.calculations import (
    assignment_capital, day_change_pnl, days_open, defined_risk_max_loss,
    net_contracts_by_symbol, position_greeks, positions_summary,
    short_premium_profile, unrealized_journal_pnl,
)
from app.services.journal_trades import fetch_trades

_ET = ZoneInfo("America/New_York")


def _utc(ts):
    """captured_at is SQLite CURRENT_TIMESTAMP: UTC but naive — say so, or
    clients render staleness shifted by the local offset."""
    if not ts:
        return None
    t = str(ts).replace(" ", "T")
    return t if (t.endswith("Z") or "+" in t) else t + "Z"


async def open_positions(account_id: int, underlying: str | None = None) -> dict:
    """Open trades valued at the latest captured marks: per-leg net position +
    mark/delta/theta/IV, unrealized P&L, % of entry credit kept, net greeks,
    days held, defined-risk max loss, and a book-level summary."""
    trades = await fetch_trades(account_id, status="open", underlying=underlying)
    latest = await fetch_all(
        """SELECT g.option_symbol, g.mark, g.delta, g.iv, g.theta, g.captured_at
           FROM greek_snapshots g
           JOIN (SELECT option_symbol, MAX(id) AS mid FROM greek_snapshots
                 WHERE account_id = ? GROUP BY option_symbol) last
             ON last.mid = g.id""",
        (account_id,),
    )
    by_sym = {r["option_symbol"]: r for r in latest}

    # Day-change references: each leg's last mark before today (ET). Legs first
    # filled today fall back to today's fill price inside day_change_pnl.
    et_now = datetime.now(_ET)
    today_iso = et_now.date().isoformat()
    day_start_utc = (et_now.replace(hour=0, minute=0, second=0, microsecond=0)
                     .astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    ref_rows = await fetch_all(
        """SELECT g.option_symbol, g.mark FROM greek_snapshots g
           JOIN (SELECT option_symbol, MAX(id) AS mid FROM greek_snapshots
                 WHERE account_id = ? AND captured_at < ? GROUP BY option_symbol) last
             ON last.mid = g.id""",
        (account_id, day_start_utc),
    )
    marks_ref = {r["option_symbol"]: r["mark"] for r in ref_rows}

    spot_rows = await fetch_all(
        "SELECT symbol, price FROM underlying_marks WHERE account_id = ?",
        (account_id,),
    )
    spot_by_underlying = {r["symbol"]: r["price"] for r in spot_rows}

    out = []
    for t in trades:
        fills = await fetch_all(
            "SELECT * FROM tt_fills WHERE journal_trade_id = ? ORDER BY executed_at, id",
            (t["id"],),
        )
        marks = {s: (by_sym.get(s) or {}).get("mark") for s in
                 net_contracts_by_symbol(fills)}
        val = unrealized_journal_pnl(fills, marks)
        legs = [
            {"option_symbol": s, "net": q,
             "mark": (by_sym.get(s) or {}).get("mark"),
             "delta": (by_sym.get(s) or {}).get("delta"),
             "iv": (by_sym.get(s) or {}).get("iv"),
             "theta": (by_sym.get(s) or {}).get("theta"),
             "as_of": _utc((by_sym.get(s) or {}).get("captured_at"))}
            for s, q in net_contracts_by_symbol(fills).items() if q != 0
        ]
        pct_of_credit = None
        if val["unrealized_pnl"] is not None and (t.get("entry_premium") or 0) > 0:
            pct_of_credit = round(val["unrealized_pnl"] / t["entry_premium"] * 100, 1)
        max_loss = defined_risk_max_loss(fills, t.get("direction") or "",
                                         t.get("entry_premium") or 0)
        return_on_risk = (round((t.get("entry_premium") or 0) / max_loss * 100, 1)
                          if max_loss and max_loss > 0 else None)
        spot = spot_by_underlying.get(t.get("underlying"))
        out.append({**t, **val, "legs": legs, "pct_of_credit": pct_of_credit,
                    **position_greeks(fills, by_sym),
                    "days_open": days_open(t.get("entry_at")),
                    "max_loss": max_loss, "return_on_risk": return_on_risk,
                    "spot": spot,
                    **short_premium_profile(fills, t.get("direction") or "",
                                            t.get("entry_premium") or 0,
                                            spot=spot, greeks=by_sym),
                    "assignment_capital": assignment_capital(fills),
                    "day_pnl": day_change_pnl(fills, marks, marks_ref, today_iso)})
    return {"positions": out, "summary": positions_summary(out)}
