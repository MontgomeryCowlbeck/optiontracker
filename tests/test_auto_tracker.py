"""Auto-tracker: fills → trades with no manual grouping. Position-based FIFO,
0DTE cycle separation, scale-ins, epoch floor, expiry sweep, live valuation."""
import pytest

from app.database import get_db, fetch_all, fetch_one
from app.services.journal_trades import auto_group_fills, close_expired_trades
from app.services.calculations import unrealized_journal_pnl

SYM_P = "QQQ   260814P00560000"
SYM_P2 = "QQQ   260814P00555000"

_ids = iter(range(1, 10_000))


async def _fill(account_id, *, sym=SYM_P, action="STO", qty=1, price=1.0,
                at="2026-07-20T14:00:00+00:00", order=None, strike=None,
                trade_date=None, expiration="2026-08-14", underlying="QQQ"):
    if strike is None:
        strike = 555.0 if sym == SYM_P2 else 560.0
    is_opening = action in ("BTO", "STO")
    value = price * qty * 100
    value = value if action in ("STO", "STC") else -value
    async with get_db() as db:
        await db.execute(
            """INSERT INTO tt_fills
               (account_id, external_id, order_id, underlying, option_symbol,
                option_type, strike, expiration, action, is_opening, quantity,
                price, fees, value, executed_at, trade_date)
               VALUES (?, ?, ?, ?, ?, 'put', ?, ?, ?, ?, ?, ?, 1.0, ?, ?, ?)""",
            (account_id, f"ext-{next(_ids)}", order, underlying, sym, strike, expiration,
             action, is_opening, qty, price, value, at, trade_date or at[:10]),
        )
        await db.commit()


async def _trades(account_id):
    return await fetch_all(
        "SELECT * FROM journal_trades WHERE account_id = ? ORDER BY id", (account_id,))


async def test_spread_created_then_closed(auth_client, account):
    aid = account["id"]
    # Put credit spread in one TT order: short 560, long 555.
    await _fill(aid, sym=SYM_P, action="STO", price=2.0, order="o1", at="2026-07-20T14:00:00+00:00")
    await _fill(aid, sym=SYM_P2, action="BTO", price=0.8, order="o1", at="2026-07-20T14:00:00+00:00")
    r = await auto_group_fills(aid)
    assert r["trades_created"] == 1 and r["fills_attached"] == 0

    trades = await _trades(aid)
    assert len(trades) == 1
    t = trades[0]
    assert t["status"] == "open"
    assert t["direction"] == "put_credit_spread"
    assert t["entry_premium"] == 120.0  # 200 credit - 80 debit

    # Close both legs later (another order) — trade auto-closes.
    await _fill(aid, sym=SYM_P, action="BTC", price=1.0, order="o2", at="2026-07-25T14:00:00+00:00")
    await _fill(aid, sym=SYM_P2, action="STC", price=0.4, order="o2", at="2026-07-25T14:00:00+00:00")
    r = await auto_group_fills(aid)
    assert r["fills_attached"] == 2 and r["trades_created"] == 0

    t = (await _trades(aid))[0]
    assert t["status"] == "closed"
    # cash: +200 -80 -100 +40 - 4 fees
    assert t["realized_pnl"] == 56.0


async def test_0dte_cycles_stay_separate(auth_client, account):
    aid = account["id"]
    seq = [("STO", "09:31"), ("BTC", "09:45"), ("STO", "10:05"), ("BTC", "10:20")]
    for action, hhmm in seq:
        await _fill(aid, action=action, price=1.0,
                    at=f"2026-07-20T{hhmm}:00-04:00", expiration="2026-07-20")
    await auto_group_fills(aid)
    trades = await _trades(aid)
    assert len(trades) == 2
    assert all(t["status"] == "closed" for t in trades)
    assert all(t["is_0dte"] for t in trades)


async def test_scale_in_merges_and_closes(auth_client, account):
    aid = account["id"]
    await _fill(aid, action="STO", qty=1, at="2026-07-20T14:00:00+00:00")
    await _fill(aid, action="STO", qty=1, at="2026-07-20T15:00:00+00:00")  # scale-in
    await _fill(aid, action="BTC", qty=2, at="2026-07-21T14:00:00+00:00")  # close all
    r = await auto_group_fills(aid)
    assert r["trades_created"] == 1 and r["fills_attached"] == 2
    trades = await _trades(aid)
    assert len(trades) == 1 and trades[0]["status"] == "closed"


