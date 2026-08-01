# Trade Journal Rebuild — Design Spec

**Date:** 2026-06-28
**Status:** Approved (pending final user review)
**Branch:** `initial_push`

## Motivation

OptionTracker no longer reflects how the owner trades. He is migrating from
undisciplined, dopamine-driven trading to a disciplined, rule-based, backtested
approach built around a set of named **mechanisms (edges)** he is actively
developing. He needs a tool whose single purpose is to **journal real trades
against those mechanisms** so he can answer, with real data, "does this edge
actually work when I trade it live?"

The existing app is being gutted. Its auto-pairing trade-sync engine
(`tastytrade_sync.py`) cannot reliably reconstruct 0DTE round-trips — its FIFO
matching and reconcile path produced phantom P&L (closing unmatched trades at
`closed_price = 0`, inventing the infamous −$70k loss). The fix is not better
auto-pairing; it is to **stop auto-pairing entirely** and let the trader select
and group his own fills. P&L is then only ever computed from real, paired cash
flows.

## Scope & Decisions

| Decision | Choice |
|---|---|
| Rebuild approach | **Gut in place** on `initial_push` — keep the working skeleton, replace the domain |
| Audience | **Single user, single trading account** (no family sharing) |
| Mechanisms/edges | **First-class entity** the user creates and manages |
| Journaling cadence | **Post-session batch** (same-day, while the tape is fresh) |
| Entry/exit delta | **Captured live in v1** via a tight market-hours poller |
| Cash ledger | **Cut** — equity curve fed by a daily TT balance snapshot instead |
| In scope beyond journal | Performance rollups (5 analysis views) · equity curve vs SPY · Framely API |

### Keep (already works — do not rebuild)
FastAPI skeleton · JWT auth · SQLite + migrations (`database.py`) · Docker/homelab
deploy · `app/services/tastytrade.py` client · scheduler · price-snapshot table ·
`benchmarks.py` (equity curve) · `mirror.py` + API-key auth (Framely consumer).

### Delete
`app/services/tastytrade_sync.py` (auto-pairing engine) · strategy-group inference ·
`app/routers/analytics.py` (old widgets) · pool · prospects · watchlist ·
cash-management UI · the entire old frontend (`app.js`, `widgets.js`, all views).

## Architecture

### Data model (new / changed tables)

**`mechanisms`** — the edges, user-managed.
- `id`, `account_id`
- `name` (e.g. "Validated Long — Williams+rVWAP+ADX-low")
- `criteria` (free text — the rule, e.g. "Williams %R ≤ -80 (14) + rVWAP lower band (34) + ADX ≤ 25 (17)")
- `regime` (categorical: range / trend / chop / n/a)
- `status` (developing / validated / retired)
- `notes`, `created_at`, `updated_at`

**`tt_fills`** — raw transactions pulled from TT; the ungrouped "inbox."
- `id`, `account_id`
- `external_id` (TT transaction id — unique, dedup key)
- `order_id`, `underlying`, `option_symbol` (OCC), `option_type`, `strike`,
  `expiration`, `action` (BTO/STO/BTC/STC), `quantity`, `price`,
  `fees`, `value` (TT signed cash flow — source of truth for P&L),
  `executed_at`, `trade_date`
- `journal_trade_id` (nullable — set when grouped)
- `dismissed` (bool — fills the user chooses to ignore, e.g. non-strategy noise)

**`journal_trades`** — one row per grouped trade (the journal unit).
- *Auto fields (derived from grouped fills):* `underlying`, `direction`
  (long_call / long_put / short_call / short_put / multi-leg), `strikes`,
  `expiration`, `dte_at_entry`, `is_0dte`, `entry_premium`, `exit_premium`,
  `quantity`, `fees_total`, `entry_at`, `exit_at`, `time_in_trade_seconds`,
  `realized_pnl`, `realized_pnl_pct`, `status` (open / closed)
- *Manual core fields (8):* `mechanism_id` (FK, nullable = discretionary/none),
  `is_system` (bool: system vs discretionary), `conviction` (1–5),
  `why_entered` (text), `thesis_worked` (yes / no / partial),
  `exit_reason` (target / stop / scratch / time / panic / reversed),
  `emotional_state` (calm / disciplined / fomo / revenge / bored),
  `reflection` (text)
- *Optional Layer-3 fields (nullable, off by default in UI):* `regime_read`,
  `execution_discipline` (1–5), `long_stopped_then_reversed` (bool),
  `short_loser_overran` (bool), `mistake_tag`, plus structured setup-checklist flags

**`greek_snapshots`** — live greeks captured by the poller.
- `id`, `account_id`, `option_symbol`, `journal_trade_id` (nullable),
  `snapshot_type` (entry / exit / interim), `delta`, `iv`, `captured_at`

**Balance snapshots** — **reuse the existing benchmark snapshot table** from
`benchmarks.py`, writing a daily row of TT net-liq (`get_balances`) + SPY price
for the equity curve. No new table; the snapshot *source* changes from the cash
ledger to a direct TT balance read.

### P&L computation
Realized P&L for a `journal_trade` is the **sum of TT's signed `value` field
across all grouped fills** (credits positive, debits negative), **minus the sum
of `fees` across those fills** (TT reports `value` gross of commission/clearing/
regulatory fees, which are carried separately per fill — the same split the old
parser used). This mirrors the source-of-truth approach the old code
trusted (`tt_value`) and is robust for messy multi-fill 0DTE round-trips. All
financial math stays in a trimmed `app/services/calculations.py` — **no inline
P&L formulas in routers** (existing hard rule preserved).

