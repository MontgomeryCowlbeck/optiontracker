"""Greek poller — captures live entry/exit delta + IV for open option
positions during market hours into `greek_snapshots`.

State lives in the DB: a symbol is "active" if it has an `entry` snapshot and
no `exit` snapshot. First sight of an open position → entry; still open →
interim; gone since last poll → exit (carrying the last known greeks, since
the contract may have delisted)."""
import logging
from typing import Optional

from app.database import get_db, fetch_all
from app.services.tastytrade import tastytrade_client, tastytrade_to_occ

logger = logging.getLogger(__name__)


def _greeks_for(quotes: dict, occ: str) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    q = quotes.get(occ) or {}
    g = q.get("greeks") or {}
    bid, ask = q.get("bid"), q.get("ask")
    mark = round((bid + ask) / 2, 4) if (bid is not None and ask is not None) else q.get("last")
    return g.get("delta"), g.get("mid_iv"), mark, g.get("theta")


async def _record(db, account_id: int, option_symbol: str, snapshot_type: str,
                  delta: Optional[float], iv: Optional[float],
                  mark: Optional[float] = None,
                  theta: Optional[float] = None) -> None:
    await db.execute(
        """INSERT INTO greek_snapshots (account_id, option_symbol, snapshot_type, delta, iv, mark, theta)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (account_id, option_symbol, snapshot_type, delta, iv, mark, theta),
    )


async def poll_greeks_once(account_id: int, account_number: str) -> dict:
    """One poll: reconcile TT's open option positions against active snapshots."""
    positions = await tastytrade_client.get_positions(account_number) or []
    current = [
        p["symbol"] for p in positions
        if p.get("instrument-type") == "Equity Option"
        and p.get("symbol")
        and float(p.get("quantity", 0) or 0) != 0
    ]

    occ_by_tt = {s: tastytrade_to_occ(s) for s in current}
    quotes = await tastytrade_client.get_option_quotes(list(occ_by_tt.values())) if current else {}

    # Underlying spot alongside the option greeks — one poll feeds both. Feeds
    # breakeven cushion / moneyness on Positions; failure here never blocks greeks.
    underlyings = sorted({
        p.get("underlying-symbol") for p in positions
        if p.get("instrument-type") == "Equity Option" and p.get("underlying-symbol")
    })
    spots = {}
    if underlyings:
        try:
            uq = await tastytrade_client.get_underlying_quotes(underlyings)
            for sym, q in uq.items():
                price = None
                if q.get("bid") is not None and q.get("ask") is not None:
                    price = round((q["bid"] + q["ask"]) / 2, 4)
                elif q.get("last") is not None:
                    price = q["last"]
                if price:
                    spots[sym] = price
        except Exception:
            logger.warning("underlying quote fetch failed", exc_info=True)

    active_prev = {
        r["option_symbol"] for r in await fetch_all(
            """SELECT DISTINCT option_symbol FROM greek_snapshots
               WHERE account_id = ? AND snapshot_type = 'entry'
                 AND option_symbol NOT IN (
                     SELECT option_symbol FROM greek_snapshots
                     WHERE account_id = ? AND snapshot_type = 'exit')""",
            (account_id, account_id),
        )
    }

    counts = {"entries": 0, "interims": 0, "exits": 0}
    current_set = set(current)

    async with get_db() as db:
        for tt_sym in current:
            delta, iv, mark, theta = _greeks_for(quotes, occ_by_tt[tt_sym])
            if tt_sym not in active_prev:
                await _record(db, account_id, tt_sym, "entry", delta, iv, mark, theta)
                counts["entries"] += 1
            else:
                await _record(db, account_id, tt_sym, "interim", delta, iv, mark, theta)
                counts["interims"] += 1

        for sym, price in spots.items():
            await db.execute(
                """INSERT INTO underlying_marks (account_id, symbol, price, captured_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(account_id, symbol) DO UPDATE
                   SET price = excluded.price, captured_at = CURRENT_TIMESTAMP""",
                (account_id, sym, price),
            )

        for tt_sym in active_prev - current_set:
            last = await fetch_all(
                """SELECT delta, iv, mark, theta FROM greek_snapshots
                   WHERE account_id = ? AND option_symbol = ?
                   ORDER BY id DESC LIMIT 1""",
                (account_id, tt_sym),
            )
            d = last[0]["delta"] if last else None
            v = last[0]["iv"] if last else None
            m = last[0]["mark"] if last else None
            th = last[0]["theta"] if last else None
            await _record(db, account_id, tt_sym, "exit", d, v, m, th)
            counts["exits"] += 1

        await db.commit()

    logger.info("greek poll account=%d entries=%d interims=%d exits=%d",
                account_id, counts["entries"], counts["interims"], counts["exits"])
    return counts
