import aiosqlite
import os
import logging
from contextlib import asynccontextmanager
from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    strike REAL NOT NULL,
    expiration DATE NOT NULL,
    option_type TEXT NOT NULL CHECK (option_type IN ('call', 'put')),
    action TEXT NOT NULL CHECK (action IN ('buy', 'sell')),
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    trade_date DATE NOT NULL,
    commission REAL DEFAULT 0,
    notes TEXT,
    closed_date DATE,
    closed_price REAL,
    occ_symbol TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    shares INTEGER NOT NULL,
    cost_basis REAL NOT NULL,
    acquired_date DATE NOT NULL,
    acquired_from_trade_id INTEGER REFERENCES trades(id),
    sold_date DATE,
    sold_price REAL,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    bid REAL,
    ask REAL,
    last REAL,
    underlying_price REAL,
    open_interest INTEGER,
    volume INTEGER,
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    rho REAL,
    implied_volatility REAL,
    FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cash_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_type TEXT NOT NULL CHECK (transaction_type IN (
        'deposit', 'withdrawal', 'premium_received', 'premium_paid',
        'trade_close', 'assignment', 'dividend', 'interest', 'fee', 'adjustment'
    )),
    amount REAL NOT NULL,
    balance_after REAL NOT NULL,
    description TEXT,
    trade_id INTEGER REFERENCES trades(id),
    transaction_date DATE NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    strike REAL NOT NULL,
    expiration DATE NOT NULL,
    option_type TEXT NOT NULL CHECK (option_type IN ('call', 'put')),
    action TEXT NOT NULL CHECK (action IN ('buy', 'sell')),
    target_entry_price REAL,
    notes TEXT,
    occ_symbol TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cash_transactions_date ON cash_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_cash_transactions_type ON cash_transactions(transaction_type);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_expiration ON trades(expiration);
CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades(closed_date);
CREATE INDEX IF NOT EXISTS idx_price_snapshots_trade ON price_snapshots(trade_id);
CREATE INDEX IF NOT EXISTS idx_price_snapshots_timestamp ON price_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_stock_positions_ticker ON stock_positions(ticker);
CREATE INDEX IF NOT EXISTS idx_prospects_ticker ON prospects(ticker);
CREATE INDEX IF NOT EXISTS idx_prospects_expiration ON prospects(expiration);

-- Performance optimization indexes
CREATE INDEX IF NOT EXISTS idx_price_snapshots_trade_timestamp ON price_snapshots(trade_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_cash_transactions_trade ON cash_transactions(trade_id);
"""

# Additional indexes for migrated columns
INDEXES_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_account_status ON trades(account_id, status);
CREATE INDEX IF NOT EXISTS idx_cash_transactions_account_type ON cash_transactions(account_id, transaction_type);
"""

# Auth tables schema
AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    is_default BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_accounts_default ON accounts(user_id, is_default);

-- Stock watchlist (simple ticker tracking)
CREATE TABLE IF NOT EXISTS stock_watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    ticker TEXT NOT NULL,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, ticker)
);
CREATE INDEX IF NOT EXISTS idx_stock_watchlist_account ON stock_watchlist(account_id);

-- API keys for machine-to-machine auth
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    key_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    last_used_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,
    is_active BOOLEAN DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_api_keys_account ON api_keys(account_id);
