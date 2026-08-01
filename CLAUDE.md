# CLAUDE.md — Personal Trading Platform (port_tracker)

## Project Overview

Monty's personal trading platform: a TradeZella-class options trade journal +
analytics dashboard, evolved in-place from the earlier "Options Tracker" app.
One platform, all trades — systematic (Strategy #1) and discretionary — with
every trade taggable to its **edge** (the hypothesis it expresses — assigned by
the trader) while its **mechanism** (the option structure's playbook) is
auto-resolved from the tracker's `direction` classification; every stat is
filterable by edge/tag/underlying/direction/date. Philosophy, enforced in copy and scoring: **reward
discipline, not wins; a by-the-book loss is a good trade.**

- **Ingest:** Tastytrade API (OAuth2 refresh-token; creds in `/projects/quant/.secrets/tt_*.txt`,
  env vars win for Docker). Fills sync from **2026-07-14 forward** — the journal epoch is a
  HARD floor (`journal_settings.JOURNAL_EPOCH`): pre-epoch fills and closes of pre-epoch
  positions are auto-dismissed. Trades opened before it do not exist here.
- **Flow (auto-tracker — NO manual grouping):** fills sync → `auto_group_fills`
  (position-based FIFO: order-batch opens a trade, scale-ins merge, closes attach
  oldest-first, flat = closed; 0DTE cycles stay separate) → expiry sweep closes
  fill-less expirations at zero → trader annotates the TRADE (tags/mechanism/risk).
  Market-hours greek poller captures per-leg mark/delta/theta/IV → `/positions` values
  open trades live (unrealized P&L, 50%-PT progress, net Δ/θ, defined-risk max loss,
  book summary). Sync also reclassifies open `short_call` → `covered_call` when live TT
  equity positions show the shares (`calculations.coverage_adjusted_direction`).
  Manual group/attach/ungroup remain as repair tools for the rare fill the tracker
  can't place.
- **DD panel:** `/journal/dd/{symbol}` (services/dd.py) reuses the watchtower level
  engine from `/projects/quant/src` (read-only import; yfinance OK — discretionary
  sleeve) for the Signals deep-dive: chart series + zones + RSI/vol/IV/earnings, plus
  an optional `claude -p` research brief with WebSearch.
- **AI:** headless `claude -p` (the watchtower pattern). Code computes ALL numbers;
  the LLM only writes coaching/pattern prose. Endpoints 503 cleanly when the CLI
  is absent (e.g. inside a container).

## Tech Stack

- **Backend:** FastAPI + SQLite (aiosqlite). Routers mounted: `auth`, `journal` ONLY —
  the legacy routers (prices, benchmarks, magic mirror, …) are unmounted; their
  tables persist for data continuity.
- **Frontend:** React + Vite + TypeScript in `frontend/`, built to `app/static/dist/`
  (served at `/` when present; legacy vanilla SPA in `app/static/` is the fallback).
  Charts: recharts, vendored via npm — no runtime CDN.
- **Auth:** JWT + per-account API keys (`X-API-Key`), `X-Account-ID` header switches account.
- **Deploy:** systemd **user** service on kaiju (`ops/trading-platform.service` →
  `~/.config/systemd/user/`, linger enabled), port 8600, DB at
  `/data/structured/tracker/options.db`. Host-run on purpose: the AI endpoints need
  the host's `claude` CLI (a container would 503 them). Single uvicorn process on
  purpose — the in-app scheduler must not duplicate. Nightly review cron: 21:30 UTC
  Mon–Fri in `crontab -l` (needs an API key in `.secrets/tracker_api_key.txt`).
  The Dockerfile/compose remain for a future non-kaiju move (no docker installed here).

## Core Values & Principles

### 0. Canonical Calculation Functions (HARD RULE — NO EXCEPTIONS)

**NEVER write inline P&L, premium, or stat formulas in routers or the frontend.**
- Fill-derived trade fields (P&L, premiums, DTE, status): `app/services/calculations.py::summarize_journal_fills` — realized P&L is `sum(value) - sum(fees)` over grouped fills, only when closed.
- Aggregate stats (win rate, expectancy, profit factor, R-multiples, drawdown, calendar, score): `app/services/journal_analytics.py`. The frontend renders server numbers; it never recomputes them.
- **Win rate**: `pnl > 0` is a win. Breakeven is NOT a win. Consistent everywhere.
- Legacy per-contract helpers (`calculate_premium`, `calculate_net_premium`, …) remain canonical for any code touching the legacy tables.

### 1. Consolidation is King
Reuse before writing: `journal_trades.fetch_trades` is the one way to read trades
(filters + tags + mechanism name attached); `_trade_filters` is the one filter
contract shared by trades/analytics/calendar/score.

### 2. Discipline over outcomes — sleeve-agnostic (revised 2026-07-22)
Discretionary trading is a legitimate sleeve of this business, not a vice.
"On-plan" = the trade followed **its own** playbook: a named edge
(`edge_id` set) + a planned exit (`PLANNED_EXITS`). A mechanism (auto structure)
never satisfies the edge bar. `is_system` labels
the sleeve for analytics only — it plays NO role in adherence. The discipline
streak counts on-plan trades regardless of sleeve or P&L; `compute_motd`
praises well-executed losers and flags unnamed bets (no edge attached) and
off-plan exits — never discretion itself. Don't reintroduce
discretionary-shaming copy in the UI.

### 3. Mobile-first, single user
Responsive is required (sidebar → bottom nav <768px, touch targets ≥44px).
No multi-tenant features; account rows map to Tastytrade accounts.

### 4. API stability
External consumers (dashboards, cron scripts) use API keys. New data must be
exposed via the API; new journal endpoints use `get_auth_context_with_api_key`.
**Always update `API.md`** when the API changes.

## Key Files

- `app/main.py` — app entry; serves `app/static/dist/` when built
- `app/database.py` — schema + migrations (run on startup via `init_db()`)
- `app/routers/journal.py` — the platform API (fills, trades, tags, edges, mechanisms, analytics, calendar, score, AI, settings)
- `app/services/tastytrade.py` — TT OAuth2 client (sandbox host via `TASTYTRADE_SANDBOX`)
- `app/services/fills_sync.py` — ingest; runs the tracker on every sync
- `app/services/journal_trades.py` — the auto-tracker (`auto_group_fills`, `close_expired_trades`) + `fetch_trades` + tags
- `app/services/journal_analytics.py` — ALL aggregate math (stats, R, calendar, score)
- `app/services/ai_review.py` — day review + chat via `claude -p`
- `scripts/ai_day_review.py` — nightly review cron (host-side)
- `frontend/` — React app (see its README for dev commands)

## Development

```bash
source /projects/quant/venv/bin/activate
cd /projects/quant/port_tracker
python -m pytest tests -q                       # backend tests
uvicorn app.main:app --port 8600                # backend (serves built SPA too)
export PATH="$HOME/.local/bin:$PATH"            # node 22 lives in ~/.local
cd frontend && npm run dev                      # vite dev server, proxies /api
npm run build                                   # → app/static/dist/
```

- Default admin user (`admin`/`changeme123`) auto-created on a fresh DB — change it.
- DB path: `DATABASE_PATH` env (container: `/app/data/options.db`).
- Secrets resolve env-first, then `/projects/quant/.secrets/` (see `app/config.py`).
- Tests use a temp DB and override auth; AI tests mock `_run_claude` — never call the real LLM in tests.

## Known context

- `STRATEGY_DASHBOARD.md` (2026-07-12) planned an algo-only dashboard; **superseded
  2026-07-22** by the unified-platform decision (all trades, filter by edge). Its
  auto-associator idea (signal-log fingerprint → auto-tag Strategy #1 trades,
  `is_system=true`) is still the roadmap for the algo sleeve.
- The in-app scheduler (`app/services/scheduler.py`) runs fills sync, the greek
  poll, and the daily equity (net-liq vs SPY) snapshot into `benchmark_snapshots`.