### The grouping workflow (core loop)
1. **Sync** pulls TT transactions into `tt_fills` via `get_all_transactions`
   (dedup on `external_id`). Money-movements/fees ignored for the inbox.
2. **Inbox view** lists ungrouped, non-dismissed fills bucketed by day:
   symbol · strike · exp · action · price · qty · time.
3. User **multi-selects** the fills composing one trade and clicks **Group**.
4. App creates a `journal_trade`: opens vs closes inferred from `action`;
   entry/exit premium and timestamps derived; **realized P&L summed from `value`**.
   Multi-leg and scale-in/out are simply "more fills selected."
5. **Open positions:** group only the opening fill(s) → trade is `open`, exit
   fields blank. Later, edit the trade to attach the closing fill(s) → `closed`.
6. User tags **mechanism** + fills the 8 core fields.
7. Ungrouped/dismissed fills stay in the inbox forever — **no forced pairing.**

### Delta poller
A tight market-hours poll (~60–90s) over `get_positions`. On first sight of a
position, snapshot greeks (≈ entry delta/IV); keep the last reading before the
position disappears (≈ exit delta/IV). Writes `greek_snapshots`, later linked to
the `journal_trade` when fills are grouped. Reuses the existing scheduler.

**Known limitations (accepted):** snapshots lag the real fill by up to one poll
interval; sub-interval scalps may capture entry only, or miss; 0DTE contracts
delist at expiry, so greeks must be captured the same session — none can be
backfilled. Adequate for coarse ATM-vs-OTM strike-selection analysis; not
fill-millisecond precise.

### Analysis views (the payoff)
All exposed via the API (`get_auth_context_with_api_key`) for Framely, and
`API.md` updated:
1. **Per-mechanism P&L** — total/avg P&L, win rate, expectancy per edge.
2. **System vs discretionary** — does judgment beat the signal?
3. **Per-instrument** — SPY vs QQQ realized comparison.
4. **Outcome by regime** — does the edge work in its intended regime live?
5. **Convex payoff** — avg win / avg loss ratio per mechanism.
6. **Watch-tier verdict** — do filtered-zone (watch) mechanisms make or lose money?

Win-rate convention preserved: `pnl > 0` is a win; breakeven is neither.

## Visual Design Direction — "Logbook"

The interface rejects the dopamine aesthetic of every other trading app (dark
mode, neon P&L, big flashing numbers). The subject's real world is a quant
researcher's lab: hypotheses (mechanisms), regimes, edge-over-base-rate,
convexity. The journal is a **logbook of experiments**, and it looks like one.

**Governing philosophy — reward discipline, not wins.** The good feeling must
come from a well-executed trade that followed the system, never from the size of
a win. The design makes money *quiet* and process *loud*.

**Signature / the one risk:** money is whispered, discipline is shouted. Dollar
P&L renders small, muted, monospace, in the corner of each entry. The hero of
every trade is the judgment (system vs discretionary, conviction, did-the-thesis
-work, emotional state).

**Discipline streak (the reward mechanism):** a computed feedback signal — count
of consecutive trades that were `is_system = true` AND exited by plan
(`exit_reason` in target/stop/time/scratch, NOT panic), **independent of P&L.**
A by-the-book loss extends the streak. The app flags the dissonant cases:
"won but broke the system" (warned, not celebrated) and "lost but well executed"
(affirmed). Computed from existing fields — no new capture.

**Palette:** Ground `#E9ECE6` (cool pale sage paper) · Ink `#1C2321` · Graphite
`#5A645E`. Two accents that encode **regime**, never decoration: range/mean-
reversion `#2E6E6A` (muted teal), trend/momentum `#B07A2E` (earthy ochre). P&L
deliberately desaturated: win `#4F7A4E` moss, loss `#A6533F` clay.

**Type:** one engineered superfamily across three roles — IBM Plex Serif
(headings), IBM Plex Sans (UI/body), IBM Plex Mono (**all** numerics: premiums,
deltas, P&L, timestamps; tabular-aligned).

**Layout:** lab-notebook calm — generous whitespace, hairline rules, faint graph-
paper grid only behind hero/empty states. Each trade is a logbook entry with a
regime-colored edge tab. The dashboard hero is the **verdict on each mechanism**
(is this edge working live?), not a portfolio number. Mobile-first per existing
conventions; inbox multi-select must be thumb-friendly.

## Error handling
- Sync dedup via unique `external_id`; partial sync failures logged, never crash.
- Grouping validation: a trade must contain ≥1 opening fill; mixed underlyings in
  one group rejected with a clear message.
- Poller failures are non-fatal (logged), consistent with existing sync behavior.

## Testing
Unit tests for:
- Fill → trade grouping: single-leg in/out, multi-leg, scale-in/out, open-only.
- P&L summation from TT `value` (credit/debit signs, fees).
- Mechanism rollups and the 5 analysis aggregations.
- Win-rate convention (`pnl > 0`).

## Out of scope (v1)
Manual (non-TT) trade entry · multi-account · cash-management UI · real-time
entry-stage journaling · the Layer-3 optional fields surfaced by default.

## Mobile
All new UI follows the existing mobile-first conventions (`.page-header` /
`.page-title` / `.page-actions`, sortable tables, cache-busted asset versions).
The inbox multi-select must be thumb-friendly.
