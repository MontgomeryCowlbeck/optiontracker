# Trade Journal — Delta Poller (Plan 3)

> Executed inline. TDD per task, Docker-run suite, one commit per task.

**Goal:** Capture live entry/exit greeks (delta, IV) for option positions during market hours, store them in `greek_snapshots`, link them to journal trades on grouping, and surface them on trade detail.

**Architecture:** A stateful poller (`greek_poller.poll_greeks_once`) compares TT's currently-open option positions against the set of symbols that have an `entry` snapshot but no `exit` (the "active" set, derived from the DB — no in-memory state). First sight → `entry`; still open → `interim`; disappeared → `exit` (copying the last known greeks, since the contract may be gone). Snapshots store the TT-format `option_symbol` so they match `tt_fills.option_symbol`; on grouping, matching snapshots are linked to the trade.

## Global Constraints
- DB only via helpers; endpoints `get_auth_context_with_api_key` + account-scoped; tests under `tests/`; additive.
- Snapshot greeks come from `tastytrade_client.get_positions` + `get_option_quotes` (greeks under `quotes[occ]["greeks"]` → `delta`, `mid_iv`). Position symbols are TT-format; convert TT↔OCC with `tastytrade_to_occ` / `occ_to_tastytrade`.

### Task 1: `greek_poller.poll_greeks_once`
**Files:** create `app/services/greek_poller.py`, `tests/test_greek_poller.py`

`async def poll_greeks_once(account_id, account_number) -> dict` returning `{"entries":n,"interims":n,"exits":n}`:
1. `positions = get_positions(account_number)`; keep `instrument-type == "Equity Option"` with `quantity != 0` → `tt_syms`.
2. `quotes = get_option_quotes([tastytrade_to_occ(s) for s in tt_syms])`; greeks via `quotes[occ]["greeks"]`.
3. `active_prev` = `option_symbol`s with an `entry` snapshot and no `exit` snapshot (this account).
4. For each open `tt_sym`: record `entry` if not in active_prev, else `interim` (delta, iv from quotes).
5. For each `sym in active_prev - current`: record `exit` copying the last snapshot's delta/iv.

Tests (monkeypatch `get_positions`/`get_option_quotes`): first poll on an open position → 1 entry; second poll same position → 1 interim; third poll after it disappears → 1 exit with carried-over delta; empty positions → no error.

### Task 2: Link on grouping + expose + poll endpoint + scheduler
**Files:** modify `app/services/journal_trades.py`, `app/routers/journal.py`, `app/services/scheduler.py`; create `tests/test_greek_linking.py`; update `API.md`

- In `create_trade_from_fills` and `attach_fills`: after linking fills, `UPDATE greek_snapshots SET journal_trade_id=? WHERE account_id=? AND journal_trade_id IS NULL AND option_symbol IN (<the trade's fills' option_symbols>)`.
- `GET /api/journal/trades/{id}` also returns `greeks: [...]` (snapshots linked to the trade).
- `POST /api/journal/greeks/poll` → resolve first TT account, call `poll_greeks_once`, return its counts.
- Scheduler: `scheduled_greek_poll()` iterating accounts w/ a TT account number, every 2 min during market hours (`minute="*/2", hour="9-16", mon-fri`).

Tests: grouping a trade links the matching entry/exit snapshots (journal_trade_id set); `GET /trades/{id}` includes them; poll endpoint returns counts (mocked).
