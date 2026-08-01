import pytest
from app.database import get_db, fetch_all
from app.services import fills_sync
from app.routers import journal as journal_router


async def _seed_fill(account_id, external_id="A1", grouped=False, dismissed=False):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO tt_fills
               (account_id, external_id, underlying, option_symbol, option_type,
                strike, expiration, action, is_opening, quantity, price, value,
                executed_at, trade_date, journal_trade_id, dismissed)
               VALUES (?, ?, 'QQQ', 'QQQ 260628C00480000', 'call', 480, '2026-06-28',
                       'BTO', 1, 1, 0.40, -40.0, '2026-06-28T18:32:00Z', '2026-06-28',
                       ?, ?)""",
            (account_id, external_id, (1 if grouped else None), (1 if dismissed else 0)),
        )
        await db.commit()


async def test_get_fills_returns_only_ungrouped_undismissed(auth_client, account):
    await _seed_fill(account["id"], "A1")
    await _seed_fill(account["id"], "A2", grouped=True)
    await _seed_fill(account["id"], "A3", dismissed=True)
    resp = await auth_client.get("/api/journal/fills")
    assert resp.status_code == 200
    fills = resp.json()["fills"]
    assert {f["external_id"] for f in fills} == {"A1"}


async def test_dismiss_fill(auth_client, account):
    await _seed_fill(account["id"], "A1")
    row = (await fetch_all("SELECT id FROM tt_fills WHERE account_id = ?", (account["id"],)))[0]
    resp = await auth_client.post(f"/api/journal/fills/{row['id']}/dismiss")
    assert resp.status_code == 200
    after = await fetch_all("SELECT dismissed FROM tt_fills WHERE id = ?", (row["id"],))
    assert after[0]["dismissed"] == 1


async def test_sync_endpoint(auth_client, account, monkeypatch):
    async def fake_get_accounts():
        return [{"account-number": "5WT00001"}]

    async def fake_sync(account_id, account_number, since_date=None):
        return {"synced": 3, "skipped": 1}

    monkeypatch.setattr(journal_router.tastytrade_client, "get_accounts", fake_get_accounts)
    monkeypatch.setattr(journal_router, "sync_fills", fake_sync)
    resp = await auth_client.post("/api/journal/fills/sync")
    assert resp.status_code == 200
    assert resp.json() == {"synced": 3, "skipped": 1}
