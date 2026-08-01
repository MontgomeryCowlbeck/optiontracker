# Trade Journal Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a test harness, the new journal data model, and a Tastytrade "fills inbox" — the additive foundation the rest of the rebuild sits on, with zero changes to the still-running old app.

**Architecture:** Add four new SQLite tables (`mechanisms`, `tt_fills`, `journal_trades`, `greek_snapshots`) via a new idempotent migration wired into the existing `init_db()` chain. Add a `fills_sync` service that pulls TT transactions through the existing `tastytrade_client`, parses option fills, and upserts them (dedup on `external_id`) into the `tt_fills` inbox. Expose the inbox through a new router. Nothing old is deleted in this plan.

**Tech Stack:** FastAPI · aiosqlite · pytest + pytest-asyncio · httpx (ASGITransport for API tests) · existing `app/services/tastytrade.py` client.

## Global Constraints

- Python deps pinned in `requirements.txt`; new deps: `pytest==8.0.0`, `pytest-asyncio==0.23.5` (one line each, exact versions).
- All DB access goes through `app/database.py` helpers (`get_db`, `fetch_one`, `fetch_all`); never open `aiosqlite.connect` directly in app code.
- All new API endpoints authenticate via `get_auth_context_with_api_key` (supports JWT + API key) and scope every query by `auth.account_id`.
- No inline P&L formulas anywhere (existing hard rule); financial math belongs in `app/services/calculations.py`. (Not exercised in this plan, but the rule stands.)
- Migrations must be idempotent (`CREATE TABLE IF NOT EXISTS`, guarded `ALTER`), safe to run on every startup.
- Tests live under `tests/`; run with `pytest` from repo root.
- This plan is ADDITIVE: do not modify or delete any existing router, service, table, or frontend file except `app/main.py` (one router registration) and `requirements.txt`.

---

### Task 1: Test harness

**Files:**
- Modify: `requirements.txt`
- Create: `pytest.ini`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_harness.py`

**Interfaces:**
- Produces: pytest fixtures `account` (returns `dict` with `id`, `user_id`) and `auth_client` (returns an `httpx.AsyncClient` with auth dependency overridden to that account). Both consumed by later tasks.

- [ ] **Step 1: Add test deps to requirements.txt**

Append these two lines to the end of `requirements.txt`:

```
pytest==8.0.0
pytest-asyncio==0.23.5
```

- [ ] **Step 2: Create pytest.ini**

Create `pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
filterwarnings =
    ignore::DeprecationWarning
```

- [ ] **Step 3: Create tests package marker**

Create `tests/__init__.py` (empty file).

- [ ] **Step 4: Create conftest.py**

Create `tests/conftest.py`. The env vars MUST be set before any `app.*` import, because `app/config.py` reads them at import time and `app/database.py` binds `settings` at import:

```python
import os
import tempfile

# Must be set BEFORE importing any app module (config reads env at import time).
_TMP_DB = os.path.join(tempfile.gettempdir(), "optiontracker_test.db")
os.environ["DATABASE_PATH"] = _TMP_DB
os.environ["JWT_SECRET_KEY"] = "test-secret-key-that-is-long-enough-32+chars"
os.environ["TASTYTRADE_CLIENT_ID"] = ""
os.environ["TASTYTRADE_CLIENT_SECRET"] = ""
os.environ["TASTYTRADE_REFRESH_TOKEN"] = ""

import pytest
import pytest_asyncio
import httpx
from httpx import ASGITransport

from app.database import init_db, get_db
from app.main import app
from app.auth.dependencies import get_auth_context_with_api_key, AuthContext


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    """Fresh database for every test."""
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    await init_db()
    yield
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)


