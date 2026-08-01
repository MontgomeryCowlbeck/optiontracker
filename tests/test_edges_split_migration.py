"""The edge/mechanism vocabulary split migration: an old-schema DB (mechanisms
= edges, journal_trades.mechanism_id) comes out with edges/edge_id and a new
per-structure mechanisms table, ids and links preserved."""
import aiosqlite
import pytest

from app.database import migrate_edges_split, seed_mechanisms_for_account


OLD_SCHEMA = """
CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
CREATE TABLE mechanisms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    name TEXT NOT NULL,
    criteria TEXT, regime TEXT,
    status TEXT NOT NULL DEFAULT 'developing',
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, name)
);
CREATE INDEX idx_mechanisms_account ON mechanisms(account_id);
CREATE TABLE journal_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    underlying TEXT, direction TEXT, status TEXT DEFAULT 'open',
    mechanism_id INTEGER REFERENCES mechanisms(id)
);
CREATE INDEX idx_journal_trades_mechanism ON journal_trades(mechanism_id);
CREATE TABLE trade_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    underlying TEXT NOT NULL,
    mechanism_id INTEGER REFERENCES mechanisms(id),
    status TEXT NOT NULL DEFAULT 'open'
);
"""


@pytest.fixture
async def old_db(tmp_path):
    async with aiosqlite.connect(tmp_path / "old.db") as db:
        await db.executescript(OLD_SCHEMA)
        await db.execute("INSERT INTO accounts (name) VALUES ('acct')")
        await db.execute(
            "INSERT INTO mechanisms (account_id, name, status) "
            "VALUES (1, 'Strategy #1 — premium selling', 'validated')")
        await db.execute(
            "INSERT INTO journal_trades (account_id, underlying, direction, mechanism_id) "
            "VALUES (1, 'SPY', 'put_credit_spread', 1)")
        await db.execute(
            "INSERT INTO trade_intents (account_id, underlying, mechanism_id) "
            "VALUES (1, 'QQQ', 1)")
        await db.commit()
        yield db


async def test_split_renames_and_preserves_links(old_db):
    await migrate_edges_split(old_db)
    await old_db.commit()

    cur = await old_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in await cur.fetchall()}
    assert "edges" in tables
    assert "mechanisms" not in tables  # old table renamed; new one comes from base schema

    cur = await old_db.execute("SELECT id, name, status FROM edges")
    rows = await cur.fetchall()
    assert rows == [(1, "Strategy #1 — premium selling", "validated")]

    cur = await old_db.execute("SELECT edge_id FROM journal_trades WHERE id = 1")
    assert (await cur.fetchone())[0] == 1
    cur = await old_db.execute("SELECT edge_id FROM trade_intents WHERE id = 1")
    assert (await cur.fetchone())[0] == 1


async def test_split_is_idempotent(old_db):
    await migrate_edges_split(old_db)
    await migrate_edges_split(old_db)  # second run must be a no-op
    await old_db.commit()
    cur = await old_db.execute("SELECT COUNT(*) FROM edges")
    assert (await cur.fetchone())[0] == 1


async def test_seed_is_edit_safe(old_db):
    """Seeding never clobbers an edited playbook."""
    await migrate_edges_split(old_db)
    await old_db.execute("""
        CREATE TABLE mechanisms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            structure TEXT NOT NULL, name TEXT NOT NULL,
            rules TEXT, notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id, structure)
        )""")
    await seed_mechanisms_for_account(old_db, 1)
    await old_db.execute(
        "UPDATE mechanisms SET rules = 'edited' WHERE structure = 'short_put'")
    await seed_mechanisms_for_account(old_db, 1)  # re-seed (a restart)
    cur = await old_db.execute(
        "SELECT rules FROM mechanisms WHERE structure = 'short_put'")
    assert (await cur.fetchone())[0] == "edited"
