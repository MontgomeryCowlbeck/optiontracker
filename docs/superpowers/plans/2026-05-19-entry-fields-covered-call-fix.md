# Entry Fields & Covered Call Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `entry_iv_rank` and `entry_delta` on all trades (backfill + forward), and fix covered call classification so short calls against owned shares are correctly labeled `covered_call` instead of `naked_call`.

**Architecture:** Three orthogonal fixes: (1) backfill existing entry fields via a new DB migration function, (2) stamp entry fields at trade creation time in the sync engine and price fetch job, (3) fix covered-call detection in both the sync engine and the historical reclassification migration. Frontend gains two new read-only display fields in trade detail.

**Tech Stack:** Python/FastAPI, SQLite/aiosqlite, vanilla JS

---

## Files

| File | Change |
|------|--------|
| `app/database.py` | Add `migrate_entry_fields()` + `migrate_reclassify_covered_calls()`, call both from `init_db()` |
| `app/services/tastytrade_sync.py` | Stamp `entry_iv_rank`/`entry_iv_percentile` in `handle_opening_fill()`; check stock positions in `create_naked_strategy_group()` |
| `app/routers/prices.py` | Stamp `entry_delta` on first snapshot write in `fetch_all_open_trade_prices()` |
| `app/static/js/app.js` | Add IVR at Entry + POP at Entry to `renderTradeInfo()` |
| `app/static/index.html` | Bump `app.js?v=` cache buster |

---

### Task 1: Backfill entry fields via DB migration

**Files:**
- Modify: `app/database.py`

- [ ] **Step 1: Add `migrate_entry_fields(db)` after the existing `migrate_pool_tables` function**

```python
async def migrate_entry_fields(db):
    """Backfill entry_iv_rank/entry_iv_percentile from iv_history and entry_delta from price_snapshots."""
    # IVR at entry — join iv_history on ticker + trade_date
    await db.execute("""
        UPDATE trades
        SET
            entry_iv_rank = (
                SELECT iv_rank FROM iv_history
                WHERE iv_history.ticker = trades.ticker
                  AND iv_history.record_date = trades.trade_date
                LIMIT 1
            ),
            entry_iv_percentile = (
                SELECT iv_percentile FROM iv_history
                WHERE iv_history.ticker = trades.ticker
                  AND iv_history.record_date = trades.trade_date
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
```

- [ ] **Step 2: Call `migrate_entry_fields` from `init_db()` after `migrate_pool_tables`**

In `init_db()`, find the block that ends with:
```python
        await migrate_pool_tables(db)
        await db.commit()
```

Add immediately after:
```python
        # Backfill entry_iv_rank, entry_iv_percentile, entry_delta
        await migrate_entry_fields(db)
        await db.commit()
```

- [ ] **Step 3: Commit**

```bash
git add app/database.py
git commit -m "feat: backfill entry_iv_rank and entry_delta via migration"
```

---

### Task 2: Stamp IVR at entry for new trades in sync engine

**Files:**
- Modify: `app/services/tastytrade_sync.py`

- [ ] **Step 1: Add IVR lookup after trade INSERT in `handle_opening_fill()`**

Find the block right after `trade_id = cursor.lastrowid` (around line 346). After that line add:

```python
        # Stamp IVR at entry if iv_history has data for this ticker/date
        cursor_ivr = await db.execute(
            """SELECT iv_rank, iv_percentile FROM iv_history
               WHERE ticker = ? AND record_date = ?
               LIMIT 1""",
            (ticker.upper(), trade_date),
        )
        ivr_row = await cursor_ivr.fetchone()
        if ivr_row and ivr_row[0] is not None:
            await db.execute(
                """UPDATE trades SET entry_iv_rank = ?, entry_iv_percentile = ?
                   WHERE id = ?""",
                (ivr_row[0], ivr_row[1], trade_id),
            )
```

- [ ] **Step 2: Verify the insertion is inside the `try` block**

The IVR lookup must be inside the same `try:` block as the INSERT, before the `result.trades_created += 1` line.

- [ ] **Step 3: Commit**

```bash
git add app/services/tastytrade_sync.py
git commit -m "feat: stamp entry_iv_rank at trade creation in sync engine"
```

---

### Task 3: Stamp `entry_delta` on first price snapshot write

**Files:**
- Modify: `app/routers/prices.py`

- [ ] **Step 1: After the batch snapshot INSERT in `fetch_all_open_trade_prices()`, add delta stamping**

Find the `async with get_db() as db:` block that does `await db.executemany(...)`. After the `await db.execute(...)` that prunes old snapshots (the tiered retention DELETE), and before `await db.commit()`, add:

```python
                # Stamp entry_delta on trades that haven't captured it yet
                for (trade_id, _, _, _, _, _, _, _, delta, *_) in snapshot_data:
                    if delta is not None:
                        await db.execute(
                            """UPDATE trades SET entry_delta = ?
                               WHERE id = ? AND entry_delta IS NULL""",
                            (delta, trade_id),
                        )
```

- [ ] **Step 2: Commit**

```bash
git add app/routers/prices.py
git commit -m "feat: stamp entry_delta on first price snapshot capture"
```

---

### Task 4: Fix covered call detection in `create_naked_strategy_group`

**Files:**
- Modify: `app/services/tastytrade_sync.py`

- [ ] **Step 1: Add `quantity` to the SELECT in `create_naked_strategy_group()`**

Find:
```python
    cursor = await db.execute(
        "SELECT id, ticker, option_type, action, strike, expiration, trade_date, strategy_group_id FROM trades WHERE id = ?",
        (trade_id,),
    )
```

