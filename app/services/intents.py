"""Pre-trade intents + Strategy #1 auto-association.

Both run against trades the auto-tracker JUST created, inside the sync path:

- intent binding: a new trade matching an open intent (same underlying,
  compatible structure, intent <= 7 days old, oldest first) inherits the
  intent's journal fields — never overwriting anything already set.
- auto-association: a new SPY/QQQ/DIA put credit spread entered on a day the
  live signal fired gets edge "Strategy #1" + is_system, read from the
  quant platform's signal_<date>.json (settings.signal_dir). The signal file
  is the same artifact the 8am cron wrote — no re-derivation here.

Returns {trade_id: "intent" | "signal"} so the Discord card can say which."""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from app.database import fetch_all, fetch_one, get_db

logger = logging.getLogger(__name__)

INTENT_MAX_AGE_DAYS = 7
SIGNAL_DIR = os.environ.get("SIGNAL_DIR", "/data/structured/live")
SIGNAL_TICKERS = {"SPY", "QQQ", "DIA"}
STRATEGY1_NAME = "Strategy #1"


def _signal_fired(trade_date: str, ticker: str) -> bool:
    """Did the live signal fire for this ticker on this date? Reads the cron's
    own signal_<date>.json; missing/unreadable file simply means no."""
    path = Path(SIGNAL_DIR) / f"signal_{trade_date}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    for s in data.get("signals", []):
        if s.get("ticker") == ticker and (s.get("fires") or s.get("signal") == "SELL"):
            return True
    return False


async def _strategy1_edge_id(account_id: int) -> int:
    """Find (case-insensitively, prefix match) or create the Strategy #1
    edge for this account."""
    row = await fetch_one(
        """SELECT id FROM edges
           WHERE account_id = ? AND LOWER(name) LIKE 'strategy #1%'
           ORDER BY id LIMIT 1""",
        (account_id,),
    )
    if row:
        return row["id"]
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO edges (account_id, name, criteria, status)
               VALUES (?, ?, ?, 'validated')""",
            (account_id, STRATEGY1_NAME,
             "calm/fat-gap entry -> risk-budgeted barbell put credit spreads "
             "(SPY/QQQ/DIA), 50% PT, no stop — see strategies/strategy_1_premium_selling"),
        )
        await db.commit()
        return cur.lastrowid


async def _apply_fields(trade_id: int, fields: dict) -> None:
    """COALESCE-set journal fields — pre-fills never overwrite the trader."""
    sets = ", ".join(f"{k} = COALESCE({k}, ?)" for k in fields)
    async with get_db() as db:
        await db.execute(
            f"UPDATE journal_trades SET {sets} WHERE id = ?",
            (*fields.values(), trade_id),
        )
        await db.commit()


async def bind_new_trades(account_id: int, new_trade_ids: list[int]) -> dict:
    """Intent binding + auto-association for freshly created trades."""
    if not new_trade_ids:
        return {}
    bound: dict[int, str] = {}
    cutoff = (datetime.utcnow() - timedelta(days=INTENT_MAX_AGE_DAYS)) \
        .strftime("%Y-%m-%d %H:%M:%S")

    for trade_id in new_trade_ids:
        t = await fetch_one(
            "SELECT * FROM journal_trades WHERE id = ? AND account_id = ?",
            (trade_id, account_id),
        )
        if not t:
            continue

        intent = await fetch_one(
            """SELECT * FROM trade_intents
               WHERE account_id = ? AND status = 'open' AND underlying = ?
                 AND (direction IS NULL OR direction = ?)
                 AND created_at >= ?
               ORDER BY id LIMIT 1""",
            (account_id, t.get("underlying"), t.get("direction"), cutoff),
        )
        if intent:
            await _apply_fields(trade_id, {
                "edge_id": intent["edge_id"],
                "is_system": intent["is_system"],
                "planned_risk": intent["planned_risk"],
                "conviction": intent["conviction"],
                "why_entered": intent["note"],
            })
            async with get_db() as db:
                await db.execute(
                    "UPDATE trade_intents SET status = 'bound', bound_trade_id = ? WHERE id = ?",
                    (trade_id, intent["id"]),
                )
                await db.commit()
            bound[trade_id] = "intent"
            continue

        if (t.get("underlying") in SIGNAL_TICKERS
                and t.get("direction") == "put_credit_spread"
                and t.get("edge_id") is None):
            trade_date = str(t.get("entry_at") or "")[:10]
            if trade_date and _signal_fired(trade_date, t["underlying"]):
                edge_id = await _strategy1_edge_id(account_id)
                await _apply_fields(trade_id, {
                    "edge_id": edge_id,
                    "is_system": 1,
                    "why_entered": f"auto: calm/fat-gap signal fired {trade_date}",
                })
                bound[trade_id] = "signal"

    if bound:
        logger.info("bind_new_trades account=%d bound=%s", account_id, bound)
    return bound


async def expire_stale_intents(account_id: int) -> int:
    """Open intents older than the window flip to 'expired' (housekeeping,
    called on sync so the open list stays honest)."""
    cutoff = (datetime.utcnow() - timedelta(days=INTENT_MAX_AGE_DAYS)) \
        .strftime("%Y-%m-%d %H:%M:%S")
    async with get_db() as db:
        cur = await db.execute(
            """UPDATE trade_intents SET status = 'expired'
               WHERE account_id = ? AND status = 'open' AND created_at < ?""",
            (account_id, cutoff),
        )
        await db.commit()
        return cur.rowcount


async def open_intents(account_id: int) -> list[dict]:
    return await fetch_all(
        """SELECT i.*, e.name AS edge_name
           FROM trade_intents i LEFT JOIN edges e ON e.id = i.edge_id
           WHERE i.account_id = ? AND i.status = 'open'
           ORDER BY i.id DESC""",
        (account_id,),
    )
