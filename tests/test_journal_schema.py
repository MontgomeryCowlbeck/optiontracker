from app.database import fetch_all, get_db


async def _columns(table):
    rows = await fetch_all(f"PRAGMA table_info({table})")
    return {r["name"] for r in rows}


async def test_journal_tables_exist():
    rows = await fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in rows}
    assert {"edges", "mechanisms", "tt_fills", "journal_trades", "greek_snapshots"} <= names


async def test_tt_fills_columns():
    cols = await _columns("tt_fills")
    assert {"external_id", "underlying", "option_symbol", "action",
            "is_opening", "value", "journal_trade_id", "dismissed"} <= cols


async def test_journal_trades_columns():
    cols = await _columns("journal_trades")
    assert {"direction", "realized_pnl", "status", "edge_id",
            "is_system", "conviction", "exit_reason", "emotional_state"} <= cols


async def test_tt_fills_external_id_unique_per_account():
    async with get_db() as db:
        await db.execute(
            "INSERT INTO tt_fills (account_id, external_id, underlying, action) "
            "VALUES (1, 'X1', 'QQQ', 'BTO')")
        await db.commit()
        raised = False
        try:
            await db.execute(
                "INSERT INTO tt_fills (account_id, external_id, underlying, action) "
                "VALUES (1, 'X1', 'QQQ', 'BTO')")
            await db.commit()
        except Exception:
            raised = True
        assert raised
