from app.database import get_db, fetch_all


async def _seed_fill(account_id, external_id, *, underlying="QQQ", action="BTO",
                     is_opening=True, value=-40.0, option_type="call", strike=480.0,
                     executed_at="2026-06-28T18:32:00+00:00"):
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO tt_fills
               (account_id, external_id, underlying, option_symbol, option_type,
                strike, expiration, action, is_opening, quantity, price, fees, value,
                executed_at, trade_date)
               VALUES (?, ?, ?, 'SYM', ?, ?, '2026-06-28', ?, ?, 1, 0.40, 0.64, ?, ?, '2026-06-28')""",
            (account_id, external_id, underlying, option_type, strike, action,
             1 if is_opening else 0, value, executed_at),
        )
        await db.commit()
        return cur.lastrowid


async def test_create_trade_groups_fills(auth_client, account):
    open_id = await _seed_fill(account["id"], "O1")
    close_id = await _seed_fill(account["id"], "C1", action="STC", is_opening=False,
                                value=55.0, executed_at="2026-06-28T18:39:00+00:00")
    resp = await auth_client.post("/api/journal/trades", json={"fill_ids": [open_id, close_id]})
    assert resp.status_code == 200
    trade = resp.json()
    assert trade["status"] == "closed"
    assert trade["direction"] == "long_call"
    assert trade["realized_pnl"] == 13.72

    # fills left the inbox
    inbox = (await auth_client.get("/api/journal/fills")).json()["fills"]
    assert inbox == []
    linked = await fetch_all("SELECT journal_trade_id FROM tt_fills WHERE id IN (?, ?)",
                             (open_id, close_id))
    assert all(r["journal_trade_id"] == trade["id"] for r in linked)


async def test_create_requires_opening_fill(auth_client, account):
    close_id = await _seed_fill(account["id"], "C1", action="STC", is_opening=False, value=55.0)
    resp = await auth_client.post("/api/journal/trades", json={"fill_ids": [close_id]})
    assert resp.status_code == 400


async def test_create_rejects_mixed_underlying(auth_client, account):
    a = await _seed_fill(account["id"], "A", underlying="QQQ")
    b = await _seed_fill(account["id"], "B", underlying="SPY")
    resp = await auth_client.post("/api/journal/trades", json={"fill_ids": [a, b]})
    assert resp.status_code == 400


async def test_create_rejects_already_grouped(auth_client, account):
    o = await _seed_fill(account["id"], "O")
    first = await auth_client.post("/api/journal/trades", json={"fill_ids": [o]})
    assert first.status_code == 200
    again = await auth_client.post("/api/journal/trades", json={"fill_ids": [o]})
    assert again.status_code == 400


async def test_manual_fields_persisted(auth_client, account):
    o = await _seed_fill(account["id"], "O")
    resp = await auth_client.post("/api/journal/trades", json={
        "fill_ids": [o], "is_system": True, "conviction": 2,
        "why_entered": "williams oversold", "emotional_state": "calm"})
    assert resp.status_code == 200
    t = resp.json()
    assert t["is_system"] == 1
    assert t["conviction"] == 2
    assert t["why_entered"] == "williams oversold"
    assert t["emotional_state"] == "calm"
    assert t["status"] == "open"