@pytest_asyncio.fixture
async def account():
    """Seed one user + one default account; return the account dict."""
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO users (username, email, hashed_password) VALUES (?, ?, ?)",
            ("tester", "tester@example.com", "x"),
        )
        user_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO accounts (user_id, name, is_default) VALUES (?, ?, 1)",
            (user_id, "Test Account"),
        )
        account_id = cur.lastrowid
        await db.commit()
    return {"id": account_id, "user_id": user_id, "name": "Test Account"}


@pytest_asyncio.fixture
async def auth_client(account):
    """httpx AsyncClient against the ASGI app with auth overridden to `account`."""
    user = {"id": account["user_id"], "username": "tester",
            "email": "tester@example.com", "is_active": 1}

    def _override():
        return AuthContext(user, account)

    app.dependency_overrides[get_auth_context_with_api_key] = _override
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
```

- [ ] **Step 5: Write the harness smoke test**

Create `tests/test_harness.py`:

```python
from app.database import fetch_all


async def test_init_db_creates_core_tables():
    rows = await fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
    names = {r["name"] for r in rows}
    assert "users" in names
    assert "accounts" in names


async def test_account_fixture_seeds_account(account):
    rows = await fetch_all("SELECT id, user_id FROM accounts WHERE id = ?",
                           (account["id"],))
    assert len(rows) == 1
    assert rows[0]["user_id"] == account["user_id"]


async def test_auth_client_health(auth_client):
    resp = await auth_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}
```

- [ ] **Step 6: Install deps and run the harness**

Run: `pip install pytest==8.0.0 pytest-asyncio==0.23.5 && pytest tests/test_harness.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt pytest.ini tests/__init__.py tests/conftest.py tests/test_harness.py
git commit -m "test: add pytest harness with temp-db and auth fixtures"
```

---

### Task 2: Journal schema migration

**Files:**
- Modify: `app/database.py` (add `migrate_journal_tables`; call it inside `init_db`)
- Create: `tests/test_journal_schema.py`

**Interfaces:**
- Produces: four tables with these exact columns (later tasks depend on these names):
  - `mechanisms(id, account_id, name, criteria, regime, status, notes, created_at, updated_at)`
  - `tt_fills(id, account_id, external_id, order_id, underlying, option_symbol, option_type, strike, expiration, action, is_opening, quantity, price, fees, value, executed_at, trade_date, journal_trade_id, dismissed, created_at)` with `UNIQUE(account_id, external_id)`
  - `journal_trades(id, account_id, underlying, direction, strikes, expiration, dte_at_entry, is_0dte, entry_premium, exit_premium, quantity, fees_total, entry_at, exit_at, time_in_trade_seconds, realized_pnl, realized_pnl_pct, status, mechanism_id, is_system, conviction, why_entered, thesis_worked, exit_reason, emotional_state, reflection, regime_read, execution_discipline, long_stopped_then_reversed, short_loser_overran, mistake_tag, created_at, updated_at)`
  - `greek_snapshots(id, account_id, option_symbol, journal_trade_id, snapshot_type, delta, iv, captured_at)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_journal_schema.py`:

```python
from app.database import fetch_all, get_db


async def _columns(table):
    rows = await fetch_all(f"PRAGMA table_info({table})")
    return {r["name"] for r in rows}


async def test_journal_tables_exist():
    rows = await fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in rows}
    assert {"mechanisms", "tt_fills", "journal_trades", "greek_snapshots"} <= names


async def test_tt_fills_columns():
    cols = await _columns("tt_fills")
    assert {"external_id", "underlying", "option_symbol", "action",
            "is_opening", "value", "journal_trade_id", "dismissed"} <= cols


