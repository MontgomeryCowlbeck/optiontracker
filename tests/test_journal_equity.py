from app.database import get_db


async def _seed_snapshot(account_id, date, portfolio, spy):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO benchmark_snapshots
               (account_id, snapshot_date, spy_price, portfolio_value, cash_balance)
               VALUES (?, ?, ?, ?, 0)""",
            (account_id, date, spy, portfolio),
        )
        await db.commit()


async def test_equity_curve_ordered(auth_client, account):
    await _seed_snapshot(account["id"], "2026-06-27", 10500.0, 600.0)
    await _seed_snapshot(account["id"], "2026-06-26", 10000.0, 595.0)
    resp = await auth_client.get("/api/journal/equity")
    assert resp.status_code == 200
    curve = resp.json()["curve"]
    assert [p["snapshot_date"] for p in curve] == ["2026-06-26", "2026-06-27"]
    assert curve[0]["portfolio_value"] == 10000.0


async def test_equity_curve_empty(auth_client, account):
    resp = await auth_client.get("/api/journal/equity")
    assert resp.status_code == 200
    assert resp.json()["curve"] == []
