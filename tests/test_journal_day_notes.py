from app.database import get_db


async def _seed_trade(account_id, entry_at, pnl=10.0):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO journal_trades
               (account_id, underlying, direction, status, realized_pnl, is_system,
                exit_reason, entry_at, exit_at)
               VALUES (?, 'QQQ', 'long_call', 'closed', ?, 1, 'target', ?, ?)""",
            (account_id, pnl, entry_at, entry_at),
        )
        await db.commit()


async def test_save_and_read_day_note_via_logbook(auth_client, account):
    await _seed_trade(account["id"], "2026-06-29T14:00:00")
    put = await auth_client.put("/api/journal/day-notes/2026-06-29",
                                json={"note": "Choppy open. Stay patient."})
    assert put.status_code == 200

    days = (await auth_client.get("/api/journal/logbook")).json()["days"]
    today = next(d for d in days if d["date"] == "2026-06-29")
    assert today["note"] == "Choppy open. Stay patient."


async def test_day_note_upsert_overwrites(auth_client, account):
    await _seed_trade(account["id"], "2026-06-29T14:00:00")
    await auth_client.put("/api/journal/day-notes/2026-06-29", json={"note": "first"})
    await auth_client.put("/api/journal/day-notes/2026-06-29", json={"note": "second"})
    days = (await auth_client.get("/api/journal/logbook")).json()["days"]
    today = next(d for d in days if d["date"] == "2026-06-29")
    assert today["note"] == "second"


async def test_logbook_note_null_when_unset(auth_client, account):
    await _seed_trade(account["id"], "2026-06-27T14:00:00")
    days = (await auth_client.get("/api/journal/logbook")).json()["days"]
    assert days[0]["note"] is None


async def test_ai_feedback_pasted_persists_in_logbook(auth_client, account):
    await _seed_trade(account["id"], "2026-06-29T14:00:00")
    put = await auth_client.put("/api/journal/day-notes/2026-06-29",
                                json={"ai_feedback": "Disciplined day. Watch the off-plan winner."})
    assert put.status_code == 200
    assert put.json()["ai_feedback"].startswith("Disciplined")
    days = (await auth_client.get("/api/journal/logbook")).json()["days"]
    today = next(d for d in days if d["date"] == "2026-06-29")
    assert today["ai_feedback"].startswith("Disciplined")


async def test_note_and_feedback_save_independently(auth_client, account):
    await _seed_trade(account["id"], "2026-06-29T14:00:00")
    await auth_client.put("/api/journal/day-notes/2026-06-29", json={"note": "stay patient"})
    # saving feedback must not wipe the note, and vice versa
    await auth_client.put("/api/journal/day-notes/2026-06-29", json={"ai_feedback": "good process"})
    days = (await auth_client.get("/api/journal/logbook")).json()["days"]
    today = next(d for d in days if d["date"] == "2026-06-29")
    assert today["note"] == "stay patient"
    assert today["ai_feedback"] == "good process"