"""


async def init_db():
    """Initialize the database and create tables if they don't exist."""
    db_dir = os.path.dirname(settings.database_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    async with aiosqlite.connect(settings.database_path) as db:
        # Create tables if they don't exist
        await db.executescript(SCHEMA)
        await db.commit()

        # Create auth tables
        await db.executescript(AUTH_SCHEMA)
        await db.commit()

        # Migration: Add new columns if they don't exist
        await migrate_db(db)
        await db.commit()

        # Migrate to multi-account (add account_id columns)
        await migrate_to_multi_account(db)
        await db.commit()

        # Create indexes that depend on migrated columns
        await db.executescript(INDEXES_SCHEMA)
        await db.commit()

        # Migrate sync tables (Tastytrade API sync)
        await migrate_sync_tables(db)
        await db.commit()

        # Backfill naked put/call strategy groups for ungrouped single-leg trades
        await migrate_naked_strategies(db)
        await db.commit()

        # Reclassify any naked_call groups that are actually covered calls
        await migrate_reclassify_covered_calls(db)
        await db.commit()

        # Create stock pool and IV history tables
        await migrate_pool_tables(db)
        await db.commit()

        # Backfill entry_iv_rank, entry_iv_percentile, entry_delta
        await migrate_entry_fields(db)
        await db.commit()

        # Edge/mechanism vocabulary split: rename the old `mechanisms` table
        # (which held EDGES) before the base schema re-creates `mechanisms`
        # with its new per-structure meaning.
        await migrate_edges_split(db)
        await db.commit()

        # Create the rebuilt trade-journal tables
        await migrate_journal_tables(db)
        await db.commit()

        # Seed the per-structure mechanism playbooks for every account
        await seed_mechanisms(db)
        await db.commit()

        # Tags v2: free-form name + color (the fixed category taxonomy is gone)
        await migrate_tags_colors(db)
        await db.commit()

        # research_briefs gains kind='daily' (nightly AI cards) — CHECK rebuild
        await migrate_research_brief_kinds(db)
        await db.commit()


async def migrate_research_brief_kinds(db):
    """Existing DBs carry CHECK (kind IN ('brief','consult')); the nightly AI
    daily cards need 'daily'. SQLite can't alter a CHECK — rebuild in place,
    ids preserved."""
    cursor = await db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='research_briefs'")
    row = await cursor.fetchone()
    if not row or "'daily'" in (row[0] or ""):
        return
    await db.execute("""
        CREATE TABLE research_briefs_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            symbol TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('brief', 'consult', 'daily')),
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        INSERT INTO research_briefs_v2 (id, account_id, symbol, kind, content, created_at)
        SELECT id, account_id, symbol, kind, content, created_at FROM research_briefs
    """)
    await db.execute("DROP TABLE research_briefs")
    await db.execute("ALTER TABLE research_briefs_v2 RENAME TO research_briefs")


async def migrate_tags_colors(db):
    """Rebuild the tags table as name + color. Pre-existing tags keep the color
    their old category rendered as. Tag ids are preserved (trade_tags references
    them); duplicate names across old categories get a numeric suffix because
    uniqueness tightens from (account, category, name) to (account, name)."""
    cursor = await db.execute("PRAGMA table_info(tags)")
    cols = [row[1] for row in await cursor.fetchall()]
    if "color" in cols:
        return
    category_colors = {
        "setup": "#3987e5",
        "mistake": "#d03b3b",
        "emotion": "#d55181",
        "context": "#199e70",
    }
    await db.execute("""
        CREATE TABLE tags_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#3987e5',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id, name)
        )
    """)
    cursor = await db.execute(
        "SELECT id, account_id, category, name, created_at FROM tags ORDER BY id"
    )
    seen: set[tuple[int, str]] = set()
    for tag_id, account_id, category, name, created_at in await cursor.fetchall():
        final, n = name, 2
        while (account_id, final.lower()) in seen:
            final = f"{name}-{n}"
            n += 1
        seen.add((account_id, final.lower()))
        await db.execute(
            "INSERT INTO tags_v2 (id, account_id, name, color, created_at) VALUES (?, ?, ?, ?, ?)",
            (tag_id, account_id, final, category_colors.get(category, "#3987e5"), created_at),
        )
    await db.execute("DROP TABLE tags")
    await db.execute("ALTER TABLE tags_v2 RENAME TO tags")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tags_account ON tags(account_id)")


async def migrate_journal_tables(db):
    """Create the rebuilt trade-journal tables (mechanisms, fills inbox,
    journal trades, greek snapshots). Idempotent."""
    await db.executescript("""
    -- EDGES (the WHY): a named, testable hypothesis the trade expresses —
    -- Strategy #1, the Watchtower tiers, the strangle forward tests. Assigned
    -- by the trader (or the Strategy #1 auto-associator); drives journal debt
    -- and adherence. A structure is never an edge.
    CREATE TABLE IF NOT EXISTS edges (
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
    CREATE INDEX IF NOT EXISTS idx_edges_account ON edges(account_id);

    -- MECHANISMS (the HOW): one playbook entry per option STRUCTURE,
    -- auto-resolved from journal_trades.direction (the tracker's classifier) —
    -- never hand-assigned to a trade. An unseeded direction just renders
    -- without a playbook.
    CREATE TABLE IF NOT EXISTS mechanisms (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        structure TEXT NOT NULL,
        name TEXT NOT NULL,
        rules TEXT,
        notes TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_id, structure)
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
        edge_id INTEGER REFERENCES edges(id),
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
    CREATE INDEX IF NOT EXISTS idx_journal_trades_edge ON journal_trades(edge_id);

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
        theta REAL,
        captured_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_greek_snapshots_symbol
        ON greek_snapshots(account_id, option_symbol);

    CREATE TABLE IF NOT EXISTS journal_settings (
        account_id INTEGER PRIMARY KEY REFERENCES accounts(id),
        tt_account_number TEXT,
        sync_from_date TEXT,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS journal_day_notes (
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        date TEXT NOT NULL,
        note TEXT,
        ai_feedback TEXT,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (account_id, date)
    );

    CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        name TEXT NOT NULL,
        color TEXT NOT NULL DEFAULT '#3987e5',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_id, name)
    );
    CREATE INDEX IF NOT EXISTS idx_tags_account ON tags(account_id);

    CREATE TABLE IF NOT EXISTS trade_tags (
        trade_id INTEGER NOT NULL REFERENCES journal_trades(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        PRIMARY KEY (trade_id, tag_id)
    );
    CREATE INDEX IF NOT EXISTS idx_trade_tags_tag ON trade_tags(tag_id);

    -- Latest underlying spot per symbol, upserted by the greek poller. Feeds
    -- breakeven cushion / moneyness on the Positions tab; latest-only on purpose.
    CREATE TABLE IF NOT EXISTS underlying_marks (
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        symbol TEXT NOT NULL,
        price REAL NOT NULL,
        captured_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (account_id, symbol)
    );

    -- Vol watch: symbols marked for volatility tracking on the Strangles page.
    -- vol_snapshots accumulates a DAILY vol record per name — that history is
    -- what makes IV rank and IV-crush forward-testing possible without a
    -- vendor IV archive.
    CREATE TABLE IF NOT EXISTS vol_watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        symbol TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_id, symbol)
    );
    CREATE TABLE IF NOT EXISTS vol_snapshots (
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        spot REAL, iv REAL, rv20 REAL, iv_rv REAL,
        term_ratio REAL, straddle_pct REAL, earnings TEXT,
        PRIMARY KEY (account_id, symbol, date)
    );

    -- Pre-trade intents: the plan stated BEFORE the fill exists. The tracker
    -- binds a matching new trade to the oldest open intent (same underlying,
    -- compatible structure, <=7 days old) and pre-fills its journal fields —
    -- journaling becomes a confirmation, not a writing task.
    CREATE TABLE IF NOT EXISTS trade_intents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        underlying TEXT NOT NULL,
        direction TEXT,
        edge_id INTEGER REFERENCES edges(id),
        is_system INTEGER,
        planned_risk REAL,
        conviction INTEGER,
        note TEXT,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'bound', 'cancelled', 'expired')),
        bound_trade_id INTEGER REFERENCES journal_trades(id),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_trade_intents_account
        ON trade_intents(account_id, status);

    -- Research section: a personal watchlist (names outside Strategy #1 and the
    -- watchtower universe) with daily DD snapshots, notes, and saved AI output.
    CREATE TABLE IF NOT EXISTS research_watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        symbol TEXT NOT NULL,
        thesis TEXT,
        assignment_ok INTEGER,   -- 1 = happy to take shares, 0 = avoid, NULL = unstated
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_id, symbol)
    );
    CREATE TABLE IF NOT EXISTS research_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        price REAL, rsi14 REAL, rv20 REAL, atm_iv REAL, iv_rv REAL,
        support_lo REAL, support_hi REAL, support_dist_pct REAL,
        support_touches INTEGER,
        earnings TEXT,
        flags TEXT,              -- JSON list of active condition flags
        UNIQUE(account_id, symbol, date)
    );
    CREATE INDEX IF NOT EXISTS idx_research_snapshots
        ON research_snapshots(account_id, symbol, date);
    CREATE TABLE IF NOT EXISTS research_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        symbol TEXT NOT NULL,
        note TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS research_briefs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        symbol TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('brief', 'consult', 'daily')),
        content TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS consult_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        symbol TEXT NOT NULL,
        trade_id INTEGER REFERENCES journal_trades(id),
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS day_review_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        date TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS ai_chat_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        title TEXT NOT NULL,
        -- claude CLI conversation id: turn 2+ resumes it instead of rebuilding
        -- the journal context from scratch (NULL until the first turn lands,
        -- or again after a fallback rebuild)
        claude_session_id TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS ai_chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL REFERENCES ai_chat_sessions(id),
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # Add ai_feedback to journal_day_notes on pre-existing databases.
    cur = await db.execute("PRAGMA table_info(journal_day_notes)")
    day_note_cols = [r[1] for r in await cur.fetchall()]
    if "ai_feedback" not in day_note_cols:
        await db.execute("ALTER TABLE journal_day_notes ADD COLUMN ai_feedback TEXT")

    # planned_risk: the dollar risk accepted at entry — the R in R-multiples.
    cur = await db.execute("PRAGMA table_info(journal_trades)")
    jt_cols = [r[1] for r in await cur.fetchall()]
    if "planned_risk" not in jt_cols:
        await db.execute("ALTER TABLE journal_trades ADD COLUMN planned_risk REAL")
    # assignment_intent: a cash-secured put sold WANTING the shares — assignment
    # is a fill, not a loss. Flips the risk framing to capital-committed
    # (strike x 100 x contracts) and relaxes the journal-debt "risk" bar.
    if "assignment_intent" not in jt_cols:
        await db.execute("ALTER TABLE journal_trades ADD COLUMN assignment_intent INTEGER")
    # no_edge: EXPLICITLY declared edge-less (a one-off, not an unfilled field).
    # Blank edge = journal debt; no_edge = a complete, honest record.
    if "no_edge" not in jt_cols:
        await db.execute("ALTER TABLE journal_trades ADD COLUMN no_edge INTEGER")

    # mark: live mid (fallback last) captured with each greek snapshot, so open
    # trades can be valued between fills.
    cur = await db.execute("PRAGMA table_info(greek_snapshots)")
    gs_cols = [r[1] for r in await cur.fetchall()]
    if "mark" not in gs_cols:
        await db.execute("ALTER TABLE greek_snapshots ADD COLUMN mark REAL")
    # theta: daily decay per contract — the stat a premium seller watches.
    if "theta" not in gs_cols:
        await db.execute("ALTER TABLE greek_snapshots ADD COLUMN theta REAL")


async def migrate_edges_split(db):
    """Vocabulary split (2026-07-26): the old `mechanisms` table actually held
    EDGES — named hypotheses the trader assigns (Strategy #1, Watchtower tiers,
    strangle forward tests). Rename it to `edges`; `mechanisms` now means the
    auto-detected option STRUCTURE's playbook (resolved from
    journal_trades.direction, never hand-assigned). Idempotent; row ids are
    preserved so trade/intent FKs follow the rename."""
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('mechanisms', 'edges', 'journal_trades', 'trade_intents')")
    tables = {r[0] for r in await cur.fetchall()}
    if "journal_trades" not in tables:
        return  # fresh DB — the base schema creates the new layout directly
    cur = await db.execute("PRAGMA table_info(journal_trades)")
    jt_cols = [r[1] for r in await cur.fetchall()]
    if "mechanism_id" not in jt_cols:
        return  # already migrated
    if "mechanisms" in tables and "edges" not in tables:
        await db.execute("ALTER TABLE mechanisms RENAME TO edges")
        # the account index followed the rename under its old name
        await db.execute("DROP INDEX IF EXISTS idx_mechanisms_account")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_edges_account ON edges(account_id)")
    await db.execute(
        "ALTER TABLE journal_trades RENAME COLUMN mechanism_id TO edge_id")
    await db.execute("DROP INDEX IF EXISTS idx_journal_trades_mechanism")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_journal_trades_edge ON journal_trades(edge_id)")
    if "trade_intents" in tables:
        cur = await db.execute("PRAGMA table_info(trade_intents)")
        if "mechanism_id" in [r[1] for r in await cur.fetchall()]:
            await db.execute(
                "ALTER TABLE trade_intents RENAME COLUMN mechanism_id TO edge_id")


# Per-structure playbooks, keyed by the classifier's direction vocabulary
# (calculations.classify_strategy + the covered-call reclassifier). Rules text
# is seeded from the research record; the trader can edit it in the app —
# INSERT OR IGNORE means edits always win over seeds.
MECHANISM_SEEDS = [
    ("short_put", "Short put",
     "Cash-secured or naked put. 25-50 DTE. Strike below a defended level "
     "(Watchtower) or at a price you WANT the shares (set assignment_intent). "
     "50% profit target GTC; no stop — assignment or keeping the premium IS "
     "the plan, so never sell a strike you can't own. Risk = strike x 100 per "
     "contract when assignment-intended; otherwise count max loss against the "
     "10%-of-account ceiling."),
    ("short_call", "Naked call",
     "Undefined upside risk and the thin side of VRP (research: calls don't "
     "pay — the condor call wing was rejected as a premium trap). Should "
     "almost always be a covered call instead; an uncovered one needs an "
     "explicit bearish thesis and sizing that survives the gap."),
    ("covered_call", "Covered call",
     "Calls against held shares. Research (2022-26 study): NEGATIVE vs "
     "buy-hold in a trending bull — the calm filter helps by trading LESS, "
     "not via a per-trade edge. Strike above your let-them-go price; rolling "
     "up-and-out defends premium, not upside. Auto-reclassified from "
     "short_call when the shares are visible."),
    ("put_credit_spread", "Put credit spread",
     "Strategy #1 construction: 45 DTE, risk-budgeted barbell — 0.10 delta / "
     "$5 low rung + 0.45 delta / $3 high rung (avg ~0.25 delta). 50% profit "
     "target GTC, NO stop (the long leg IS the stop), no time stop. Hard "
     "10%-of-account defined-risk ceiling. ~$2.51/spread round-trip live "
     "cost. Large-cap index only (SPY/QQQ/DIA)."),
    ("call_credit_spread", "Call credit spread",
     "The rejected condor call wing on its own: VRP is asymmetric and does "
     "not pay the call side (Phase 2). Needs a specific bearish thesis — it "
     "is not premium harvesting."),
    ("short_strangle", "Short strangle",
     "FORWARD TEST ONLY — the backtest evidence says not deployable "
     "(Strategy #2 research: VRP-z entry died under deflation, holdout "
     "failed; asymmetry + wings-for-survivability are what survived). "
     "ETF-only per the 2026-07-26 decision. Log VRP-z at entry and tag one "
     "of the two strangle edges (TT mechanical vs VRP thesis)."),
    ("short_straddle", "Short straddle",
     "Max-premium, max-gamma version of the strangle — same forward-test-only "
     "status, less room to be wrong. Needs the same VRP-z log + edge tag."),
    ("iron_condor", "Iron condor",
     "REJECTED by Phase 2 research: the call wing is a premium trap (VRP is "
     "asymmetric — puts pay and drift helps; calls do neither). If one is in "
     "the book it needs a story."),
    ("long_call", "Long call",
     "Directional debit bet — negative theta, the opposite of the premium "
     "business. Needs a catalyst thesis and a loss budget (the debit is the "
     "stop). Long-dated only (FIG-style), never a lotto."),
    ("long_put", "Long put",
     "Directional or hedge debit bet — negative theta. As a hedge, journal "
     "WHAT it protects and its cost as insurance; as a standalone bet it is "
     "usually a declared no-edge one-off."),
]


async def seed_mechanisms_for_account(db, account_id):
    """INSERT OR IGNORE the standard playbooks for one account — user edits
    always survive (UNIQUE(account_id, structure)); directions outside the
    seed list simply render without a playbook until one is added in-app."""
    for structure, name, rules in MECHANISM_SEEDS:
        await db.execute(
            "INSERT OR IGNORE INTO mechanisms (account_id, structure, name, rules) "
            "VALUES (?, ?, ?, ?)",
            (account_id, structure, name, rules))


async def seed_mechanisms(db):
    """Seed every existing account's playbooks (startup). Accounts created
    later are lazily seeded by GET /journal/mechanisms."""
    cur = await db.execute("SELECT id FROM accounts")
    for (account_id,) in await cur.fetchall():
        await seed_mechanisms_for_account(db, account_id)


async def migrate_db(db):
    """Run migrations to add missing columns to existing tables."""
    cursor = await db.execute("PRAGMA table_info(trades)")
    columns = await cursor.fetchall()
    column_names = [col[1] for col in columns]

    # Add status column if missing
    if 'status' not in column_names:
        await db.execute(
            "ALTER TABLE trades ADD COLUMN status TEXT DEFAULT 'open' CHECK (status IN ('open', 'closed', 'assigned', 'expired'))"
        )
        await db.execute("UPDATE trades SET status = 'closed' WHERE closed_date IS NOT NULL")
    else:
        # Fix any trades that have closed_date but status is still 'open'
        await db.execute("UPDATE trades SET status = 'closed' WHERE closed_date IS NOT NULL AND status = 'open'")
        # Migrate any 'rolled' trades to 'closed' (roll feature removed)
        await db.execute("UPDATE trades SET status = 'closed' WHERE status = 'rolled'")

    # Add underlying_price column if missing
    if 'underlying_price' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN underlying_price REAL")

    # Add profit_target column if missing
    if 'profit_target' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN profit_target INTEGER")

    # === JOURNALING FIELDS ===
    if 'pre_trade_notes' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN pre_trade_notes TEXT")
    if 'post_trade_notes' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN post_trade_notes TEXT")
    if 'trade_grade' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN trade_grade TEXT CHECK (trade_grade IN ('A', 'B', 'C', 'D', 'F'))")

    # === ENTRY GREEKS ===
    if 'entry_delta' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN entry_delta REAL")
    if 'entry_theta' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN entry_theta REAL")
    if 'entry_vega' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN entry_vega REAL")
    if 'entry_gamma' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN entry_gamma REAL")

    # === IV ANALYSIS ===
    if 'entry_iv' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN entry_iv REAL")
    if 'entry_iv_rank' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN entry_iv_rank REAL")
    if 'entry_iv_percentile' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN entry_iv_percentile REAL")

    # === POSITION SIZING ===
    if 'position_size_pct' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN position_size_pct REAL")

    # === ROLL TRACKING ===
    if 'roll_count' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN roll_count INTEGER DEFAULT 0")

    # === STOCK POSITIONS MIGRATION: effective_cost_basis ===
    cursor = await db.execute("PRAGMA table_info(stock_positions)")
    stock_columns = await cursor.fetchall()
    stock_column_names = [col[1] for col in stock_columns]

    if 'effective_cost_basis' not in stock_column_names:
        await db.execute("ALTER TABLE stock_positions ADD COLUMN effective_cost_basis REAL")
        # Backfill effective_cost_basis for existing assigned positions
        # effective_basis = strike - (premium / shares)
        await db.execute("""
            UPDATE stock_positions
            SET effective_cost_basis = (
                SELECT sp2.cost_basis - (t.price * t.quantity * 100 / sp2.shares)
                FROM stock_positions sp2
                JOIN trades t ON t.id = sp2.acquired_from_trade_id
                WHERE sp2.id = stock_positions.id
            )
            WHERE acquired_from_trade_id IS NOT NULL
              AND effective_cost_basis IS NULL
        """)

    # === PLANNED EXIT ===
    if 'planned_exit_price' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN planned_exit_price REAL")
    if 'planned_exit_dte' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN planned_exit_dte INTEGER")

    # === MULTI-LEG STRATEGY ===
    if 'strategy_group_id' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN strategy_group_id INTEGER")
    if 'leg_number' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN leg_number INTEGER")

    # === CREATE NEW TABLES ===

    # Strategy Groups table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS strategy_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            strategy_type TEXT NOT NULL,
            underlying_ticker TEXT NOT NULL,
            opened_date DATE NOT NULL,
            closed_date DATE,
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Trade Attachments table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS trade_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            attachment_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            mime_type TEXT,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (trade_id) REFERENCES trades(id) ON DELETE CASCADE
        )
    """)

    # Benchmark Snapshots table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date DATE NOT NULL UNIQUE,
            spy_price REAL NOT NULL,
            portfolio_value REAL NOT NULL,
            cash_balance REAL NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === PROSPECTS MIGRATIONS ===
    cursor = await db.execute("PRAGMA table_info(prospects)")
    prospect_columns = await cursor.fetchall()
    prospect_column_names = [col[1] for col in prospect_columns]

    if 'quantity' not in prospect_column_names:
        await db.execute("ALTER TABLE prospects ADD COLUMN quantity INTEGER DEFAULT 1")

    # Add strategy_group_id and leg_number for multi-leg prospects
    if 'strategy_group_id' not in prospect_column_names:
        await db.execute("ALTER TABLE prospects ADD COLUMN strategy_group_id INTEGER REFERENCES prospect_strategy_groups(id)")

    if 'leg_number' not in prospect_column_names:
        await db.execute("ALTER TABLE prospects ADD COLUMN leg_number INTEGER")

    # Prospect Strategy Groups table (for multi-leg prospect strategies)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS prospect_strategy_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id),
            name TEXT,
            strategy_type TEXT NOT NULL,
            underlying_ticker TEXT NOT NULL,
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create indexes for new tables
    await db.execute("CREATE INDEX IF NOT EXISTS idx_strategy_groups_ticker ON strategy_groups(underlying_ticker)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_trade_attachments_trade ON trade_attachments(trade_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_benchmark_snapshots_date ON benchmark_snapshots(snapshot_date)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_strategy_group ON trades(strategy_group_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_prospect_strategy_groups_ticker ON prospect_strategy_groups(underlying_ticker)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_prospect_strategy_groups_account ON prospect_strategy_groups(account_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_prospects_strategy_group ON prospects(strategy_group_id)")

    # === USER PREFERENCES TABLE ===
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            account_id INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
            pref_key TEXT NOT NULL,
            pref_value TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, account_id, pref_key)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_user_prefs_lookup ON user_preferences(user_id, account_id, pref_key)")

    # === FIX NULL ACCOUNT_ID ON STOCK POSITIONS ===
    # Stock positions from assignments may have NULL account_id if created before the fix.
    # Inherit account_id from the originating trade.
    # Guard: only run if both tables already have the account_id column (added by migrate_to_multi_account).
    cursor = await db.execute("PRAGMA table_info(trades)")
    _trades_cols = [c[1] for c in await cursor.fetchall()]
    cursor = await db.execute("PRAGMA table_info(stock_positions)")
    _sp_cols = [c[1] for c in await cursor.fetchall()]
    if 'account_id' in _trades_cols and 'account_id' in _sp_cols:
        await db.execute("""
            UPDATE stock_positions
            SET account_id = (
                SELECT t.account_id
                FROM trades t
                WHERE t.id = stock_positions.acquired_from_trade_id
            )
            WHERE account_id IS NULL
              AND acquired_from_trade_id IS NOT NULL
        """)

    # === FIX BENCHMARK SNAPSHOT DATES ===
    # Snapshots taken after midnight but before market open should be labeled with
    # the previous trading day, not the calendar date when created.
    # This migration shifts snapshot_date back by 1 day for snapshots created between
    # midnight and 9:30am ET on their creation date.
    # Skip rows where shifting would collide with an existing snapshot.
    await db.execute("""
        UPDATE benchmark_snapshots
        SET snapshot_date = date(snapshot_date, '-1 day')
        WHERE time(created_at) < '14:30:00'
          AND snapshot_date = date(created_at)
          AND NOT EXISTS (
              SELECT 1 FROM benchmark_snapshots b2
              WHERE b2.snapshot_date = date(benchmark_snapshots.snapshot_date, '-1 day')
                AND b2.id != benchmark_snapshots.id
          )
    """)