Replace with:
```python
    cursor = await db.execute(
        "SELECT id, ticker, option_type, action, quantity, strike, expiration, trade_date, strategy_group_id FROM trades WHERE id = ?",
        (trade_id,),
    )
```

- [ ] **Step 2: Replace the hardcoded `strategy_type = f"naked_{option_type}"` with covered call check**

Find:
```python
    strategy_type = f"naked_{option_type}"
    ticker = trade["ticker"]
    trade_date = trade["trade_date"]
```

Replace with:
```python
    strategy_type = f"naked_{option_type}"
    ticker = trade["ticker"]
    trade_date = trade["trade_date"]

    # Detect covered call: short call where account owns >= 100 shares/contract
    if option_type == "call" and trade.get("action") == "sell":
        quantity = trade.get("quantity") or 1
        cursor_sp = await db.execute(
            """SELECT COALESCE(SUM(shares), 0)
               FROM stock_positions
               WHERE ticker = ?
                 AND account_id = ?
                 AND (sold_date IS NULL OR sold_date >= ?)""",
            (ticker, account_id, trade_date),
        )
        sp_row = await cursor_sp.fetchone()
        if sp_row and sp_row[0] >= quantity * 100:
            strategy_type = "covered_call"
```

- [ ] **Step 3: Commit**

```bash
git add app/services/tastytrade_sync.py
git commit -m "fix: detect covered calls in create_naked_strategy_group"
```

---

### Task 5: Fix covered call detection in `migrate_naked_strategies` and add reclassification migration

**Files:**
- Modify: `app/database.py`

- [ ] **Step 1: Fix `migrate_naked_strategies` to check stock positions for calls**

Find the line inside `for key, group_trades in groups.items():`:
```python
            ticker, strike, expiration, option_type, action, trade_date = key
            strategy_type = f"naked_{option_type}"
```

Replace with:
```python
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
```

- [ ] **Step 2: Add `migrate_reclassify_covered_calls(db)` function after `migrate_naked_strategies`**

```python
async def migrate_reclassify_covered_calls(db):
    """Reclassify existing naked_call strategy groups to covered_call where stock position exists."""
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
```

- [ ] **Step 3: Call `migrate_reclassify_covered_calls` from `init_db()` after `migrate_naked_strategies`**

Find:
```python
        await migrate_naked_strategies(db)
        await db.commit()
```

Replace with:
```python
        await migrate_naked_strategies(db)
        await db.commit()

        # Reclassify any naked_call groups that are actually covered calls
        await migrate_reclassify_covered_calls(db)
        await db.commit()
```

- [ ] **Step 4: Commit**

```bash
git add app/database.py
git commit -m "fix: detect and reclassify covered calls in strategy group migrations"
```

---

### Task 6: Display IVR at Entry and POP at Entry in trade detail

**Files:**
- Modify: `app/static/js/app.js`
- Modify: `app/static/index.html`

- [ ] **Step 1: Add IVR at Entry and POP at Entry to `renderTradeInfo()` in `app.js`**

Find the closing backtick of the `container.innerHTML = \`` block in `renderTradeInfo` (the line that has `</div>\n    \`;` just before `function renderPricing`). It ends with:

```javascript
        ${trade.strategy_group_id ? `
            <div class="detail-item">
                <div class="detail-label">Strategy</div>
                <div class="detail-value">
                    <span class="badge bg-purple" style="cursor: pointer;" onclick="showTrades('strategies')">
                        <i class="bi bi-layers me-1"></i>Leg ${trade.leg_number || '?'} of Strategy #${trade.strategy_group_id}
                    </span>
                </div>
            </div>
        ` : ''}
    `;
}
```

Replace with:

```javascript
        ${trade.strategy_group_id ? `
            <div class="detail-item">
                <div class="detail-label">Strategy</div>
                <div class="detail-value">
                    <span class="badge bg-purple" style="cursor: pointer;" onclick="showTrades('strategies')">
                        <i class="bi bi-layers me-1"></i>Leg ${trade.leg_number || '?'} of Strategy #${trade.strategy_group_id}
                    </span>
                </div>
            </div>
        ` : ''}
        ${trade.entry_iv_rank != null ? `
            <div class="detail-item">
                <div class="detail-label">IVR at Entry</div>
                <div class="detail-value">${trade.entry_iv_rank.toFixed(1)}%</div>
            </div>
        ` : ''}
        ${trade.entry_delta != null ? `
            <div class="detail-item">
                <div class="detail-label">POP at Entry</div>
                <div class="detail-value">
                    ${((trade.action === 'sell' ? 1 - Math.abs(trade.entry_delta) : Math.abs(trade.entry_delta)) * 100).toFixed(1)}%
                    <span class="text-muted small ms-1">(δ ${trade.entry_delta.toFixed(2)})</span>
                </div>
            </div>
        ` : ''}
    `;
}
```

- [ ] **Step 2: Bump `app.js` version in `index.html`**

Find `app.js?v=153` and change to `app.js?v=154`.

- [ ] **Step 3: Commit**

```bash
git add app/static/js/app.js app/static/index.html
git commit -m "feat: show IVR at entry and POP at entry in trade detail view"
```

---

### Task 7: Rebuild and verify

- [ ] **Step 1: Rebuild container**

```bash
docker compose build --no-cache && docker compose up -d
```

- [ ] **Step 2: Verify entry fields backfilled**

Open a trade that was opened on a date that has iv_history data. Trade detail should show IVR at Entry and POP at Entry.

- [ ] **Step 3: Verify covered call reclassification**

In Strategies tab, filter by "Covered Call" — if you own shares of any ticker you sold calls against, those should now appear here instead of in Naked Call.

- [ ] **Step 4: Commit**

```bash
git add data/options.db  # if you want to commit the migrated DB
git push origin initial_push
```
