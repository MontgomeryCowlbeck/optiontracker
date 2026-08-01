from app.database import get_db, fetch_all


async def _seed_fill(account_id, external_id="O1"):
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO tt_fills
               (account_id, external_id, underlying, option_symbol, option_type,
                strike, expiration, action, is_opening, quantity, price, fees, value,
                executed_at, trade_date)
               VALUES (?, ?, 'QQQ', 'SYM', 'call', 480, '2026-06-28', 'BTO', 1, 1,
                       0.40, 0.64, -40.0, '2026-06-28T18:32:00+00:00', '2026-06-28')""",
            (account_id, external_id),
        )
        await db.commit()
        return cur.lastrowid


async def test_ungroup_returns_fills_to_inbox_and_deletes_trade(auth_client, account):
    fid = await _seed_fill(account["id"])
    trade = (await auth_client.post("/api/journal/trades", json={"fill_ids": [fid]})).json()
    # fill left the inbox
    assert (await auth_client.get("/api/journal/fills")).json()["fills"] == []

    resp = await auth_client.delete(f"/api/journal/trades/{trade['id']}")
    assert resp.status_code == 200

    # trade gone
    assert (await auth_client.get(f"/api/journal/trades/{trade['id']}")).status_code == 404
    # fill back in the inbox
    inbox = (await auth_client.get("/api/journal/fills")).json()["fills"]
    assert [f["id"] for f in inbox] == [fid]
    rows = await fetch_all("SELECT journal_trade_id FROM tt_fills WHERE id = ?", (fid,))
    assert rows[0]["journal_trade_id"] is None


async def test_ungroup_missing_404(auth_client, account):
    assert (await auth_client.delete("/api/journal/trades/9999")).status_code == 404
