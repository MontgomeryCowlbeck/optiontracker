"""Research section — a personal watchlist for names OUTSIDE Strategy #1 and
the watchtower universe (MSFT-style "stocks I'm following"), with:

- a thesis + assignment stance per name (feeds the consult agent),
- a nightly DD snapshot per name (price/RSI/vol/IV/nearest-support/earnings),
  giving instant loads, trend history, and condition FLAGS shown in-app
  (no Discord pings by design — Monty said push isn't needed here),
- notes and saved AI output (briefs + consult recommendations).

All numbers come from the DD engine (services/dd.py -> watchtower machinery).
Flag thresholds mirror the desk's own framework, computed here, never by an LLM.
"""
import json
import logging
from datetime import date, datetime, timedelta

from app.database import fetch_all, fetch_one, get_db

logger = logging.getLogger(__name__)

# Condition flags. Monty asked for near_support + earnings_soon; the other two
# are computed anyway (cheap, same data) and rendered as secondary badges.
NEAR_SUPPORT_PCT = 3.0     # price within 3% above (or inside) a support zone
EARNINGS_SOON_DAYS = 7
RSI_WASHOUT = 35.0
IV_RICH = 1.30             # the fat-gap threshold, per name


def compute_flags(snap: dict) -> list[str]:
    """Active condition flags for one snapshot row (pure; testable)."""
    flags = []
    d = snap.get("support_dist_pct")
    # distance_pct is negative below price; "approaching" = zone top within
    # NEAR_SUPPORT_PCT below spot (or price already inside the zone).
    if d is not None and -NEAR_SUPPORT_PCT <= d <= 2.0:
        flags.append("near_support")
    earn = snap.get("earnings")
    if earn:
        try:
            days = (date.fromisoformat(str(earn)[:10]) - date.today()).days
            if 0 <= days <= EARNINGS_SOON_DAYS:
                flags.append("earnings_soon")
        except ValueError:
            pass
    rsi = snap.get("rsi14")
    if rsi is not None and rsi <= RSI_WASHOUT:
        flags.append("rsi_washout")
    ivrv = snap.get("iv_rv")
    if ivrv is not None and ivrv >= IV_RICH:
        flags.append("iv_rich")
    return flags


def snapshot_from_dd(dd: dict) -> dict:
    """Compact snapshot row from a full DD payload (pure; testable)."""
    s = dd["stats"]
    sup = dd["supports"][0] if dd.get("supports") else None
    snap = {
        "price": dd.get("price"),
        "rsi14": s.get("rsi14"),
        "rv20": s.get("rv20"),
        "atm_iv": s.get("atm_iv"),
        "iv_rv": s.get("iv_rv_ratio"),
        "support_lo": sup["lo"] if sup else None,
        "support_hi": sup["hi"] if sup else None,
        "support_dist_pct": sup["distance_pct"] if sup else None,
        "support_touches": sup["touches"] if sup else None,
        "earnings": s.get("earnings"),
    }
    snap["flags"] = compute_flags(snap)
    return snap


async def refresh_symbol(account_id: int, symbol: str) -> dict:
    """Snapshot one name today (upsert on the date)."""
    from app.services.dd import fetch_dd

    dd = await fetch_dd(symbol)
    snap = snapshot_from_dd(dd)
    today = date.today().isoformat()
    async with get_db() as db:
        await db.execute(
            """INSERT INTO research_snapshots
               (account_id, symbol, date, price, rsi14, rv20, atm_iv, iv_rv,
                support_lo, support_hi, support_dist_pct, support_touches,
                earnings, flags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, symbol, date) DO UPDATE SET
                 price=excluded.price, rsi14=excluded.rsi14, rv20=excluded.rv20,
                 atm_iv=excluded.atm_iv, iv_rv=excluded.iv_rv,
                 support_lo=excluded.support_lo, support_hi=excluded.support_hi,
                 support_dist_pct=excluded.support_dist_pct,
                 support_touches=excluded.support_touches,
                 earnings=excluded.earnings, flags=excluded.flags""",
            (account_id, symbol.upper(), today, snap["price"], snap["rsi14"],
             snap["rv20"], snap["atm_iv"], snap["iv_rv"], snap["support_lo"],
             snap["support_hi"], snap["support_dist_pct"],
             snap["support_touches"], snap["earnings"],
             json.dumps(snap["flags"])),
        )
        await db.commit()
    return snap


