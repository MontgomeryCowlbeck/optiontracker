from app.database import get_db


async def _seed_closed_trade(account_id, *, mech_name="Validated Long", pnl=100.0,
                             underlying="QQQ", is_system=1, regime="range",
                             exit_reason="target"):
    async with get_db() as db:
        mcur = await db.execute(
            "INSERT INTO edges (account_id, name) VALUES (?, ?)",
            (account_id, mech_name),
        )
        mech_id = mcur.lastrowid
        await db.execute(
            """INSERT INTO journal_trades
               (account_id, underlying, direction, status, realized_pnl, edge_id,
                is_system, regime_read, exit_reason, exit_at)
               VALUES (?, ?, 'long_call', 'closed', ?, ?, ?, ?, ?, '2026-06-28T18:00:00')""",
            (account_id, underlying, pnl, mech_id, is_system, regime, exit_reason),
        )
        await db.commit()


async def test_analytics_endpoint(auth_client, account):
    await _seed_closed_trade(account["id"], mech_name="A", pnl=100.0, underlying="QQQ")
    await _seed_closed_trade(account["id"], mech_name="B", pnl=-40.0, underlying="SPY",
                             is_system=0, exit_reason="panic")
    resp = await auth_client.get("/api/journal/analytics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall"]["total_pnl"] == 60.0
    assert data["overall"]["count"] == 2
    by_mech = {m["edge_name"]: m for m in data["by_edge"]}
    assert by_mech["A"]["total_pnl"] == 100.0
    assert data["system_vs_discretionary"]["discretionary"]["total_pnl"] == -40.0
    assert data["discipline"]["dissonance"]["won_but_off_plan"] == 0


async def test_analytics_empty(auth_client, account):
    resp = await auth_client.get("/api/journal/analytics")
    assert resp.status_code == 200
    assert resp.json()["overall"]["count"] == 0
