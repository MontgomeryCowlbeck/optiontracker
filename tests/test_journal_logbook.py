from app.database import get_db


async def _seed_trade(account_id, entry_at, pnl):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO journal_trades
               (account_id, underlying, direction, status, realized_pnl, is_system,
                exit_reason, entry_at, exit_at)
               VALUES (?, 'QQQ', 'long_call', 'closed', ?, 1, 'target', ?, ?)""",
            (account_id, pnl, entry_at, entry_at),
        )
        await db.commit()


async def test_logbook_groups_by_day_newest_first(auth_client, account):
    await _seed_trade(account["id"], "2026-06-27T15:00:00", 100.0)
    await _seed_trade(account["id"], "2026-06-29T14:00:00", 50.0)
    await _seed_trade(account["id"], "2026-06-29T15:00:00", -20.0)

    resp = await auth_client.get("/api/journal/logbook")
    assert resp.status_code == 200
    days = resp.json()["days"]
    assert [d["date"] for d in days] == ["2026-06-29", "2026-06-27"]
    assert len(days[0]["trades"]) == 2
    assert days[0]["stats"]["total_pnl"] == 30.0
    assert days[0]["stats"]["count"] == 2
    # Seeded trades carry no edge_id: the new sleeve-agnostic coaching
    # nudges toward naming the edge instead of praising "the system".
    assert "no edge" in days[0]["message"].lower()
    assert days[1]["stats"]["total_pnl"] == 100.0


async def test_logbook_empty(auth_client, account):
    resp = await auth_client.get("/api/journal/logbook")
    assert resp.status_code == 200
    assert resp.json()["days"] == []