async def refresh_all(account_id: int) -> dict:
    """Snapshot every active watchlist name; one bad ticker never kills the rest."""
    rows = await fetch_all(
        "SELECT symbol FROM research_watchlist WHERE account_id = ?", (account_id,))
    ok, failed = 0, []
    for r in rows:
        try:
            await refresh_symbol(account_id, r["symbol"])
            ok += 1
        except Exception as e:
            logger.warning("research refresh %s failed: %s", r["symbol"], e)
            failed.append(r["symbol"])
    return {"refreshed": ok, "failed": failed}


def _fmt(v, suffix="") -> str:
    return "n/a" if v is None else f"{v}{suffix}"


async def daily_update(account_id: int, symbol: str) -> dict:
    """The nightly per-name AI update: code computes every number (price
    action, volume, RSI, IV/RV, supports/resistances, regime, earnings),
    the LLM writes a short desk read — what changed, what to watch. NO
    buy/sell calls here; recommendations stay in the consult agent.
    Stored in research_briefs as kind='daily' (one per day, newest wins)."""
    from app.services import ai_review
    from app.services.dd import fetch_dd

    if not ai_review.ai_available():
        raise RuntimeError("AI unavailable on this host")
    sym = symbol.upper()
    dd = await fetch_dd(sym)
    s = dd["stats"]
    row = await fetch_one(
        """SELECT thesis, assignment_ok FROM research_watchlist
           WHERE account_id = ? AND symbol = ?""", (account_id, sym))
    open_pos = await fetch_one(
        """SELECT COUNT(*) AS n FROM journal_trades
           WHERE account_id = ? AND underlying = ? AND status = 'open'""",
        (account_id, sym))
    last_note = await fetch_one(
        """SELECT note, created_at FROM research_notes
           WHERE account_id = ? AND symbol = ? ORDER BY id DESC LIMIT 1""",
        (account_id, sym))

    sup = dd["supports"][0] if dd.get("supports") else None
    res = dd["resistances"][0] if dd.get("resistances") else None
    facts = f"""SYMBOL: {sym} @ ${dd['price']} (as of {dd['as_of']})
RETURNS: 5d {_fmt(s.get('ret_5d'), '%')} · 20d {_fmt(s.get('ret_20d'), '%')} · vs 52w high {_fmt(s.get('pct_from_52w_high'), '%')} / low +{_fmt(s.get('pct_from_52w_low'), '%')}
REGIME: {_fmt(s.get('regime'))} (SMA50 {_fmt(s.get('sma50'))} / SMA200 {_fmt(s.get('sma200'))})
VOLUME: today/20d-avg ratio {_fmt(s.get('volume_ratio'))}
VOL: RSI14 {_fmt(s.get('rsi14'))} · RV20 {_fmt(s.get('rv20'))} · ATM IV {_fmt(s.get('atm_iv'))} · IV/RV {_fmt(s.get('iv_rv_ratio'))}
SUPPORT: {f"{sup['lo']}-{sup['hi']} ({sup['touches']} touches, {sup['distance_pct']}% away)" if sup else 'none detected'}
RESISTANCE: {f"{res['lo']}-{res['hi']} ({res['distance_pct']}% away)" if res else 'none detected'}
EARNINGS: {_fmt(s.get('earnings'))}
MY THESIS: {(row or {}).get('thesis') or '(none stated)'} · assignment_ok={(row or {}).get('assignment_ok')}
OPEN POSITIONS IN NAME: {(open_pos or {}).get('n', 0)}
MY LAST NOTE: {((last_note or {}).get('note') or '(none)')[:300]}"""

    prompt = f"""You are the overnight desk analyst writing the daily card for ONE watchlist name.
ALL numbers you may cite are below — computed by code; do not invent any number,
and do NOT give buy/sell/entry advice (a separate consult agent does that on request).

{facts}

Write a tight update (<=120 words) in 2-3 short paragraphs or bullets:
1) what today's tape did in context (price action, volume, regime),
2) where the name sits vs its levels and vol (support/resistance, IV vs RV),
3) what to WATCH next (earnings timing, a level, a vol condition) given my thesis.
Plain markdown, no headers, no preamble."""

    text = await ai_review._run_claude(prompt)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO research_briefs (account_id, symbol, kind, content) VALUES (?, ?, 'daily', ?)",
            (account_id, sym, text),
        )
        await db.commit()
    return {"symbol": sym, "content": text}


