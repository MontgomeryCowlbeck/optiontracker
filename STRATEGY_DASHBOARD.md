# Strategy Dashboard — adoption plan & roadmap

> **SCOPE SUPERSEDED 2026-07-22.** Monty's decision: port_tracker is now the **unified
> personal trading platform** — ALL trades (systematic + discretionary) in one journal,
> filterable by mechanism/edge and tags, rather than an algo-only view. See `CLAUDE.md`.
> The reuse inventory and the **auto-associator** design below remain the roadmap for
> the algo sleeve (auto-tagging Strategy #1 trades from the signal log).

**What this is (original 2026-07-12 framing).** `port_tracker/` is the **algo-strategy portfolio dashboard**, adopted in-place from the
"optiontracker / Logbook" trade-journal app (a prior personal project). It tracks **only** the systematic
strategies' live positions & performance (Strategy #1 today), auto-associated from real Tastytrade fills —
**not** the discretionary / long-term positions in the same brokerage account.

Its origin app was built for the *same* philosophy as this quant platform — *"reward discipline, not wins;
money is whispered, discipline is shouted; journal real trades against named **mechanisms (edges)** to answer,
with real data, does this edge work live?"* We keep that ethos; we automate the manual parts.

## Decisions (locked 2026-07-12)
- **Adapt in-place.** Reuse the app (~90%): TT OAuth2 client, fills inbox, fill-grouping, P&L-from-real-
  cashflows, `mechanisms`↔trade association, analytics, discipline streak, the Logbook SPA.
- **Journal DB is the authoritative forward log** of live algo trades (real fills). `position_book.jsonl`
  narrows to the **live-sizing math** (R, open_risk, available) — *derived from* the journal's open
  Strategy-#1 trades. One reconciled record, not two.
- **`mechanisms` = our strategies.** Seed one row: **"Strategy #1 — premium selling"** (points at
  `../strategies/strategy_1_premium_selling/README.md`). Future strategies = more mechanism rows.
- **No-discretion → auto-associate.** Replace the manual "select fills → pick mechanism" step with a
  signal-log matcher (below). The signal log *is* the label.

## The reused pieces (do not rebuild)
| Piece | File | Role |
|---|---|---|
| TT OAuth2 client (refresh-token; sandbox host `api.cert.tastyworks.com`) | `app/services/tastytrade.py` | pull transactions / positions / balances / greeks |
| Fills inbox (pull → dedupe on `external_id`) | `app/services/fills_sync.py` + `tt_fills` table | raw execution ingest |
| Fill→spread grouping (union-find + clean-cycle) | `app/services/fill_grouping.py` | reconstruct multi-leg trades |
| P&L from signed `value` − fees (the phantom-P&L fix) | `app/services/calculations.py::summarize_journal_fills` | broker-accurate realized P&L |
| Edge association | `mechanisms` + `journal_trades.mechanism_id` | tie a trade to a strategy |
| Analytics (by-mechanism, system-vs-discretionary, expectancy, discipline streak) | `app/services/journal_analytics.py` | performance rollups |

## The one net-new piece — the auto-associator (`app/services/auto_associate.py`, TODO)
Rules-based, zero discretion:
1. On each fills sync, take the day's ungrouped `tt_fills`.
2. Reuse `fill_grouping.suggest_fill_groups` to form candidate spreads (drop the human-confirm ceremony;
   auto-create when a component is unambiguous — same `order_id` or a single clean open→close cycle).
3. **Fingerprint** each candidate against (a) the strategy signature — underlying ∈ {SPY,QQQ,DIA},
   put credit spread, ~30–50 DTE, short-leg delta near a rung (~0.10 or ~0.45), width $5/$3 — and
   (b) the fired **signal spec** for that date (`/data/structured/live/signal_<date>.json`: ticker, rungs,
   counts). A match → auto-create the `journal_trade`, stamp `mechanism_id = Strategy #1`, `is_system=true`.
4. No match → leave in the inbox as discretionary noise (never associated, never shown in the algo view).
Delta at fill comes from our own pricing engine (`pricing.strike_for_delta_american`) / Massive, since TT's
REST chain returns strikes+symbols but not greeks (greeks are streaming). `greek_poller` still captures
live entry/exit delta where market hours allow.

## Algo-specific views to add (net-new frontend, no charts exist today)
- **10%-ceiling gauge** — open_risk vs R (green/amber/red); the invariant, front and center.
- **Open barbell units** — rungs, credit, max_loss, days held, 50% PT progress, reconciliation vs broker.
- **Equity / cumulative realized P&L curve**, per-rung (low vs high) and per-ticker splits.
- **Regime banner** — SPY drawdown vs 52w high; flag the −10/−20% danger zone.
Charts are net-new (the origin app renders plain tables) — add lightweight inline SVG/canvas.

## What was stripped on adoption
Their `.env` (real creds — removed; we source from `/projects/quant/.secrets`), the 124 MB import zip and
all SQLite DBs/backups (git-ignored / removed — the DB regenerates on init), ~80 one-off `data/debug_*.py`
scripts, and misc cruft. Still-legacy-in-tree (strip as we go): the old `trades`/`strategy_groups`/wheel/
prospect model in `app/database.py` + `README.md`/most of `API.md` describe a product the running app
(only `auth` + `journal` routers) no longer serves.

## How it runs
- **Deploy:** Docker on kaiju — `python:3.11-slim`, `docker-compose up` → host **:8082** → container :8000,
  `./data` volume holds the SQLite DB. (Not runnable in the 3.14 quant venv; deps are 3.11-pinned.)
- **Creds:** env vars win (compose `${TASTYTRADE_*}` / `${JWT_SECRET_KEY}` from a git-ignored compose `.env`);
  for local/non-Docker runs `app/config.py` falls back to `/projects/quant/.secrets/tt_*.txt` and persists a
  stable `dashboard_jwt_secret.txt` there. Read-only TT scope is sufficient (we pull, never place).
- **Account:** bind the journal to TT account `5WI41395` (the ~$110k trading account) via `journal_settings`.

## Roadmap
1. **Stand up read-only on kaiju** — Docker, our creds, bind account 5WI41395, sync fills → see real trades.
2. **Seed Strategy #1 mechanism** + build `auto_associate.py` + tests (synthetic fills × sample signal log).
3. **Algo views** (ceiling gauge, barbell units, P&L curve, regime banner) + charts.
4. **Derive `position_book` sizing** from the journal's open Strategy-#1 trades (make the journal the single
   source; `daily_signal` reads open_risk from it).
5. Strip remaining legacy; update `API.md`.
