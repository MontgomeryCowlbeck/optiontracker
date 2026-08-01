"""Tastytrade fills sync — pulls option-trade transactions into `tt_fills`,
then the tracker (journal_trades.auto_group_fills) turns them into journal
trades automatically. Fills that can't be placed stay unmatched for repair."""
import logging
from datetime import date
from typing import Optional

from app.database import get_db
from app.services.tastytrade import tastytrade_client

logger = logging.getLogger(__name__)

_ACTION_MAP = {
    "Buy to Open":  ("BTO", True),
    "Sell to Open": ("STO", True),
    "Buy to Close": ("BTC", False),
    "Sell to Close": ("STC", False),
}


def _f(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_tt_symbol(symbol: str):
    """'QQQ   260628C00480000' -> (ticker, 'YYYY-MM-DD', 'call'/'put', strike)."""
    if not symbol or len(symbol) < 21:
        return None
    try:
        ticker = symbol[:6].rstrip()
        d = symbol[6:12]
        type_char = symbol[12]
        strike = int(symbol[13:21]) / 1000.0
        expiration = f"20{d[:2]}-{d[2:4]}-{d[4:6]}"
        option_type = "call" if type_char.upper() == "C" else "put"
        return ticker, expiration, option_type, strike
    except (ValueError, IndexError):
        return None


def parse_fill(txn: dict) -> Optional[dict]:
    """Normalize a raw TT transaction into a fill dict, or None to skip.
    Only option-trade fills (Buy/Sell to Open/Close) are kept."""
    if txn.get("transaction-type") != "Trade":
        return None
    if txn.get("instrument-type") != "Equity Option":
        return None

    action_info = _ACTION_MAP.get(txn.get("action", ""))
    if action_info is None:
        return None
    action, is_opening = action_info

    parsed_sym = _parse_tt_symbol(txn.get("symbol", ""))
    if not parsed_sym:
        return None
    ticker, expiration, option_type, strike = parsed_sym

    underlying = (txn.get("underlying-symbol") or ticker).upper()

    try:
        quantity = abs(int(float(txn.get("quantity", 0))))
    except (ValueError, TypeError):
        quantity = 0

    fees = ((_f(txn.get("commission")) or 0.0)
            + (_f(txn.get("clearing-fees")) or 0.0)
            + (_f(txn.get("regulatory-fees")) or 0.0))

    value_raw = _f(txn.get("value")) or 0.0
    value = value_raw if txn.get("value-effect") == "Credit" else -value_raw

    executed_at = txn.get("executed-at", "")
    trade_date = txn.get("transaction-date") or (executed_at[:10] if executed_at else str(date.today()))

    return {
        "external_id": str(txn.get("id", "")),
        "order_id": str(txn.get("order-id")) if txn.get("order-id") else None,
        "underlying": underlying,
        "option_symbol": txn.get("symbol", ""),
        "option_type": option_type,
        "strike": strike,
        "expiration": expiration,
        "action": action,
        "is_opening": is_opening,
        "quantity": quantity,
        "price": _f(txn.get("price")) or 0.0,
        "fees": round(fees, 4),
        "value": value,
        "executed_at": executed_at,
        "trade_date": trade_date,
    }


async def store_fills(account_id: int, fills: list[dict]) -> dict:
    """Insert parsed fills, deduping on (account_id, external_id)."""
    synced = 0
    skipped = 0
    async with get_db() as db:
        for f in fills:
            if not f:
                continue
            cur = await db.execute(
                "SELECT 1 FROM tt_fills WHERE account_id = ? AND external_id = ?",
                (account_id, f["external_id"]),
            )
            if await cur.fetchone():
                skipped += 1
                continue
            await db.execute(
                """INSERT INTO tt_fills
                   (account_id, external_id, order_id, underlying, option_symbol,
                    option_type, strike, expiration, action, is_opening, quantity,
                    price, fees, value, executed_at, trade_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (account_id, f["external_id"], f["order_id"], f["underlying"],
                 f["option_symbol"], f["option_type"], f["strike"], f["expiration"],
                 f["action"], f["is_opening"], f["quantity"], f["price"], f["fees"],
                 f["value"], f["executed_at"], f["trade_date"]),
            )
            synced += 1
        await db.commit()
    return {"synced": synced, "skipped": skipped}


async def sync_fills(account_id: int, account_number: str,
                     since_date: Optional[str] = None) -> dict:
    """Fetch TT transactions, parse option fills, store them, then run the
    tracker: auto-group fills into trades and sweep expired ones. One call
    keeps the journal current — no manual grouping step."""
    from app.database import fetch_all
    from app.services.journal_trades import (
        auto_group_fills, close_expired_trades, reclassify_covered_calls,
    )

    txns = await tastytrade_client.get_all_transactions(account_number, start_date=since_date)
    parsed = [parse_fill(t) for t in (txns or [])]
    parsed = [p for p in parsed if p]
    prev = {r["id"]: r["status"] for r in await fetch_all(
        "SELECT id, status FROM journal_trades WHERE account_id = ?", (account_id,))}
    result = await store_fills(account_id, parsed)
    result.update(await auto_group_fills(account_id))
    result["trades_expired"] = await close_expired_trades(account_id)
    # Share coverage: a short call against >=100 held shares/contract is a
    # covered call. Best-effort — a positions-API hiccup must not fail the sync.
    try:
        shares = await equity_shares(account_number)
        result["covered_reclassified"] = await reclassify_covered_calls(account_id, shares)
    except Exception:
        logger.warning("sync_fills: equity-position lookup failed; covered-call "
                       "reclassification skipped this sync", exc_info=True)
        result["covered_reclassified"] = 0
    logger.info("sync_fills account=%d synced=%d skipped=%d created=%d attached=%d "
                "unmatched=%d expired=%d covered=%d",
                account_id, result["synced"], result["skipped"],
                result["trades_created"], result["fills_attached"],
                result["unmatched"], result["trades_expired"],
                result["covered_reclassified"])

    # Journaling loop: bind pre-trade intents / auto-associate Strategy #1
    # signal trades, then push one Discord card for the batch. All best-effort —
    # the sync result above is already final.
    try:
        rows = await fetch_all(
            "SELECT * FROM journal_trades WHERE account_id = ?", (account_id,))
        new_ids = [r["id"] for r in rows if r["id"] not in prev]
        closed_now = [r for r in rows
                      if r["status"] == "closed" and prev.get(r["id"]) == "open"]
        bound = {}
        if new_ids:
            from app.services import intents
            await intents.expire_stale_intents(account_id)
            bound = await intents.bind_new_trades(account_id, new_ids)
            rows = await fetch_all(
                "SELECT * FROM journal_trades WHERE account_id = ?", (account_id,))
        if new_ids or closed_now:
            from app.services import notify
            from app.services.journal_analytics import journal_debt
            by_id = {r["id"]: r for r in rows}
            await notify.notify_sync(
                [by_id[i] for i in new_ids if i in by_id],
                [by_id[r["id"]] for r in closed_now if r["id"] in by_id],
                journal_debt(rows)["count"], bound)
    except Exception:
        logger.warning("post-sync journaling hook failed", exc_info=True)
    return result


async def equity_shares(account_number: str) -> dict[str, float]:
    """Net shares held per underlying from live TT positions (short negative)."""
    positions = await tastytrade_client.get_positions(account_number) or []
    shares: dict[str, float] = {}
    for p in positions:
        if p.get("instrument-type") != "Equity" or not p.get("symbol"):
            continue
        qty = float(p.get("quantity", 0) or 0)
        if (p.get("quantity-direction") or "").lower() == "short":
            qty = -qty
        sym = p["symbol"].upper()
        shares[sym] = shares.get(sym, 0.0) + qty
    return shares
