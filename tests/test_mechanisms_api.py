"""Mechanisms = per-structure playbooks, auto-resolved onto trades via
journal_trades.direction. The API edits playbook text; it never assigns."""
from app.database import get_db


async def _seed_trade(account_id, direction, status="closed", pnl=10.0):
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO journal_trades
               (account_id, underlying, direction, status, realized_pnl, exit_at)
               VALUES (?, 'QQQ', ?, ?, ?, '2026-06-27T15:00:00')""",
            (account_id, direction, status, pnl))
        await db.commit()
        return cur.lastrowid


async def test_seeded_playbooks_listed(auth_client):
    resp = await auth_client.get("/api/journal/mechanisms")
    assert resp.status_code == 200
    by_structure = {m["structure"]: m for m in resp.json()["mechanisms"]}
    # the standard seeds are present with playbook rules text
    assert "put_credit_spread" in by_structure
    assert "short_strangle" in by_structure
    assert "Strategy #1" in by_structure["put_credit_spread"]["rules"]


async def test_trade_counts_and_unplaybooked(auth_client, account):
    await _seed_trade(account["id"], "put_credit_spread")
    await _seed_trade(account["id"], "custom_5_leg")  # no seed for this shape
    resp = await auth_client.get("/api/journal/mechanisms")
    data = resp.json()
    pcs = next(m for m in data["mechanisms"] if m["structure"] == "put_credit_spread")
    assert pcs["trade_count"] == 1
    orphans = {o["structure"]: o for o in data["unplaybooked"]}
    assert orphans["custom_5_leg"]["trade_count"] == 1


async def test_add_playbook_for_orphan_structure(auth_client):
    resp = await auth_client.post("/api/journal/mechanisms", json={
        "structure": "Custom_5_Leg", "name": "Custom 5-leg",
        "rules": "should not exist; explain yourself"})
    assert resp.status_code == 200
    assert resp.json()["structure"] == "custom_5_leg"  # normalized
    dup = await auth_client.post("/api/journal/mechanisms", json={
        "structure": "custom_5_leg", "name": "Again"})
    assert dup.status_code == 409


async def test_update_playbook_rules(auth_client):
    listed = (await auth_client.get("/api/journal/mechanisms")).json()["mechanisms"]
    target = next(m for m in listed if m["structure"] == "short_put")
    resp = await auth_client.put(f"/api/journal/mechanisms/{target['id']}", json={
        "rules": "my edited rules"})
    assert resp.status_code == 200
    assert resp.json()["rules"] == "my edited rules"


async def test_trade_carries_mechanism_name(auth_client, account):
    await auth_client.get("/api/journal/mechanisms")  # triggers lazy seeding
    trade_id = await _seed_trade(account["id"], "put_credit_spread")
    resp = await auth_client.get(f"/api/journal/trades/{trade_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mechanism_name"] == "Put credit spread"
    assert body["edge_id"] is None  # auto-mechanism never satisfies the edge bar
