from app.database import get_db, fetch_all
from app.routers import journal as journal_router

TT_SYM = "QQQ   260628C00480000"


async def _seed_fill(account_id, external_id, option_symbol=TT_SYM):
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO tt_fills
               (account_id, external_id, underlying, option_symbol, option_type,
                strike, expiration, action, is_opening, quantity, price, fees, value,
                executed_at, trade_date)
               VALUES (?, ?, 'QQQ', ?, 'call', 480, '2026-06-28', 'BTO', 1, 1, 0.40, 0.64,
                       -40.0, '2026-06-28T18:32:00+00:00', '2026-06-28')""",
            (account_id, external_id, option_symbol),
        )
        await db.commit()
        return cur.lastrowid


async def _seed_snapshot(account_id, option_symbol=TT_SYM, snapshot_type="entry", delta=0.48):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO greek_snapshots (account_id, option_symbol, snapshot_type, delta, iv)
               VALUES (?, ?, ?, ?, 0.25)""",
            (account_id, option_symbol, snapshot_type, delta),
        )
        await db.commit()


async def test_grouping_links_matching_snapshots(auth_client, account):
    await _seed_snapshot(account["id"], snapshot_type="entry", delta=0.48)
    await _seed_snapshot(account["id"], snapshot_type="exit", delta=0.07)
    fid = await _seed_fill(account["id"], "O1")
    trade = (await auth_client.post("/api/journal/trades", json={"fill_ids": [fid]})).json()

    linked = await fetch_all(
        "SELECT snapshot_type FROM greek_snapshots WHERE journal_trade_id = ?", (trade["id"],))
    assert {r["snapshot_type"] for r in linked} == {"entry", "exit"}

    detail = (await auth_client.get(f"/api/journal/trades/{trade['id']}")).json()
    assert len(detail["greeks"]) == 2


async def test_poll_endpoint(auth_client, account, monkeypatch):
    async def fake_get_accounts():
        return [{"account-number": "5WT1"}]

    async def fake_poll(account_id, account_number):
        return {"entries": 2, "interims": 1, "exits": 0}

    monkeypatch.setattr(journal_router.tastytrade_client, "get_accounts", fake_get_accounts)
    monkeypatch.setattr(journal_router, "poll_greeks_once", fake_poll)
    resp = await auth_client.post("/api/journal/greeks/poll")
    assert resp.status_code == 200
    assert resp.json() == {"entries": 2, "interims": 1, "exits": 0}