async def test_oversized_close_left_for_repair(auth_client, account):
    aid = account["id"]
    await _fill(aid, action="STO", qty=2, at="2026-07-20T14:00:00+00:00")
    await _fill(aid, action="BTC", qty=1, at="2026-07-20T15:00:00+00:00")
    await _fill(aid, action="BTC", qty=2, at="2026-07-20T16:00:00+00:00")  # only 1 left open
    r = await auto_group_fills(aid)
    assert r["unmatched"] == 1
    t = (await _trades(aid))[0]
    assert t["status"] == "open"  # 1 contract still unaccounted
    left = await fetch_all(
        "SELECT * FROM tt_fills WHERE account_id = ? AND journal_trade_id IS NULL AND dismissed = 0",
        (aid,))
    assert len(left) == 1 and left[0]["quantity"] == 2


async def test_pre_epoch_fills_dismissed(auth_client, account):
    aid = account["id"]
    # Fill dated before the 2026-07-14 epoch.
    await _fill(aid, action="STO", at="2026-07-10T14:00:00+00:00", trade_date="2026-07-10")
    # Closing fill after the epoch for a position opened before it (no open trade).
    await _fill(aid, action="BTC", at="2026-07-16T14:00:00+00:00")
    r = await auto_group_fills(aid)
    assert r["dismissed_pre_epoch"] == 2
    assert r["trades_created"] == 0 and r["unmatched"] == 0
    assert await _trades(aid) == []
    active = await fetch_all(
        "SELECT * FROM tt_fills WHERE account_id = ? AND dismissed = 0", (aid,))
    assert active == []


async def test_expiry_sweep(auth_client, account):
    aid = account["id"]
    await _fill(aid, action="STO", price=1.5, at="2026-07-15T14:00:00+00:00",
                expiration="2026-07-17")
    await auto_group_fills(aid)
    n = await close_expired_trades(aid)
    assert n == 1
    t = (await _trades(aid))[0]
    assert t["status"] == "closed"
    assert t["exit_reason"] == "expired"
    assert t["exit_premium"] == 0
    assert t["realized_pnl"] == 149.0  # 150 credit - 1 fee
    assert t["exit_at"] == "2026-07-17T16:00:00"


async def test_expiry_sweep_spares_live_legs(auth_client, account):
    aid = account["id"]
    await _fill(aid, action="STO", at="2026-07-15T14:00:00+00:00", expiration="2099-01-15")
    await auto_group_fills(aid)
    assert await close_expired_trades(aid) == 0
    assert (await _trades(aid))[0]["status"] == "open"


def test_unrealized_math():
    # Short 1 put @2.00 credit, long 1 put @0.80 debit, $2 fees; marks 1.00/0.30.
    fills = [
        {"option_symbol": "S", "action": "STO", "is_opening": 1, "quantity": 1,
         "value": 200.0, "fees": 1.0},
        {"option_symbol": "L", "action": "BTO", "is_opening": 1, "quantity": 1,
         "value": -80.0, "fees": 1.0},
    ]
    v = unrealized_journal_pnl(fills, {"S": 1.0, "L": 0.3})
    assert v["cash_flow"] == 118.0
    assert v["liquidation_value"] == -70.0   # -100 to buy back short, +30 for long
    assert v["unrealized_pnl"] == 48.0
    assert v["marks_missing"] == []

    v = unrealized_journal_pnl(fills, {"S": 1.0})
    assert v["unrealized_pnl"] is None
    assert v["marks_missing"] == ["L"]


async def test_positions_endpoint(auth_client, account):
    aid = account["id"]
    await _fill(aid, action="STO", price=2.0, at="2026-07-20T14:00:00+00:00")
    await auto_group_fills(aid)
    async with get_db() as db:
        await db.execute(
            """INSERT INTO greek_snapshots (account_id, option_symbol, snapshot_type,
                                            delta, iv, mark)
               VALUES (?, ?, 'interim', -0.22, 0.19, 1.0)""",
            (aid, SYM_P),
        )
        await db.commit()

    r = await auth_client.get("/api/journal/positions")
    assert r.status_code == 200
    pos = r.json()["positions"]
    assert len(pos) == 1
    p = pos[0]
    assert p["legs"][0]["net"] == -1
    assert p["legs"][0]["mark"] == 1.0
    assert p["unrealized_pnl"] == 99.0        # +200 credit - 1 fee - 100 to close
    assert p["pct_of_credit"] == 49.5         # ~halfway to the 50% PT