async def daily_update_all(account_id: int) -> dict:
    """Nightly AI updates for every watchlist name; one failure never kills
    the rest, and absence of the claude CLI just skips the whole pass."""
    from app.services import ai_review
    if not ai_review.ai_available():
        return {"updated": 0, "skipped": "ai_unavailable"}
    rows = await fetch_all(
        "SELECT symbol FROM research_watchlist WHERE account_id = ?", (account_id,))
    ok, failed = 0, []
    for r in rows:
        try:
            await daily_update(account_id, r["symbol"])
            ok += 1
        except Exception as e:
            logger.warning("daily update %s failed: %s", r["symbol"], e)
            failed.append(r["symbol"])
    return {"updated": ok, "failed": failed}


async def overview(account_id: int, history_days: int = 30) -> dict:
    """The Research view payload: each watchlist name with its latest snapshot,
    a short history (for sparklines/trends), flags, and journal linkage."""
    names = await fetch_all(
        """SELECT id, symbol, thesis, assignment_ok, created_at
           FROM research_watchlist WHERE account_id = ? ORDER BY symbol""",
        (account_id,),
    )
    since = (date.today() - timedelta(days=history_days)).isoformat()
    cards = []
    for n in names:
        snaps = await fetch_all(
            """SELECT date, price, rsi14, rv20, atm_iv, iv_rv, support_lo,
                      support_hi, support_dist_pct, support_touches, earnings, flags
               FROM research_snapshots
               WHERE account_id = ? AND symbol = ? AND date >= ?
               ORDER BY date""",
            (account_id, n["symbol"], since),
        )
        latest = dict(snaps[-1]) if snaps else None
        if latest:
            latest["flags"] = json.loads(latest.get("flags") or "[]")
            if len(snaps) >= 2 and snaps[-2]["price"] and latest["price"]:
                latest["chg_1d_pct"] = round(
                    (latest["price"] / snaps[-2]["price"] - 1) * 100, 2)
            else:
                latest["chg_1d_pct"] = None
        trades = await fetch_one(
            """SELECT COUNT(*) AS n,
                      SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_n
               FROM journal_trades WHERE account_id = ? AND underlying = ?""",
            (account_id, n["symbol"]),
        )
        last_brief = await fetch_one(
            """SELECT kind, created_at FROM research_briefs
               WHERE account_id = ? AND symbol = ? ORDER BY id DESC LIMIT 1""",
            (account_id, n["symbol"]),
        )
        last_daily = await fetch_one(
            """SELECT content, created_at FROM research_briefs
               WHERE account_id = ? AND symbol = ? AND kind = 'daily'
               ORDER BY id DESC LIMIT 1""",
            (account_id, n["symbol"]),
        )
        cards.append({
            **n,
            "latest": latest,
            "history": [{"date": s["date"], "price": s["price"],
                         "rsi14": s["rsi14"], "iv_rv": s["iv_rv"],
                         "support_dist_pct": s["support_dist_pct"]}
                        for s in snaps],
            "journal_trades": trades["n"] if trades else 0,
            "open_positions": (trades["open_n"] or 0) if trades else 0,
            "last_ai": dict(last_brief) if last_brief else None,
            "last_daily": dict(last_daily) if last_daily else None,
        })
    return {"watchlist": cards}