async def test_journal_trades_columns():
    cols = await _columns("journal_trades")
    assert {"direction", "realized_pnl", "status", "mechanism_id",
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_journal_schema.py -v`
Expected: FAIL (tables/columns do not exist).

- [ ] **Step 3: Add the migration function**

In `app/database.py`, add this function (place it near the other `migrate_*` functions, e.g. after `migrate_pool_tables`):

```python
async def migrate_journal_tables(db):
    """Create the rebuilt trade-journal tables (mechanisms, fills inbox,
    journal trades, greek snapshots). Idempotent."""
    await db.executescript("""
    CREATE TABLE IF NOT EXISTS mechanisms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        name TEXT NOT NULL,
        criteria TEXT,
        regime TEXT,
        status TEXT NOT NULL DEFAULT 'developing'
            CHECK (status IN ('developing', 'validated', 'retired')),
        notes TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_id, name)
    );
    CREATE INDEX IF NOT EXISTS idx_mechanisms_account ON mechanisms(account_id);

    CREATE TABLE IF NOT EXISTS journal_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        underlying TEXT,
        direction TEXT,
        strikes TEXT,
        expiration DATE,
        dte_at_entry INTEGER,
        is_0dte BOOLEAN DEFAULT 0,
        entry_premium REAL,
        exit_premium REAL,
        quantity INTEGER,
        fees_total REAL DEFAULT 0,
        entry_at DATETIME,
        exit_at DATETIME,
        time_in_trade_seconds INTEGER,
        realized_pnl REAL,
        realized_pnl_pct REAL,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'closed')),
        mechanism_id INTEGER REFERENCES mechanisms(id),
        is_system BOOLEAN,
        conviction INTEGER,
        why_entered TEXT,
        thesis_worked TEXT,
        exit_reason TEXT,
        emotional_state TEXT,
        reflection TEXT,
        regime_read TEXT,
        execution_discipline INTEGER,
        long_stopped_then_reversed BOOLEAN,
        short_loser_overran BOOLEAN,
        mistake_tag TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_journal_trades_account ON journal_trades(account_id);
    CREATE INDEX IF NOT EXISTS idx_journal_trades_mechanism ON journal_trades(mechanism_id);

    CREATE TABLE IF NOT EXISTS tt_fills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        external_id TEXT NOT NULL,
        order_id TEXT,
        underlying TEXT,
        option_symbol TEXT,
        option_type TEXT,
        strike REAL,
        expiration DATE,
        action TEXT,
        is_opening BOOLEAN,
        quantity INTEGER,
        price REAL,
        fees REAL DEFAULT 0,
        value REAL,
        executed_at DATETIME,
        trade_date DATE,
        journal_trade_id INTEGER REFERENCES journal_trades(id),
        dismissed BOOLEAN DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_id, external_id)
    );
    CREATE INDEX IF NOT EXISTS idx_tt_fills_account ON tt_fills(account_id);
    CREATE INDEX IF NOT EXISTS idx_tt_fills_ungrouped
        ON tt_fills(account_id, journal_trade_id, dismissed);

    CREATE TABLE IF NOT EXISTS greek_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        option_symbol TEXT NOT NULL,
        journal_trade_id INTEGER REFERENCES journal_trades(id),
        snapshot_type TEXT
            CHECK (snapshot_type IN ('entry', 'exit', 'interim')),
        delta REAL,
        iv REAL,
        captured_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_greek_snapshots_symbol
        ON greek_snapshots(account_id, option_symbol);
    """)
```

- [ ] **Step 4: Wire the migration into init_db**

In `app/database.py`, inside `init_db()`, after the `await migrate_pool_tables(db)` / `await db.commit()` block (around line 200-201), add:

```python
        # Create the rebuilt trade-journal tables
        await migrate_journal_tables(db)
        await db.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_journal_schema.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add app/database.py tests/test_journal_schema.py
git commit -m "feat: add journal schema (mechanisms, fills, journal_trades, greeks)"
```

---

### Task 3: Fills parsing + sync service

**Files:**
- Create: `app/services/fills_sync.py`
- Create: `tests/test_fills_sync.py`

**Interfaces:**
- Consumes: `app.services.tastytrade.tastytrade_client.get_all_transactions(account_number, start_date=None)` → `list[dict]`; DB helper `get_db`.
- Produces:
  - `parse_fill(txn: dict) -> Optional[dict]` — returns a normalized fill dict with keys `external_id, order_id, underlying, option_symbol, option_type, strike, expiration, action, is_opening, quantity, price, fees, value, executed_at, trade_date`; returns `None` for any non-option-trade transaction.
  - `async def store_fills(account_id: int, fills: list[dict]) -> dict` → `{"synced": int, "skipped": int}` (dedup on `(account_id, external_id)`).
  - `async def sync_fills(account_id: int, account_number: str, since_date: Optional[str] = None) -> dict` → `{"synced": int, "skipped": int}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fills_sync.py`:

```python
import pytest
from app.services import fills_sync
from app.services.fills_sync import parse_fill, store_fills, sync_fills
from app.database import fetch_all


def _opt_txn(**over):
    txn = {
        "id": 1001,
        "order-id": 55,
        "transaction-type": "Trade",
        "instrument-type": "Equity Option",
        "action": "Buy to Open",
        "symbol": "QQQ   260628C00480000",
        "underlying-symbol": "QQQ",
        "quantity": "1",
        "price": "0.40",
        "commission": "0.50",
        "clearing-fees": "0.10",
        "regulatory-fees": "0.04",
        "value": "40.0",
        "value-effect": "Debit",
        "executed-at": "2026-06-28T18:32:00.000+00:00",
        "transaction-date": "2026-06-28",
    }
    txn.update(over)
    return txn


def test_parse_fill_opening_option():
    p = parse_fill(_opt_txn())
    assert p["external_id"] == "1001"
    assert p["underlying"] == "QQQ"
    assert p["option_type"] == "call"
    assert p["strike"] == 480.0
    assert p["expiration"] == "2026-06-28"
    assert p["action"] == "BTO"
    assert p["is_opening"] is True
    assert p["quantity"] == 1
    assert p["price"] == 0.40
    assert round(p["fees"], 2) == 0.64
    # Debit -> negative signed value
    assert p["value"] == -40.0


def test_parse_fill_closing_credit_sign():
    # Note: 'value-effect' has a hyphen, so it can't be a kwarg — pass via **{}.
    p = parse_fill(_opt_txn(**{"id": 1002, "action": "Sell to Close",
                               "value": "55.0", "value-effect": "Credit"}))
    assert p["action"] == "STC"
    assert p["is_opening"] is False
    assert p["value"] == 55.0


def test_parse_fill_skips_money_movement():
    assert parse_fill({"id": 9, "transaction-type": "Money Movement",
                       "transaction-sub-type": "Deposit"}) is None


def test_parse_fill_skips_equity():
    assert parse_fill({"id": 10, "transaction-type": "Trade",
                       "instrument-type": "Equity", "action": "Buy to Open"}) is None


async def test_store_fills_inserts_and_dedups(account):
    p1 = parse_fill(_opt_txn(id=2001))
    res1 = await store_fills(account["id"], [p1])
    assert res1 == {"synced": 1, "skipped": 0}
    # Re-storing the same external_id is skipped
    res2 = await store_fills(account["id"], [p1])
    assert res2 == {"synced": 0, "skipped": 1}
    rows = await fetch_all("SELECT external_id, value FROM tt_fills WHERE account_id = ?",
                           (account["id"],))
    assert len(rows) == 1
    assert rows[0]["external_id"] == "2001"


async def test_sync_fills_pulls_and_stores(account, monkeypatch):
    txns = [
        _opt_txn(id=3001),
        _opt_txn(**{"id": 3002, "action": "Sell to Close",
                    "value": "60.0", "value-effect": "Credit"}),
        {"id": 3003, "transaction-type": "Money Movement",
         "transaction-sub-type": "Deposit"},
    ]

    async def fake_get_all(account_number, start_date=None):
        return txns

    monkeypatch.setattr(fills_sync.tastytrade_client, "get_all_transactions", fake_get_all)
    res = await sync_fills(account["id"], "5WT00001")
    assert res["synced"] == 2  # money movement skipped
    rows = await fetch_all("SELECT external_id FROM tt_fills WHERE account_id = ?",
                           (account["id"],))
    assert {r["external_id"] for r in rows} == {"3001", "3002"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fills_sync.py -v`
Expected: FAIL (`app.services.fills_sync` does not exist).

- [ ] **Step 3: Implement the service**

Create `app/services/fills_sync.py`:

```python
"""Tastytrade fills sync — pulls option-trade transactions into the
ungrouped `tt_fills` inbox. No auto-pairing: the trader groups fills
into journal trades manually."""
import logging
from datetime import date
from typing import Optional

from app.database import get_db
from app.services.tastytrade import tastytrade_client

logger = logging.getLogger(__name__)

_ACTION_MAP = {
    "Buy to Open":  ("BTO", True),
    "Sell to Open": ("STO", True),
    "Buy to Close": ("BTC", False),
    "Sell to Close": ("STC", False),
}


def _f(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_tt_symbol(symbol: str):
    """'QQQ   260628C00480000' -> (ticker, 'YYYY-MM-DD', 'call'/'put', strike)."""
    if not symbol or len(symbol) < 21:
        return None
    try:
        ticker = symbol[:6].rstrip()
        d = symbol[6:12]
        type_char = symbol[12]
        strike = int(symbol[13:21]) / 1000.0
        expiration = f"20{d[:2]}-{d[2:4]}-{d[4:6]}"
        option_type = "call" if type_char.upper() == "C" else "put"
        return ticker, expiration, option_type, strike
    except (ValueError, IndexError):
        return None


def parse_fill(txn: dict) -> Optional[dict]:
    """Normalize a raw TT transaction into a fill dict, or None to skip.
    Only option-trade fills (Buy/Sell to Open/Close) are kept."""
    if txn.get("transaction-type") != "Trade":
        return None
    if txn.get("instrument-type") != "Equity Option":
        return None

    action_info = _ACTION_MAP.get(txn.get("action", ""))
    if action_info is None:
        return None
    action, is_opening = action_info

    parsed_sym = _parse_tt_symbol(txn.get("symbol", ""))
    if not parsed_sym:
        return None
    ticker, expiration, option_type, strike = parsed_sym

    underlying = (txn.get("underlying-symbol") or ticker).upper()

    try:
        quantity = abs(int(float(txn.get("quantity", 0))))
    except (ValueError, TypeError):
        quantity = 0

    fees = ((_f(txn.get("commission")) or 0.0)
            + (_f(txn.get("clearing-fees")) or 0.0)
            + (_f(txn.get("regulatory-fees")) or 0.0))

    value_raw = _f(txn.get("value")) or 0.0
    value = value_raw if txn.get("value-effect") == "Credit" else -value_raw

    executed_at = txn.get("executed-at", "")
    trade_date = txn.get("transaction-date") or (executed_at[:10] if executed_at else str(date.today()))

    return {
        "external_id": str(txn.get("id", "")),
        "order_id": str(txn.get("order-id")) if txn.get("order-id") else None,
        "underlying": underlying,
        "option_symbol": txn.get("symbol", ""),
        "option_type": option_type,
        "strike": strike,
        "expiration": expiration,
        "action": action,
        "is_opening": is_opening,
        "quantity": quantity,
        "price": _f(txn.get("price")) or 0.0,
        "fees": round(fees, 4),
        "value": value,
        "executed_at": executed_at,
        "trade_date": trade_date,
    }


async def store_fills(account_id: int, fills: list[dict]) -> dict:
    """Insert parsed fills, deduping on (account_id, external_id)."""
    synced = 0
    skipped = 0
    async with get_db() as db:
        for f in fills:
            if not f:
                continue
            cur = await db.execute(
                "SELECT 1 FROM tt_fills WHERE account_id = ? AND external_id = ?",
                (account_id, f["external_id"]),
            )
            if await cur.fetchone():
                skipped += 1
                continue
            await db.execute(
                """INSERT INTO tt_fills
                   (account_id, external_id, order_id, underlying, option_symbol,
                    option_type, strike, expiration, action, is_opening, quantity,
                    price, fees, value, executed_at, trade_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (account_id, f["external_id"], f["order_id"], f["underlying"],
                 f["option_symbol"], f["option_type"], f["strike"], f["expiration"],
                 f["action"], f["is_opening"], f["quantity"], f["price"], f["fees"],
                 f["value"], f["executed_at"], f["trade_date"]),
            )
            synced += 1
        await db.commit()
    return {"synced": synced, "skipped": skipped}


async def sync_fills(account_id: int, account_number: str,
                     since_date: Optional[str] = None) -> dict:
    """Fetch TT transactions, parse option fills, store into the inbox."""
    txns = await tastytrade_client.get_all_transactions(account_number, start_date=since_date)
    parsed = [parse_fill(t) for t in (txns or [])]
    parsed = [p for p in parsed if p]
    result = await store_fills(account_id, parsed)
    logger.info("sync_fills account=%d synced=%d skipped=%d",
                account_id, result["synced"], result["skipped"])
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fills_sync.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/fills_sync.py tests/test_fills_sync.py
git commit -m "feat: fills sync service — parse TT option fills into inbox"
```

---

### Task 4: Fills inbox API

**Files:**
- Create: `app/routers/journal.py`
- Modify: `app/main.py` (import + register the new router)
- Create: `tests/test_journal_api.py`
- Modify: `API.md` (document the new endpoints)

**Interfaces:**
- Consumes: `get_auth_context_with_api_key`, `AuthContext`; `fills_sync.sync_fills`; `tastytrade_client.get_accounts`; DB helpers.
- Produces these endpoints (later plans add grouping endpoints to this same router):
  - `GET /api/journal/fills` → `{"fills": [ {id, underlying, option_symbol, option_type, strike, expiration, action, is_opening, quantity, price, value, executed_at, trade_date} ... ]}` — only ungrouped (`journal_trade_id IS NULL`), non-dismissed fills, ordered by `executed_at DESC`.
  - `POST /api/journal/fills/sync` → `{"synced": int, "skipped": int}` (resolves TT account number via `get_accounts`).
  - `POST /api/journal/fills/{fill_id}/dismiss` → `{"ok": true}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_journal_api.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_journal_api.py -v`
Expected: FAIL (`app.routers.journal` does not exist).

- [ ] **Step 3: Implement the router**

Create `app/routers/journal.py`:

```python
"""Trade journal router — fills inbox (grouping/mechanisms/analysis added later)."""
from fastapi import APIRouter, Depends, HTTPException

from app.database import fetch_all, get_db
from app.auth.dependencies import get_auth_context_with_api_key, AuthContext
from app.services.tastytrade import tastytrade_client
from app.services.fills_sync import sync_fills

router = APIRouter(prefix="/api/journal", tags=["journal"])


@router.get("/fills")
async def list_fills(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Ungrouped, non-dismissed fills — the inbox."""
    rows = await fetch_all(
        """SELECT id, external_id, order_id, underlying, option_symbol, option_type,
                  strike, expiration, action, is_opening, quantity, price, value,
                  executed_at, trade_date
           FROM tt_fills
           WHERE account_id = ? AND journal_trade_id IS NULL AND dismissed = 0
           ORDER BY executed_at DESC""",
        (auth.account_id,),
    )
    return {"fills": rows}


@router.post("/fills/sync")
async def sync_fills_endpoint(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Pull TT transactions into the inbox. Resolves the first TT account."""
    accounts = await tastytrade_client.get_accounts()
    if not accounts:
        raise HTTPException(status_code=400, detail="No Tastytrade account available")
    account_number = accounts[0].get("account-number")
    if not account_number:
        raise HTTPException(status_code=400, detail="Tastytrade account has no number")
    return await sync_fills(auth.account_id, account_number)


@router.post("/fills/{fill_id}/dismiss")
async def dismiss_fill(fill_id: int,
                       auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Mark a fill dismissed so it leaves the inbox without being grouped."""
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE tt_fills SET dismissed = 1 WHERE id = ? AND account_id = ?",
            (fill_id, auth.account_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Fill not found")
    return {"ok": True}
```

- [ ] **Step 4: Register the router in main.py**

In `app/main.py`, add `journal` to the routers import (line 12) and register it. Change the import line:

```python
from app.routers import trades, prices, cash, benchmarks, prospects, auth, watchlist, mirror, sync, analytics, pool, journal
```

And after `app.include_router(pool.router)` (line 64) add:

```python
app.include_router(journal.router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_journal_api.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: all tests pass (harness + schema + fills_sync + journal_api).

- [ ] **Step 7: Document the endpoints in API.md**

Append a "Trade Journal (rebuild)" section to `API.md` documenting the three endpoints above (method, path, auth = JWT or `X-API-Key`, response shape). Match the existing formatting style in that file.

- [ ] **Step 8: Commit**

```bash
git add app/routers/journal.py app/main.py tests/test_journal_api.py API.md
git commit -m "feat: fills inbox API (list, sync, dismiss)"
```

---

## Self-Review

**Spec coverage (Plan 1 scope only):**
- Test harness — Task 1. ✓
- New tables `mechanisms`, `tt_fills`, `journal_trades`, `greek_snapshots` — Task 2. ✓
- Fills sync into inbox, dedup on `external_id`, money-movements ignored — Task 3. ✓
- Inbox queryable + sync trigger + dismiss — Task 4. ✓
- Auth via `get_auth_context_with_api_key`, account-scoped — Tasks 4. ✓
- API.md updated — Task 4 Step 7. ✓
- Deferred to later plans (intentionally out of Plan 1 scope): grouping endpoints, mechanisms CRUD, delta poller, analysis views, equity-curve balance snapshot, demolition of old domain, Logbook frontend. Tracked in the Roadmap below.

**Placeholder scan:** No TBD/TODO; every code step contains full code. The TT key `value-effect` is hyphenated, so closing-txn tests pass it via `_opt_txn(**{"value-effect": "Credit"})` rather than as a keyword arg (a hyphen can't be a Python identifier).

**Type consistency:** `parse_fill` returns the key set consumed by `store_fills`'s INSERT; `sync_fills` returns `{"synced", "skipped"}` matching the API passthrough; column names in tests match the Task 2 schema. ✓

---

## Roadmap (subsequent plans — not part of this plan)

Each will get its own spec-aligned plan via writing-plans when we reach it:

- **Plan 2 — Grouping core + Mechanisms:** mechanisms CRUD; `POST /api/journal/trades` (group selected fill ids → `journal_trades`, derive auto fields, sum P&L from `value` − `fees`); attach-closes-to-open-trade; the 8 manual core fields; `calculations.py` for grouped P&L. Includes validation (≥1 opening fill, single underlying).
- **Plan 3 — Delta poller:** tight market-hours position poll → `greek_snapshots` (entry on first sight, exit on last-before-close); link to trades on grouping.
- **Plan 4 — Analysis + API + equity curve:** the 5 analysis views + discipline-streak; daily TT balance snapshot feeding the existing benchmark/equity-curve table.
- **Plan 5 — Demolition + Logbook frontend:** remove old domain (trades/strategy/analytics/pool/prospects/watchlist/cash UI, old `app.js`/`widgets.js`); update `main.py`; build the Logbook UI per the visual direction.
