# Trade Journal — Analysis + Equity Curve (Plan 4)

> Executed inline. TDD per task, Docker-run suite, one commit per task.

**Goal:** Turn closed journal trades into the analysis the journal exists for — per-mechanism P&L, system-vs-discretionary, per-instrument, by-regime, convex payoff, and the **discipline streak** (reward process, not P&L) — plus a TT-net-liq equity curve.

## Global Constraints
- All P&L/stat math in a dedicated module; no inline math in routers. Win = `pnl > 0`, breakeven neither.
- Endpoints `get_auth_context_with_api_key`, account-scoped. Tests under `tests/`. Additive.

### Task 1: `compute_journal_analytics` (pure)
**Files:** create `app/services/journal_analytics.py`, `tests/test_journal_analytics.py`

`compute_journal_analytics(trades: list[dict]) -> dict` over CLOSED trades with non-null `realized_pnl`. Each trade: `realized_pnl, mechanism_id, mechanism_name, is_system, underlying, regime_read, exit_reason, exit_at, id`.
- `_stats(pnls)` → `{count,total_pnl,avg_pnl,wins,losses,win_rate,avg_win,avg_loss,payoff_ratio,expectancy}`.
- `by_mechanism` (groups by mechanism_name, None→"(none)"; covers watch-tier verdict via a user-named mechanism), `system_vs_discretionary` (`{system,discretionary}`), `by_instrument` (underlying), `by_regime` (regime_read, None→"(unspecified)"), `overall`.
- `discipline`: adherent = `is_system` truthy AND `exit_reason in {target,stop,time,scratch}` (planned exits, independent of P&L). Order by `exit_at` then `id`. `current_streak` = trailing run of adherent; `longest_streak` = max run. `dissonance`: `won_but_broke_system` (pnl>0 & not adherent), `lost_but_well_executed` (pnl<0 & adherent).

Tests: per-mechanism totals/win-rate/payoff; system vs discretionary split; streak over a crafted sequence; dissonance counts; empty input safe.

### Task 2: Analytics endpoint
**Files:** modify `app/routers/journal.py`; create `tests/test_journal_analytics_api.py`; update `API.md`
- `GET /api/journal/analytics` → fetch closed trades (LEFT JOIN mechanisms for name), run `compute_journal_analytics`, return it.
Tests: create + close two trades on different mechanisms, assert response shape + a known number.

### Task 3: TT equity curve
**Files:** modify `app/services/scheduler.py`, `app/routers/journal.py`; create `tests/test_journal_equity.py`; update `API.md`
- `scheduled_tt_balance_snapshot()`: for each account with a TT account number, `get_balances` → write `net-liquidating-value` (portfolio_value) + `cash-balance` into `benchmark_snapshots` for today (insert/update), with SPY price best-effort. Register at 16:20 ET weekdays.
- `GET /api/journal/equity` → `{curve:[{snapshot_date,portfolio_value,spy_price}...]}` for the account, oldest first.
Tests: seed two benchmark_snapshots rows → endpoint returns them ordered.