async def migrate_to_multi_account(db):
    """
    Migrate existing data to multi-account support.
    - Add account_id column to relevant tables
    - Create default user/account for existing data
    """
    from app.auth.security import get_password_hash

    # Check if users table has any rows
    cursor = await db.execute("SELECT COUNT(*) as count FROM users")
    user_count = (await cursor.fetchone())[0]

    # Check if trades exist (to determine if we need to migrate existing data)
    cursor = await db.execute("SELECT COUNT(*) as count FROM trades")
    trade_count = (await cursor.fetchone())[0]

    # Tables that need account_id
    tables_to_migrate = [
        'trades', 'stock_positions', 'cash_transactions',
        'prospects', 'benchmark_snapshots', 'strategy_groups'
    ]

    # Add account_id column to tables that don't have it
    for table in tables_to_migrate:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]

        if 'account_id' not in column_names:
            logger.info(f"Adding account_id column to {table}")
            await db.execute(f"ALTER TABLE {table} ADD COLUMN account_id INTEGER REFERENCES accounts(id)")

    # Create indexes for account_id
    await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_account ON trades(account_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_stock_positions_account ON stock_positions(account_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_cash_transactions_account ON cash_transactions(account_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_prospects_account ON prospects(account_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_benchmark_snapshots_account ON benchmark_snapshots(account_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_strategy_groups_account ON strategy_groups(account_id)")

    # Compound indexes for common query patterns (account + filter)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_account_ticker ON trades(account_id, ticker)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_trades_account_closed ON trades(account_id, closed_date)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_cash_transactions_account_date ON cash_transactions(account_id, transaction_date DESC)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_benchmark_snapshots_account_date ON benchmark_snapshots(account_id, snapshot_date DESC)")

    # If no users exist, create default user and account
    if user_count == 0:
        logger.info("Creating default user and account for existing data migration")

        # Create default user
        hashed_pw = get_password_hash("changeme123")
        cursor = await db.execute(
            """INSERT INTO users (username, email, hashed_password, is_active)
               VALUES (?, ?, ?, 1)""",
            ("admin", "admin@localhost", hashed_pw)
        )
        user_id = cursor.lastrowid

        # Create default account
        cursor = await db.execute(
            """INSERT INTO accounts (user_id, name, description, is_default)
               VALUES (?, ?, ?, 1)""",
            (user_id, "Default Account", "Auto-created during migration")
        )
        account_id = cursor.lastrowid

        # Update all existing rows with default account_id
        for table in tables_to_migrate:
            await db.execute(
                f"UPDATE {table} SET account_id = ? WHERE account_id IS NULL",
                (account_id,)
            )
            logger.info(f"Updated {table} with default account_id")

        logger.info(f"Migration complete: Created user 'admin' (password: changeme123) with default account")


async def migrate_sync_tables(db):
    """Migrate tables for Tastytrade API sync support."""
    # --- trades table ---
    cursor = await db.execute("PRAGMA table_info(trades)")
    columns = await cursor.fetchall()
    column_names = [col[1] for col in columns]

    if 'external_id' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN external_id TEXT")
    if 'sync_source' not in column_names:
        await db.execute("ALTER TABLE trades ADD COLUMN sync_source TEXT DEFAULT 'manual'")

    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_external_id
        ON trades(account_id, external_id) WHERE external_id IS NOT NULL
    """)

    # --- cash_transactions table ---
    cursor = await db.execute("PRAGMA table_info(cash_transactions)")
    columns = await cursor.fetchall()
    ct_column_names = [col[1] for col in columns]

    if 'external_id' not in ct_column_names:
        await db.execute("ALTER TABLE cash_transactions ADD COLUMN external_id TEXT")
    if 'sync_source' not in ct_column_names:
        await db.execute("ALTER TABLE cash_transactions ADD COLUMN sync_source TEXT DEFAULT 'manual'")

    await db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cash_external_id
        ON cash_transactions(account_id, external_id) WHERE external_id IS NOT NULL
    """)

    # --- tastytrade_sync_state table ---
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tastytrade_sync_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            tastytrade_account_number TEXT,
            last_sync_at DATETIME,
            sync_from_date DATE,
            auto_sync_enabled BOOLEAN DEFAULT 1,
            sync_status TEXT DEFAULT 'idle' CHECK (sync_status IN ('idle', 'running', 'error')),
            last_sync_error TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id)
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_sync_state_account
        ON tastytrade_sync_state(account_id)
    """)

    # --- strategy_groups table ---
    cursor = await db.execute("PRAGMA table_info(strategy_groups)")
    columns = await cursor.fetchall()
    sg_column_names = [col[1] for col in columns]

    if 'stop_loss' not in sg_column_names:
        await db.execute("ALTER TABLE strategy_groups ADD COLUMN stop_loss REAL")

    # --- naked_put / naked_call strategy type support ---
    # Add naked_put and naked_call as valid strategy types (SQLite CHECK constraints
    # aren't enforced retroactively, so this is a no-op on the constraint; it just
    # documents intent and future rows will be accepted by the app-level enum).
    pass


@asynccontextmanager
async def get_db():
    """Get a database connection context manager."""
    db = await aiosqlite.connect(settings.database_path)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def execute_query(query: str, params: tuple = ()):
    """Execute a query and return the cursor."""
    async with get_db() as db:
        cursor = await db.execute(query, params)
        await db.commit()
        return cursor


async def fetch_one(query: str, params: tuple = ()):
    """Fetch a single row."""
    async with get_db() as db:
        cursor = await db.execute(query, params)
        row = await cursor.fetchone()
        return dict(row) if row else None


async def fetch_all(query: str, params: tuple = ()):
    """Fetch all rows."""
    async with get_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_owned_resource(table: str, resource_id: int, account_id: int, columns: str = "*"):
    """
    Fetch resource if owned by account, raise 404 if not found.

    This consolidates the common pattern of ownership verification across routers.
    """
    from fastapi import HTTPException
    result = await fetch_one(
        f"SELECT {columns} FROM {table} WHERE id = ? AND account_id = ?",
        (resource_id, account_id)
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"{table.replace('_', ' ').title()} not found")
    return result


async def migrate_pool_tables(db):
    """Create stock pool and IV history tables."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS pool_tickers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            ticker TEXT NOT NULL COLLATE NOCASE,
            description TEXT,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id, ticker)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS iv_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL COLLATE NOCASE,
            record_date DATE NOT NULL,
            iv_rank REAL,
            iv_percentile REAL,
            iv_30day REAL,
            hv_30day REAL,
            underlying_price REAL,
            vix_level REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ticker, record_date)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_pool_tickers_account ON pool_tickers(account_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_iv_history_ticker_date ON iv_history(ticker, record_date DESC)")


async def migrate_entry_fields(db):
    """Backfill entry_iv_rank/entry_iv_percentile from iv_history and entry_delta from price_snapshots."""
    # IVR at entry — join iv_history on ticker + trade_date
    await db.execute("""
        UPDATE trades
        SET
            entry_iv_rank = (
                SELECT iv_rank FROM iv_history
                WHERE iv_history.ticker = trades.ticker
                  AND DATE(iv_history.record_date) = DATE(trades.trade_date)
                LIMIT 1
            ),
            entry_iv_percentile = (
                SELECT iv_percentile FROM iv_history
                WHERE iv_history.ticker = trades.ticker
                  AND DATE(iv_history.record_date) = DATE(trades.trade_date)
                LIMIT 1
            )
        WHERE entry_iv_rank IS NULL
          AND option_type IS NOT NULL
    """)

    # Delta at entry — earliest price snapshot for each trade
    await db.execute("""
        UPDATE trades
        SET entry_delta = (
            SELECT delta FROM price_snapshots
            WHERE price_snapshots.trade_id = trades.id
              AND delta IS NOT NULL
            ORDER BY timestamp ASC
            LIMIT 1
        )
        WHERE entry_delta IS NULL
          AND option_type IS NOT NULL
    """)
    logger.info("migrate_entry_fields: entry_iv_rank and entry_delta backfill complete")


async def migrate_naked_strategies(db):
    """Create strategy groups for ungrouped single-leg option trades (naked puts/calls).

    Groups same-ticker/strike/exp/action/date fills together so that e.g.
    two qty-1 fills of the same option on the same day become one group.
    Idempotent — skips trades that already have strategy_group_id set.
    """
    import logging
    from collections import defaultdict
    logger = logging.getLogger(__name__)

    cursor = await db.execute(
        "SELECT DISTINCT account_id FROM trades WHERE account_id IS NOT NULL"
    )
    account_ids = [row[0] async for row in cursor]

    for account_id in account_ids:
        cursor = await db.execute(
            """SELECT id, ticker, strike, expiration, option_type, action,
                      quantity, price, trade_date, status, closed_date
               FROM trades
               WHERE strategy_group_id IS NULL
                 AND option_type IS NOT NULL
                 AND account_id = ?
               ORDER BY trade_date, ticker, expiration, strike, option_type, action""",
            (account_id,),
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        trades = [dict(zip(cols, r)) for r in rows]

        if not trades:
            continue

        # Group fills that represent the same position opened on the same day
        groups: dict = defaultdict(list)
        for t in trades:
            key = (
                t["ticker"], float(t["strike"]), t["expiration"],
                t["option_type"], t["action"], t["trade_date"],
            )
            groups[key].append(t)

        created = 0
        for key, group_trades in groups.items():
            ticker, strike, expiration, option_type, action, trade_date = key
            strategy_type = f"naked_{option_type}"

            # Detect covered call: short call + stock position held at open date
            if option_type == "call" and action == "sell":
                total_qty = sum(t["quantity"] for t in group_trades)
                cursor_sp = await db.execute(
                    """SELECT COALESCE(SUM(shares), 0)
                       FROM stock_positions
                       WHERE ticker = ?
                         AND account_id = ?
                         AND (sold_date IS NULL OR sold_date >= ?)""",
                    (ticker, account_id, trade_date),
                )
                sp_row = await cursor_sp.fetchone()
                if sp_row and sp_row[0] >= total_qty * 100:
                    strategy_type = "covered_call"

            all_terminal = all(
                t["status"] in ("closed", "expired", "assigned") for t in group_trades
            )
            closed_date = None
            if all_terminal:
                closed_dates = [t["closed_date"] for t in group_trades if t["closed_date"]]
                closed_date = max(closed_dates) if closed_dates else trade_date

            cur2 = await db.execute(
                """INSERT INTO strategy_groups
                   (account_id, name, strategy_type, underlying_ticker,
                    opened_date, closed_date, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    account_id,
                    f"{ticker} {option_type.title()} {strike} {expiration}",
                    strategy_type,
                    ticker,
                    trade_date,
                    closed_date,
                    "Auto-created from ungrouped option trade",
                ),
            )
            group_id = cur2.lastrowid

            for i, t in enumerate(group_trades, start=1):
                await db.execute(
                    "UPDATE trades SET strategy_group_id = ?, leg_number = ? WHERE id = ?",
                    (group_id, i, t["id"]),
                )
            created += 1

        if created:
            logger.info(
                "migrate_naked_strategies: created %d naked strategy groups for account %d",
                created, account_id,
            )


async def migrate_reclassify_covered_calls(db):
    """Reclassify existing naked_call strategy groups to covered_call where stock position exists."""
    import logging
    logger = logging.getLogger(__name__)

    cursor = await db.execute("""
        SELECT sg.id, sg.account_id, sg.underlying_ticker, sg.opened_date,
               COALESCE(SUM(t.quantity), 1) AS total_qty
        FROM strategy_groups sg
        JOIN trades t ON t.strategy_group_id = sg.id
            AND t.action = 'sell' AND t.option_type = 'call'
        WHERE sg.strategy_type = 'naked_call'
        GROUP BY sg.id
    """)
    rows = await cursor.fetchall()

    reclassified = 0
    for row in rows:
        sg_id, account_id, ticker, opened_date, total_qty = row
        shares_needed = (total_qty or 1) * 100

        cursor_sp = await db.execute(
            """SELECT COALESCE(SUM(shares), 0)
               FROM stock_positions
               WHERE ticker = ?
                 AND account_id = ?
                 AND (sold_date IS NULL OR sold_date >= ?)""",
            (ticker, account_id, opened_date),
        )
        sp_row = await cursor_sp.fetchone()
        if sp_row and sp_row[0] >= shares_needed:
            await db.execute(
                "UPDATE strategy_groups SET strategy_type = 'covered_call' WHERE id = ?",
                (sg_id,),
            )
            reclassified += 1

    if reclassified:
        logger.info("migrate_reclassify_covered_calls: reclassified %d strategy groups to covered_call", reclassified)
