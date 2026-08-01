# AGENTS.md — Options Tracker Architecture Guide

This document is the primary reference for AI agents and developers working on the Options Tracker codebase. Read it before making any changes.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Directory Structure](#2-directory-structure)
3. [Data Model](#3-data-model)
4. [Calculation Architecture](#4-calculation-architecture) ← read this carefully
5. [API Surface](#5-api-surface)
6. [Authentication Pattern](#6-authentication-pattern)
7. [Frontend Patterns](#7-frontend-patterns)
8. [Conventions & Rules](#8-conventions--rules)
9. [Common Pitfalls](#9-common-pitfalls)
10. [Deployment](#10-deployment)

---

## 1. Project Overview

A personal options trading tracker for monitoring positions, P&L, and yield analytics. Built for one user with family/friend sharing. Also consumed by **Framely** (a digital picture frame platform) via API keys.

- **Backend:** FastAPI (Python, async)
- **Database:** SQLite via `aiosqlite`
- **Auth:** JWT tokens + API keys
- **Deployment:** Docker on homelab VM, accessible at `options.thesquishlab.com` (Cloudflare tunnel) and `options.home` (Traefik internal)
- **External integration:** Tastytrade brokerage API (OAuth2) for auto-importing trades

---

## 2. Directory Structure

```
app/
  main.py                    # FastAPI app entry point, router registration, lifespan
  database.py                # Schema, migrations (init_db), fetch_all/fetch_one helpers
  auth/
    dependencies.py          # get_auth_context, get_auth_context_with_api_key
    models.py                # User, Account, ApiKey Pydantic models
    security.py              # JWT creation/validation, bcrypt password hashing
  routers/
    auth.py                  # /api/auth — login, accounts, API keys, preferences
    trades.py                # /api/trades — core trade CRUD, dashboard, all analytics
    cash.py                  # /api/cash — ledger-based cash management
    prices.py                # /api/prices — price refresh, option quotes, Tradier
    benchmarks.py            # /api/benchmarks — equity curve, snapshots, performance
    prospects.py             # /api/prospects — trade watchlist / paper trades
    watchlist.py             # /api/watchlist — stock ticker watchlist
    analytics.py             # /api/analytics — yield analytics (strategy-aware P&L)
    mirror.py                # /api/mirror — external display (magic mirror) summaries
    sync.py                  # /api/sync — Tastytrade sync config and triggers
  services/
    calculations.py          # ALL calculation functions (single source of truth)
    tastytrade_sync.py       # Tastytrade sync engine (parses transactions, creates trades)
    scheduler.py             # APScheduler jobs (auto price refresh, auto sync)
    tradier.py               # Tradier broker client (quotes, chains, Greeks)
    finnhub.py               # Finnhub market data client (backup quotes)
    yahoo.py                 # Yahoo Finance fallback (stock quotes)
    cache.py                 # In-memory analytics cache (5-min TTL)
  static/
    index.html               # Single-page app shell
    js/
      app.js                 # All frontend JS (~14000+ lines)
      widgets.js             # Dashboard widget definitions and layout system
    css/
      style.css              # Custom styles (mobile-first, responsive)
```

---

## 3. Data Model

All data is scoped to an `account_id`. One user can have multiple accounts.

### Key Tables

#### `trades`
The core table. Each row is one option contract leg.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `account_id` | INTEGER FK | Always required |
| `ticker` | TEXT | Underlying symbol (e.g. "SPY") |
| `action` | TEXT | `"sell"` or `"buy"` |
| `option_type` | TEXT | `"put"` or `"call"` |
| `strike` | REAL | Strike price |
| `expiration` | TEXT | ISO date string |
| `quantity` | REAL | Number of contracts |
| `price` | REAL | Entry price per contract |
| `trade_date` | TEXT | ISO date string |
| `status` | TEXT | `"open"`, `"closed"`, `"expired"`, `"assigned"` |
| `closed_date` | TEXT | NULL if still open |
| `closed_price` | REAL | Exit price per contract (NULL if open) |
| `commission` | REAL | Total commission for this leg |
| `strategy_group_id` | INTEGER FK | Links legs of a multi-leg strategy |
| `strategy_type` | TEXT | Deprecated — use `strategy_groups.strategy_type` instead |
| `sync_source` | TEXT | `"manual"` or `"tastytrade_api"` |
| `external_id` | TEXT | Tastytrade transaction ID (for dedup) |
| `roll_count` | INTEGER | How many times rolled (0 = never rolled) |

#### `strategy_groups`
Groups multiple trade legs into one strategy (spreads, iron condors, PMCC, etc.).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `account_id` | INTEGER FK | |
| `strategy_type` | TEXT | `"naked_put"`, `"vertical_spread"`, `"iron_condor"`, etc. |
| `stop_loss` | REAL | Dollar stop-loss amount (NULL = no stop) |
| `closed_date` | TEXT | NULL if any leg still open |

**Strategy types:** `naked_put`, `naked_call`, `covered_call`, `vertical_spread`, `iron_condor`, `strangle`, `straddle`, `calendar_spread`, `custom`

#### `cash_transactions`
Ledger-based cash accounting. Balance is always the `balance_after` of the most recent row.

| Column | Type | Notes |
|--------|------|-------|
| `transaction_type` | TEXT | `"deposit"`, `"withdrawal"`, `"premium_received"`, `"premium_paid"`, `"trade_close"`, `"dividend"`, `"adjustment"` |
| `amount` | REAL | Positive = cash in, Negative = cash out |
| `balance_after` | REAL | Running balance |

#### `price_snapshots`
Historical option prices with Greeks. One row per (trade_id, timestamp).

| Column | Type | Notes |
|--------|------|-------|
| `trade_id` | INTEGER FK | |
| `bid` / `ask` / `last` | REAL | Market prices |
| `delta` / `gamma` / `theta` / `vega` / `rho` | REAL | Greeks |
| `mid_iv` | REAL | Implied volatility (0-1 scale, e.g. 0.35 = 35%) |

#### `stock_positions`
Held stock from assignments or manual entry.

| Column | Type | Notes |
|--------|------|-------|
| `ticker` | TEXT | |
| `shares` | REAL | |
| `cost_basis` | REAL | Price paid per share |
| `effective_cost_basis` | REAL | Cost basis adjusted for premium received (wheel strategy) |

#### `benchmark_snapshots`
Daily portfolio value snapshots for equity curve charting.

| Column | Type | Notes |
|--------|------|-------|
| `snapshot_date` | TEXT | Trading day this represents |
| `portfolio_value` | REAL | Total portfolio value |
| `cash_balance` | REAL | Cash only |
| `spy_price` | REAL | SPY closing price for benchmark |

---

## 4. Calculation Architecture

**The golden rule: all financial calculations live in `app/services/calculations.py`. Never write P&L formulas inline in routers or frontend JS.**

### Single-Trade Functions

Always import from `app.services.calculations`:

```python
from app.services.calculations import (
    calculate_premium,          # price × qty × 100 (always positive)
    calculate_net_premium,      # positive for sells, negative for buys
    calculate_realized_pnl,     # commission-aware P&L for closed trades → Optional[float]
    calculate_unrealized_pnl,   # uses mid price, commission-aware → Optional[float]
    calculate_capital_deployed, # strike × 100 × qty for short puts → Optional[float]
    calculate_dte,              # days to expiration → int
    calculate_moneyness,        # "ITM" / "ATM" / "OTM" (ATM = within 2% of strike)
    compute_trade_metrics,      # computes ALL metrics for one trade in one call
)
```

### Strategy-Level Aggregation

When you need P&L grouped by strategy (not per leg), use `compute_closed_units()`:

```python
from app.services.calculations import compute_closed_units

units = compute_closed_units(closed_trades)
# Each unit: {pnl, premium_collected, ticker, strategy_type, closed_date, trade_date, status}
```

**This is the canonical function** used by the Yield tab. It:
- Groups legs by `strategy_group_id` (spreads netted into one P&L)
- Subtracts commissions (via `calculate_realized_pnl`)
- Skips orphaned buy legs in ungrouped trades
- Returns one unit per strategy/trade

### Win Rate Definition

**A trade is a win if `pnl > 0`. Breakeven (`pnl == 0`) is neither win nor loss.**

This is consistent across:
- `AggregateStats.add_trade()` in calculations.py
- Dashboard endpoint in trades.py
- Yield analytics in analytics.py

### Premium Sign Convention

- **Sell to open:** positive premium collected (credit)
- **Buy to open:** negative premium (debit)
- `calculate_net_premium(trade)` follows this convention
- `premium_collected` in `compute_closed_units` is the **net** opening credit: positive for credit spreads, negative for debit spreads (like PMCC)

### Mark Price for Unrealized P&L

`calculate_unrealized_pnl()` uses:
1. Mid price `(bid + ask) / 2` if both available
2. Last price if last is inside the spread (better mark for illiquid options)
3. Last price as fallback if bid/ask unavailable

The same logic is in `calculate_unrealized_pnl()` — don't reimplement it.

### What `trades.py` Dashboard Computes

The `/api/trades/dashboard` endpoint uses `calculations.py` functions correctly:
- `calculate_premium()` for premium totals
- `calculate_realized_pnl()` for closed trade P&L
- `calculate_unrealized_pnl()` for open trade P&L
- `calculate_capital_deployed()` for CSP capital

The dashboard does **not** use strategy-group-aware P&L for totals (trades are per-leg). This is intentional — the Yield tab is the strategy-aware view.

---

## 5. API Surface

Base URL: `/api`. All endpoints require `Authorization: Bearer <token>` or `X-API-Key: <key>`.

**New endpoints must use `get_auth_context_with_api_key`** (not `get_auth_context`) to support both JWT and API key auth for Framely compatibility.

### Auth — `/api/auth`
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/login` | JWT token (rate-limited 5/min) |
| GET | `/me` | Current user |
| PUT | `/password` | Change password |
| GET | `/accounts` | List accounts |
| POST | `/accounts` | Create account |
| PUT | `/accounts/{id}` | Update account |
| DELETE | `/accounts/{id}` | Delete account |
| POST | `/api-keys` | Generate API key |
| GET | `/api-keys` | List API keys |
| DELETE | `/api-keys/{id}` | Revoke API key |
| GET | `/preferences/{key}` | Get user preference |
| PUT | `/preferences/{key}` | Set user preference |

### Trades — `/api/trades`
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/trades` | All trades (filterable by status, limit) |
| GET | `/trades/dashboard` | Dashboard stats aggregate |
| POST | `/trades` | Create single trade |
| GET | `/trades/{id}` | Single trade with latest snapshot |
| PUT | `/trades/{id}` | Update trade |
| DELETE | `/trades/{id}` | Delete trade + reverse cash |
| POST | `/trades/{id}/close` | Close trade, record P&L |
| POST | `/trades/{id}/assign` | Mark assigned, create stock position |
| POST | `/trades/multi-leg` | Create all legs of a strategy at once |
| GET | `/trades/analytics/data` | Per-leg analytics (premium by ticker, monthly P&L) |
| GET | `/trades/analytics/extended` | Win rates, DTE analysis, streaks, day-of-week |
| GET | `/trades/analytics/iv-performance` | High IV vs low IV trade performance |
| GET | `/trades/analytics/ticker-pnl` | Options + stock P&L per ticker |
| GET | `/trades/analytics/true-pnl` | Comprehensive P&L (premium + stock realized + unrealized) |
| GET | `/trades/analytics/wheel-summary` | Wheel cycle P&L (CSP → assign → CC) |
| GET | `/trades/strategy-groups` | All strategy groups with consolidated legs |
| POST | `/trades/strategy-groups` | Create strategy group |
| GET | `/trades/strategy-groups/{id}` | Strategy with max profit/loss/breakeven |
| POST | `/trades/strategy-groups/{id}/close` | Close all legs at once |
| GET | `/trades/stock-positions` | All stock positions with P&L |
| GET | `/trades/stock-positions/consolidated` | Consolidated by ticker |
| POST | `/trades/stock-positions` | Add stock position manually |

### Analytics — `/api/analytics`
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/yield` | Strategy-aware yield analytics (see response schema below) |

**`GET /api/analytics/yield` response:**
```json
{
  "summary": {
    "total_pnl": 0.0,
    "win_rate": 0.0,
    "avg_win": 0.0,
    "avg_loss": 0.0,
    "avg_days_in_trade": 0,
    "total_trades": 0,
    "open_trades": 0,
    "open_premium": 0.0,
    "total_premium_collected": 0.0,
    "stop_risk": 0.0,
    "max_risk": 0.0
  },
  "closed_trade_series": [{"closed_date": "YYYY-MM-DD", "pnl": 0.0, "ticker": "string"}],
  "monthly_cash_flow": [{"month": "YYYY-MM", "premium_in": 0.0, "premium_out": 0.0, "net": 0.0}],
  "monthly_realized_pnl": [{"month": "YYYY-MM", "pnl": 0.0}],
  "trade_outcomes": {"bought_to_close": 0, "assigned": 0, "expired": 0, "total": 0},
  "strategy_performance": [{"strategy_type": "string", "trades": 0, "wins": 0, "win_rate": 0.0, "total_pnl": 0.0, "open_positions": 0, "open_premium": 0.0}],
  "ticker_performance": [{"ticker": "string", "trades": 0, "wins": 0, "win_rate": 0.0, "total_pnl": 0.0, "pnl_pct": 0.0, "open_positions": 0, "open_premium": 0.0}],
  "ticker_closed_trades": {"TICKER": [{"closed_date": "YYYY-MM-DD", "trade_date": "YYYY-MM-DD", "strategy_type": "string", "status": "string", "pnl": 0.0}]},
  "open_positions_detail": [{"ticker": "string", "strategy_type": "string", "expiration": "YYYY-MM-DD", "net_premium": 0.0, "legs": 0}]
}
```

**Key fields:**
- `open_premium` — net credit from **credit-only** open positions (net > 0). Debit positions excluded.
- `total_premium_collected` — net opening credit from **closed** credit positions only.
- `ticker_closed_trades` — keyed by ticker symbol, sorted newest-first. Used for clickable row expansion.
- `open_positions_detail` — per open position breakdown for the "Open Premium" modal.

### Cash — `/api/cash`
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/balance` | Current balance + transaction type summary |
| POST | `/deposit` | Record deposit |
| POST | `/withdraw` | Record withdrawal |
| POST | `/adjustment` | Manual balance adjustment |
| GET | `/transactions` | Paginated transaction history |
| DELETE | `/transactions/{id}` | Delete + recalculate subsequent balances |

### Prices — `/api/prices`
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/refresh` | Refresh prices for all open trades |
| GET | `/option/{occ_symbol}` | Single option quote |
| GET | `/quote/{symbol}` | Stock quote |
| POST | `/quotes/batch` | Batch stock quotes |
| GET | `/{trade_id}` | Price history for a trade |

### Benchmarks — `/api/benchmarks`
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/snapshot` | Create today's EOD snapshot |
| GET | `/snapshots` | List snapshots (date range, limit) |
| GET | `/equity-curve` | Portfolio vs SPY returns (1M/3M/6M/YTD/1Y/ALL) |
| GET | `/rolling-returns` | 30/60/90 day returns vs SPY |
| GET | `/portfolio-breakdown` | Cash + stocks + open options breakdown |

### Sync — `/api/sync`
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/status` | Sync config + last run time |
| POST | `/now` | Trigger immediate Tastytrade sync |
| PUT | `/config` | Update account number, start date, toggle auto-sync |
| GET | `/accounts` | List linked Tastytrade accounts |
| POST | `/test-connection` | Test Tastytrade OAuth2 |
| GET | `/balances` | Live Tastytrade account metrics (net liq, cash, buying power) |
| POST | `/stop-losses` | Set stop-loss limits on open strategy groups |

### Mirror — `/api/mirror`
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/summary` | Quick summary for external display |

### Prospects — `/api/prospects`
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Watchlist with live quotes |
| POST | `/` | Add prospect |
| POST | `/{id}/execute` | Convert prospect to trade |
| POST | `/multi-leg` | Create multi-leg template |
| POST | `/strategy-groups/{id}/execute` | Execute multi-leg template as trades |

### Watchlist — `/api/watchlist`
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Watched tickers + quotes + open trades |
| POST | `/` | Add ticker |
| DELETE | `/ticker/{ticker}` | Remove ticker |
| POST | `/sync-from-trades` | Auto-sync from tickers with open trades |

---

## 6. Authentication Pattern

**Always use `get_auth_context_with_api_key` for new endpoints.** This supports both JWT tokens and API keys (needed for Framely):

```python
from app.auth.dependencies import get_auth_context_with_api_key, AuthContext

@router.get("/my-endpoint")
async def my_endpoint(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    account_id = auth.account_id  # always use this for DB queries
    user_id = auth.user_id
```

**Never use `get_auth_context` for new endpoints** — it only supports JWT, which breaks API key consumers.

The `AuthContext` object has: `user_id`, `account_id`, `username`.

---

## 7. Frontend Patterns

### API Calls

All API requests go through `apiRequest(path, options)` in `app.js`. The base URL is `/api`:

```javascript
// Correct — path is relative to /api
const data = await apiRequest('/trades?status=all');
const trades = data.trades;  // /trades returns {trades: [...]}

// Wrong — double /api prefix
const data = await apiRequest('/api/trades');
```

**Important:** `/api/trades` returns `{ trades: [...] }`, not a raw array. Always destructure.

### Sortable Tables

All data tables must use `initSortableTable()`:

```javascript
initSortableTable('table-id', data, renderBodyFn, 'default_sort_key', 'desc');
```

The render callback receives sorted data and should set `tbody.innerHTML`.

### Mobile-First Headers

All view headers must use the unified pattern:

```html
<div class="page-header">
    <h5 class="page-title"><i class="bi bi-icon me-2"></i>Title</h5>
    <div class="page-actions">
        <button class="btn btn-primary">
            <i class="bi bi-plus"></i>
            <span class="btn-text">Action</span>
        </button>
    </div>
</div>
```

On mobile (`< 768px`): headers stack vertically, buttons expand full width, `.btn-text` spans hide (icons only visible). `.btn-primary` buttons keep their text always visible.

### Cache Busting

When modifying `app.js` or `style.css`, increment the version query param in `index.html`:

```html
<link rel="stylesheet" href="/static/css/style.css?v=XX">   <!-- increment XX -->
<script src="/static/js/app.js?v=XX"></script>               <!-- increment XX -->
```

Current: `style.css?v=76`, `app.js?v=150`, `widgets.js?v=16`

### Dashboard Widget Data

`loadDashboard()` in `app.js` fetches all data in parallel via `Promise.all`. The `yieldData` global holds the `/api/analytics/yield` response. Widget render functions access it directly:

```javascript
const [stats, positions, tradesData, snapshots, ttBalances, syncStatus, yieldData] = await Promise.all([...]);
```

### Dropdowns Inside Cards

Bootstrap cards clip dropdowns due to `overflow: hidden`. Fix:

```css
#your-view .card,
#your-view .card-body {
    overflow: visible;
}
#your-view .card .dropdown-menu {
    z-index: 1050;
}
```

---

## 8. Conventions & Rules

### Calculation Rules
1. **Never write `price * quantity * 100` inline in a router.** Use `calculate_premium(trade)`.
2. **Never write `(price - closed_price) * qty * 100` inline.** Use `calculate_realized_pnl(trade)`.
3. **For strategy P&L (spread nets), use `compute_closed_units(trades)`.** Do not re-implement grouping.
4. **Win rate uses `pnl > 0`.** Breakeven is not a win.

### API Rules
1. New endpoints use `get_auth_context_with_api_key`.
2. All new data fields must be included in the API response (Framely compatibility).
3. Update `API.md` when adding/modifying/removing endpoints.
4. No breaking changes to existing response shapes.

### Database Rules
1. All queries must include `account_id = ?` in WHERE clause.
2. Use `fetch_all()` and `fetch_one()` from `app.database` (not raw aiosqlite).
3. Schema changes go in `init_db()` in `database.py` as `ALTER TABLE IF NOT EXISTS` migrations.

### Frontend Rules
1. Mobile-first — test all UI on mobile viewport.
2. Use `initSortableTable()` for all data tables.
3. Use `.page-header` / `.page-title` / `.page-actions` for all view headers.
4. Bump cache version in `index.html` after every static file change.
5. **`hideAllViews()` must stay in sync with index.html.** Every `<div id="*-view">` in the HTML must have a matching `document.getElementById('*-view').style.display = 'none'` line in `hideAllViews()`. Adding or removing a view div requires updating both places — a stale ID causes a null-style crash on every navigation.
6. **Never compute capital inline in the frontend.** Use `trade.capital_deployed` (returned by `/api/trades`). This value is `null` for spread legs — using `strike * 100 * quantity` on all short puts inflates totals by counting spread legs as capital.

---

## 9. Common Pitfalls

### Wrong API path prefix
```javascript
// WRONG — double /api prefix
apiRequest('/api/analytics/yield')

// CORRECT — apiRequest prepends /api automatically
apiRequest('/analytics/yield')
```

### Leg-level vs strategy-level P&L
The trades table has one row per option leg. A vertical spread has 2 rows. **Never sum raw trade P&L** across the dashboard without grouping by `strategy_group_id` first. Use `compute_closed_units()` for strategy-level views.

### Open premium sign
The `open_premium` field in the Yield summary includes **only credit positions** (net > 0). Debit positions like PMCC (with expensive long LEAPs) are excluded from the dollar total but still appear in the `open_positions_detail` list. This is intentional — the metric represents collected credit, not net exposure.

### Commission awareness
`_leg_pnl()` — **do not create this pattern again.** The old inline P&L function in analytics.py ignored commissions. Use `calculate_realized_pnl()` which subtracts commission.

### Strategy type location
The `strategy_type` column exists on both the `trades` table and `strategy_groups` table. The authoritative value is `strategy_groups.strategy_type`. When querying, always JOIN:
```sql
LEFT JOIN strategy_groups sg ON sg.id = t.strategy_group_id
```
And use `sg.strategy_type`.

### Tastytrade sync deduplication
Trades synced from Tastytrade have `external_id` set. Three-layer dedup exists: unique index, order-group check, semantic matching. Do not add a fourth layer — it creates false negatives.

### Dashboard portfolio value
Portfolio value and cash balance come **exclusively from Tastytrade** when available (`/api/sync/balances`). The local cash ledger is secondary. The dashboard hero section should always prefer `ttBalances.net_liquidating_value` over computed values.

### Dropdowns inside strategy view cards
The Strategies view uses `overflow: visible` on cards so dropdown menus are not clipped. If adding dropdowns to other card-based views, replicate this pattern.

### `hideAllViews()` / index.html view div mismatch
`hideAllViews()` in `app.js` must list every `<div id="*-view">` that exists in `index.html`. If a view is added or removed from the HTML without updating `hideAllViews()`, every tab navigation throws `TypeError: Cannot read properties of null (reading 'style')`. Always update both together.

### Inline capital calculation in calendar stats
The "Capital Freeing Up" stat (and any similar metric) must use `trade.capital_deployed` returned by `/api/trades`, **not** the inline formula `strike * 100 * quantity`. `calculate_capital_deployed()` in `calculations.py` returns `null` for spread legs — the inline formula inflates totals by treating every short put (including spread legs) as naked capital.

---

## 10. Deployment

### Local Development
```bash
docker compose build --no-cache
docker compose up -d
# App at http://localhost:8082
```

**Always test on local container before authorizing a push to production.**

### Production
Push to the `initial_push` branch triggers GitHub Actions CI/CD:
1. Builds Docker image
2. Runs Ansible playbook against homelab VM
3. Deploys updated container

**Never push to `initial_push` without explicit user authorization.** The production app is live at `options.thesquishlab.com`.

### Environment Variables
| Variable | Purpose |
|----------|---------|
| `TASTYTRADE_CLIENT_ID` | Tastytrade OAuth2 client ID |
| `TASTYTRADE_CLIENT_SECRET` | Tastytrade OAuth2 client secret |
| `TASTYTRADE_REFRESH_TOKEN` | Long-lived OAuth2 refresh token |
| `FINNHUB_API_KEY` | Finnhub market data (optional, backup quotes) |
| `JWT_SECRET_KEY` | Signs JWT tokens (required) |
| `DATABASE_PATH` | SQLite file path (default: `/app/data/options.db`) |
| `JWT_EXPIRE_DAYS` | Token lifetime (default: 30) |

### Database
SQLite at `/app/data/options.db` (bind-mounted from `./data/` on host). Migrations run automatically on startup. Default admin: `admin` / `changeme123`.
