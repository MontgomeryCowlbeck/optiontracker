"""Morning digest — the pre-market briefing that assembles every monitoring
surface into one read: market pulse, Strategy #1, watchtower cards, the open
book's attention items, the look-ahead calendar, research condition flags,
plans on deck, and journal debt.

ASSEMBLY ONLY, and deliberately fast: DB rows + the signal/pulse JSON files the
quant crons already write — no live yfinance in this path (the vol-watch and
screener sections on the Morning page load their live numbers separately).
Every number comes from the canonical service that owns it; nothing is
re-derived here. Earnings dates are the NIGHTLY-STORED ones (research and vol
snapshots), which is the honest semantic for a pre-market read anyway."""
from datetime import date, timedelta

from app.database import fetch_all
from app.services.calculations import is_short_premium
from app.services.journal_analytics import journal_debt
from app.services.journal_trades import fetch_trades
from app.services.positions import open_positions
from app.services.signals import signals_feed

# Attention thresholds — the same lines the Positions pills draw.
EXPIRING_DTE = 7          # red zone of the management clock
CLOCK_DTE = 21            # inside the 21-DTE management window
PT_PCT = 50.0             # the validated 50% profit target
CALENDAR_HORIZON_DAYS = 14
WATCHTOWER_DAYS = 3       # how far back the digest shows alert cards
MAX_CARDS = 8


def _dte(expiration, today: date):
    if not expiration:
        return None
    try:
        return (date.fromisoformat(str(expiration)[:10]) - today).days
    except ValueError:
        return None


def attention_items(positions: list[dict], earnings_by_symbol: dict,
                    today: date) -> list[dict]:
    """What needs the trader's eyes today, derived from the open book. Pure.

    Reasons, in display-priority order: threatened (short strike ATM/ITM),
    expiring (DTE <= 7), pt_hit (>= 50% of credit captured — the validated
    close), earnings before expiry, clock (inside the 21-DTE window)."""
    items = []
    for p in positions:
        dte = _dte(p.get("expiration"), today)
        reasons = []
        if p.get("moneyness") in ("ITM", "ATM"):
            reasons.append("threatened")
        if dte is not None and dte <= EXPIRING_DTE:
            reasons.append("expiring")
        if (is_short_premium(p.get("direction") or "")
                and (p.get("pct_of_credit") or 0) >= PT_PCT):
            reasons.append("pt_hit")
        earn = earnings_by_symbol.get(p.get("underlying"))
        if (earn and p.get("expiration")
                and today.isoformat() <= earn <= str(p["expiration"])[:10]):
            reasons.append("earnings")
        if dte is not None and EXPIRING_DTE < dte <= CLOCK_DTE:
            reasons.append("clock")
        if reasons:
            items.append({
                "trade_id": p.get("id"), "underlying": p.get("underlying"),
                "direction": p.get("direction"), "strikes": p.get("strikes"),
                "expiration": p.get("expiration"), "dte": dte,
                "moneyness": p.get("moneyness"),
                "pct_of_credit": p.get("pct_of_credit"),
                "unrealized_pnl": p.get("unrealized_pnl"),
                "day_pnl": p.get("day_pnl"),
                "earnings": earn if "earnings" in reasons else None,
                "reasons": reasons,
            })
    # Threatened first, then soonest expiry.
    items.sort(key=lambda i: (0 if "threatened" in i["reasons"] else 1,
                              i["dte"] if i["dte"] is not None else 999))
    return items


async def _stored_earnings(account_id: int, today: date) -> dict:
    """{symbol: next earnings date} from the freshest nightly snapshot rows
    (research + vol watch). Stored-only on purpose — no live fetch here."""
    out: dict = {}
    for table in ("research_snapshots", "vol_snapshots"):
        rows = await fetch_all(
            f"""SELECT symbol, earnings FROM {table} s
                WHERE account_id = ? AND earnings IS NOT NULL
                  AND date = (SELECT MAX(date) FROM {table}
                              WHERE account_id = s.account_id AND symbol = s.symbol)""",
            (account_id,),
        )
        for r in rows:
            day = str(r["earnings"])[:10]
            if day >= today.isoformat() and (r["symbol"] not in out or day < out[r["symbol"]]):
                out[r["symbol"]] = day
    return out


async def assemble_digest(account_id: int) -> dict:
    today = date.today()
    horizon = (today + timedelta(days=CALENDAR_HORIZON_DAYS)).isoformat()

    feed = await signals_feed(account_id, days=WATCHTOWER_DAYS)
    book = await open_positions(account_id)
    positions = book["positions"]
    earnings_by_symbol = await _stored_earnings(account_id, today)

    expirations: dict[str, list] = {}
    for p in positions:
        exp = str(p.get("expiration") or "")[:10]
        if exp and exp <= horizon:
            expirations.setdefault(exp, []).append({
                "id": p.get("id"), "underlying": p.get("underlying"),
                "direction": p.get("direction"), "strikes": p.get("strikes"),
            })
    held = {p.get("underlying") for p in positions}
    earnings: dict[str, list] = {}
    for sym, day in sorted(earnings_by_symbol.items()):
        if day <= horizon:
            earnings.setdefault(day, []).append({"symbol": sym, "held": sym in held})

    flags = await fetch_all(
        """SELECT symbol, date, flags FROM research_snapshots s
           WHERE account_id = ? AND flags IS NOT NULL AND flags != '[]'
             AND date = (SELECT MAX(date) FROM research_snapshots
                         WHERE account_id = s.account_id AND symbol = s.symbol)
           ORDER BY symbol""",
        (account_id,),
    )
    import json as _json
    research_flags = [{"symbol": r["symbol"], "date": r["date"],
                       "flags": _json.loads(r["flags"] or "[]")} for r in flags]

    from app.services.intents import expire_stale_intents, open_intents
    await expire_stale_intents(account_id)
    intents = await open_intents(account_id)

    debt = journal_debt(await fetch_trades(account_id))

    return {
        "date": today.isoformat(),
        "market": feed["pulse"],          # last EOD pulse, carries its own date
        "strategy1": feed["strategy1"],   # latest signal file, carries its date
        "watchtower": feed["watchtower"][:MAX_CARDS],
        "book": book["summary"],
        "attention": attention_items(positions, earnings_by_symbol, today),
        "calendar": {"horizon_days": CALENDAR_HORIZON_DAYS,
                     "expirations": expirations, "earnings": earnings,
                     "dte_window": {
                         "start": (today + timedelta(days=30)).isoformat(),
                         "end": (today + timedelta(days=45)).isoformat()}},
        "research_flags": research_flags,
        "intents": intents,
        "debt": {"count": debt["count"], "items": debt["items"][:5]},
    }
