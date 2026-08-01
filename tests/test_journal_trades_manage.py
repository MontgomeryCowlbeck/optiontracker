from app.database import get_db


async def _seed_fill(account_id, external_id, *, underlying="QQQ", action="BTO",
                     is_opening=True, value=-40.0,
                     executed_at="2026-06-28T18:32:00+00:00"):
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO tt_fills
               (account_id, external_id, underlying, option_symbol, option_type,
                strike, expiration, action, is_opening, quantity, price, fees, value,
                executed_at, trade_date)
               VALUES (?, ?, ?, 'SYM', 'call', 480, '2026-06-28', ?, ?, 1, 0.40, 0.64, ?, ?, '2026-06-28')""",
            (account_id, external_id, underlying, action, 1 if is_opening else 0,
             value, executed_at),
        )
        await db.commit()
        return cur.lastrowid


async def _open_trade(auth_client, account, ext="O"):
    fid = await _seed_fill(account["id"], ext)
    return (await auth_client.post("/api/journal/trades", json={"fill_ids": [fid]})).json()


async def test_attach_close_flips_to_closed(auth_client, account):
    trade = await _open_trade(auth_client, account)
    assert trade["status"] == "open"
    close_id = await _seed_fill(account["id"], "C1", action="STC", is_opening=False,
                                value=55.0, executed_at="2026-06-28T18:39:00+00:00")
    resp = await auth_client.post(f"/api/journal/trades/{trade['id']}/fills",
                                  json={"fill_ids": [close_id]})
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["status"] == "closed"
    assert updated["realized_pnl"] == 13.72


async def test_attach_mismatched_underlying_rejected(auth_client, account):
    trade = await _open_trade(auth_client, account)
    spy = await _seed_fill(account["id"], "SPY1", underlying="SPY", action="STC", is_opening=False, value=55.0)
    resp = await auth_client.post(f"/api/journal/trades/{trade['id']}/fills",
                                  json={"fill_ids": [spy]})
    assert resp.status_code == 400


async def test_update_manual_fields(auth_client, account):
    trade = await _open_trade(auth_client, account)
    resp = await auth_client.put(f"/api/journal/trades/{trade['id']}", json={
        "conviction": 1, "thesis_worked": "partial", "reflection": "forced it"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["conviction"] == 1
    assert body["thesis_worked"] == "partial"
    assert body["reflection"] == "forced it"


async def test_list_and_detail(auth_client, account):
    trade = await _open_trade(auth_client, account)
    lst = (await auth_client.get("/api/journal/trades")).json()["trades"]
    assert any(t["id"] == trade["id"] for t in lst)
    assert "edge_name" in lst[0]
    detail = (await auth_client.get(f"/api/journal/trades/{trade['id']}")).json()
    assert detail["id"] == trade["id"]
    assert len(detail["fills"]) == 1


async def test_update_and_attach_missing_404(auth_client, account):
    assert (await auth_client.put("/api/journal/trades/9999", json={"conviction": 3})).status_code == 404
    assert (await auth_client.get("/api/journal/trades/9999")).status_code == 404
    assert (await auth_client.post("/api/journal/trades/9999/fills", json={"fill_ids": [1]})).status_code == 404
