# Trading Platform API

Base URL: `/api`. This documents the API actually mounted by `app/main.py`
(routers: `auth`, `journal`). The pre-2026-07 Options-Tracker endpoints
(trades/stock-positions/prices/benchmarks/magic-mirror/…) are no longer mounted.

**Auth:** JWT `Authorization: Bearer <token>` from `/auth/login`, or a
machine API key `X-API-Key: <key>` (journal endpoints accept both).
**Account scope:** every journal call is scoped to one account — the JWT's
default account, overridable per-request with `X-Account-ID: <id>`; API keys
are bound to a single account at creation.

---

## Auth — `/api/auth`

| Method | Path | Notes |
|---|---|---|
| POST | `/login` | `{username, password}` → `{access_token, accounts: [...]}`. Rate-limited 5/min. Registration is disabled. |
| GET | `/me` | Current user profile. |
| PUT | `/password` | `{current_password, new_password}`. |
| DELETE | `/user` | Delete user + all data. |
| GET/POST | `/accounts` | List / create trading accounts. |
| PUT/DELETE | `/accounts/{id}` | Rename / delete an account. |
| POST | `/api-keys` | `{name}` → key (shown once). Bound to the current account. |
| GET | `/api-keys` | List keys (prefixes only). |
| DELETE | `/api-keys/{id}` | Revoke. |
| GET/PUT/DELETE | `/preferences/{key}` | Per-user UI preferences. |

---

## Journal — `/api/journal`

### Fills & the auto-tracker

Trades are **auto-detected** — there is no manual grouping step. On every sync:
opening fills sharing a TT order become a new trade; an opening fill on a leg an
open trade already holds scale-ins to it (FIFO); closing fills FIFO-attach to
the oldest open trade whose leg they fit, and a trade auto-closes when every
contract is matched. Flat trades never absorb new fills, so repeated 0DTE
round-trips on the same strike become separate trades. Fills dated before the
**journal epoch (2026-07-14)**, and closing fills of pre-epoch positions, are
auto-dismissed. Open trades whose every leg is past expiration are swept closed
at zero (`exit_reason: expired` — a planned exit).

| Method | Path | Notes |
|---|---|---|
| GET | `/fills` | Unplaced, undismissed fills — normally empty; anything here needs manual repair (e.g. one fill closing legs from several trades). |
| POST | `/fills/sync` | Pull TT transactions since `sync_from_date`, then auto-group + expiry-sweep + covered-call reclassification (see `direction` below). Returns `{synced, skipped, trades_created, fills_attached, unmatched, dismissed_pre_epoch, trades_expired, covered_reclassified}`. |
| POST | `/fills/{id}/dismiss` | Drop an unplaced fill without journaling it. |
| GET | `/positions` | Open trades valued at the latest polled marks: per-leg net/mark/delta/theta/IV, `cash_flow`, `liquidation_value`, `unrealized_pnl` (None + `marks_missing` when a leg lacks a mark), `pct_of_credit` (progress toward the 50% PT on credit trades), `position_delta`/`position_theta` (net greeks of the open legs × contracts × 100; None until every leg has a mark), `days_open`, and for defined-risk shapes `max_loss` (width − credit) + `return_on_risk`. Short-premium shapes additionally carry `spot` (latest poller-captured underlying), `breakevens` (short strike ∓ credit/share; two-sided shapes get two), `cushion_pct` (% move in spot to the nearest short strike, negative once breached), `moneyness` (OTM / ATM within 2% / ITM; delta-graded fallback without spot), and `day_pnl` (unrealized change since the prior session's last mark; None when unknowable). Also returns a book-level `summary` (totals for credit/liquidation/unrealized/day P&L/net Δ/net θ/defined risk, with coverage counts). |
| GET | `/calendar/forward` | The look-ahead calendar: `expirations` (open positions keyed by expiry date), `earnings` (next earnings date per held + research-watchlist name, `held` flagged; yfinance-sourced, 10-min cached), and the 30-45 DTE `dte_window`. |
| GET | `/strangle/watch` | Vol watchlist: per marked name, live `spot/iv/iv_front/iv_back/rv20/iv_rv/term_ratio/straddle_pct/earnings/ivr` (+ `ivr_n` while the snapshot history is still building for non-index names) and up to 40 daily snapshots — the record IV-crush forward review reads. |
| POST | `/strangle/watch` | `{symbol}` — mark a stock/ETF for vol tracking (validated live, snapshotted immediately; nightly at 5:40 PM ET thereafter). |
| DELETE | `/strangle/watch/{symbol}` | Unmark; snapshot history is retained. |
| GET | `/strangle/screener` | ETF strangle conditions + live construction math (SPY/QQQ/DIA/IWM): vol-index IV proxy vs RV20, `vrp_z` (252d), gap ratio, expected move, and 16Δ/16Δ + 25Δp/6Δc constructions with credits/breakevens from the yfinance chain (screener-grade BS deltas). Decision support ONLY — the ledger's VRP-z entry candidate failed its holdout; `rich` marks the unvalidated train-period threshold. |
| GET | `/digest` | The Morning page payload, stored-data only (fast pre-market): `market` (last pulse), `strategy1` (latest signal), `watchtower` cards (3d), `book` summary, `attention` (open positions needing eyes: threatened/expiring/pt_hit/earnings/clock), 14-day `calendar` (expirations + nightly-stored earnings), `research_flags`, open `intents`, `debt`. Vol-watch/screener live numbers are NOT here — the page fetches those separately. |
| GET | `/digest/live` | The Morning Refresh live layer (60s server cache): `as_of`, `session` (pre-market/regular/after-hours/closed), `quotes` (SPY/QQQ/DIA/IWM/TLT/GLD/^VIX/^VIX3M: `last`, `prev_close`, `d1_pct`, per-quote `as_of`; minute bars incl. pre/post), and code-computed `sentiment` — component gauges (VIX level, VIX 1d, VIX/VIX3M term structure, index breadth, haven bid) each scored on [-1,+1] with a `read` label, averaged into `score` + composite `label` (risk-on … risk-off). No LLM — every number is derivable from the quotes. |
| GET | `/debt` | Trades owing journal work: `{count, items:[{id, underlying, direction, strikes, status, missing:[edge\|risk\|exit reason]}]}` — open first, newest first. The nav badge, dashboard queue, and Discord cards all read this. |
| GET | `/intents` | Open pre-trade intents (stale ones auto-expire after 7 days). |
| POST | `/intents` | `{underlying, direction?, edge_id?, is_system?, planned_risk?, conviction?, note?}` — state the plan before the fill; the tracker binds the next matching trade and pre-fills its journal fields (never overwriting). |
| DELETE | `/intents/{id}` | Cancel an open intent. |

### Notifications (Discord)

Webhook: `.secrets/discord_webhook_tracker.txt` (env `DISCORD_WEBHOOK_TRACKER` wins;
`TRACKER_DISABLE_DISCORD=1` silences; `TRACKER_APP_URL` sets the deep-link base).
Event-driven card after every sync that created/closed trades (intent-bound and
Strategy #1 auto-tagged trades are labeled), plus a 5:10 PM ET EOD sweep (today's
closes + remaining journal debt; silent when clean). All sends are best-effort —
Discord being down never fails a sync.
| POST | `/positions/{trade_id}/consult` | `{message}` — one turn with the same desk consultant as `/research/{symbol}/consult`, but the clicked trade is pinned as `subject_position` in the context: no intent gate, the agent goes straight to the position's state (pct_of_credit vs the 50% PT, DTE, net Δ/θ, max loss, days held) and management. The conversation is persisted server-side per trade — history comes from the stored thread (a client-sent `history` is ignored). 404 unless the trade is open and yours; 503 when the `claude` CLI is absent. Verdict-carrying replies are auto-saved to the underlying's research trail (`kind: consult`). |
| GET | `/positions/{trade_id}/consult` | The stored consult thread: `{trade_id, symbol, messages: [{role, content, created_at}]}`. Works on closed trades too — the record outlives the position. |
| DELETE | `/positions/{trade_id}/consult` | Clear the stored thread so the next consult starts fresh. |

### Trades

All list/analytics endpoints below accept the same optional filters:
`edge_id`, `tag_id`, `underlying`, `status`, `direction`, `date_from`,
`date_to` (dates YYYY-MM-DD, filtering on the entry day).

`direction` is the auto-classified strategy shape (`calculations.classify_strategy`
over the opening legs): `short_put`/`long_put`/`short_call`/`long_call`,
`put_credit_spread`/`put_debit_spread`/`call_credit_spread`/`call_debit_spread`,
`put_calendar`/`call_calendar`, `put_diagonal`/`call_diagonal`,
`short_straddle`/`long_straddle`, `short_strangle`/`long_strangle`,
`risk_reversal`, `jade_lizard`/`reverse_jade_lizard`, `iron_condor`,
`iron_butterfly`, `long_/short_{put,call}_butterfly`, else `custom_N_leg`
(named shapes require equal leg quantities — a 1x2 is custom, not a vertical).
`covered_call` is assigned at sync time, not by the classifier: an open
`short_call` becomes `covered_call` when live TT equity positions show >=100
held shares per contract (and reverts if the shares go away). Closed trades
keep the label they had while open.
Analytics include a `by_strategy` breakdown. Re-run after classifier changes:
`scripts/reclassify_trades.py`.

| Method | Path | Notes |
|---|---|---|
| POST | `/trades` | Repair tool: `{fill_ids, ...manual fields}` — hand-group unplaced fills. Derived fields (P&L, premiums, DTE, status) come from `calculations.summarize_journal_fills`, never the client. |
| GET | `/trades` | Filterable list; each trade carries `edge_name`, `mechanism_name` (auto-resolved structure playbook) + `tags`. |
| GET | `/trades/{id}` | Trade + its `fills`, `greeks`, `tags`, plus one-tap planned-risk fills: `defined_risk` (width − credit; defined-risk shapes only) and `two_x_credit` (2× credit collected; short-premium shapes only). |
| PUT | `/trades/{id}` | Edit manual journal fields: `edge_id, is_system, conviction, why_entered, thesis_worked, exit_reason, emotional_state, reflection, planned_risk, assignment_intent`. `assignment_intent` marks a CSP sold wanting the shares: assignment is a fill, not a loss — `/positions` then reports `assignment_capital` (strike × 100 × contracts) instead of max loss, and `/debt` stops requiring a risk number. |
| DELETE | `/trades/{id}` | Ungroup — fills return to the inbox. |
| POST | `/trades/{id}/fills` | Attach more fills (closing legs); trade recomputed. |
| PUT | `/trades/{id}/tags` | `{tag_ids}` — replace the trade's tag set. |

`planned_risk` is the dollar risk accepted at entry — the R denominator for
R-multiple analytics. Optional but strongly encouraged; coverage is reported.

### Tags

Free-form colored labels, one shared pool per account. Each tag is `{id, name,
color}` where `color` is `#rrggbb`. (The former fixed categories — setup /
mistake / emotion / context — were dropped 2026-07-26; the migration kept each
existing tag's color.)

| Method | Path | Notes |
|---|---|---|
| GET | `/tags` | All tags, ordered by name. |
| POST | `/tags` | `{name, color?}` (color defaults `#3987e5`). 409 on duplicate name, 400 on bad color. |
| PUT | `/tags/{id}` | `{name?, color?}` — rename/recolor; trades carrying it follow. |
| DELETE | `/tags/{id}` | Deletes; trade associations cascade away. |

### Edges — the WHY (hypotheses trades are tagged to)

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/edges` | List / create. Status: `developing`/`validated`/`retired`. |
| GET/PUT/DELETE | `/edges/{id}` | Standard CRUD. |

### Mechanisms — the HOW (per-structure playbooks, auto-resolved)

A mechanism is the auto-detected structure's playbook, keyed by
`journal_trades.direction` (the classifier's vocabulary). It attaches to trades
automatically — the API edits playbook text, it never assigns.

| Method | Path | Notes |
|---|---|---|
| GET | `/mechanisms` | `{mechanisms: [{structure, name, rules, notes, trade_count}], unplaybooked: [{structure, trade_count}]}`. Lazily seeds the standard playbooks for the account. |
| POST | `/mechanisms` | `{structure, name, rules?, notes?}` — add a playbook for an unseeded structure (409 if it has one). |
| PUT/DELETE | `/mechanisms/{id}` | Edit `name/rules/notes` (structure is fixed) / remove. |

### Analytics & views

| Method | Path | Notes |
|---|---|---|
| GET | `/analytics` | Filterable: `overall` stats (incl. `profit_factor`), `r_multiples`, `by_tag`, `by_edge`, `by_mechanism` (by structure, playbook-labeled), `by_instrument`, `by_regime`, `system_vs_discretionary`, `discipline` (streaks + dissonance). All math in `journal_analytics.py`. |
| GET | `/calendar` | Filterable per-day realized P&L keyed by **exit** date: `{date, pnl, trades, wins, r, has_note, has_review}` — `r` = the day's summed R-multiples over trades with a declared planned risk (null if none). |
| GET | `/score` | Filterable composite score: six 0-100 components (`win_rate, payoff, profit_factor, drawdown, consistency, recovery`; weights in `SCORE_WEIGHTS`) + `score`, `max_drawdown`. |
| GET | `/logbook` | Filterable (same filter set) day-paged journal: per-day trades (each with server-computed `insights` badges: `{key, label, tone}` — unnamed bet, off-plan exit, by-the-book loss, % of credit kept, cut early, inside 21 DTE, full credit), stats, coaching message, note, AI review. |
| PUT | `/day-notes/{date}` | `{note?, ai_feedback?}` — each field independent. |
| GET | `/motd` | Discipline digest for today. |
| GET | `/equity` | Daily net-liq vs SPY from benchmark snapshots. |

### AI (headless `claude -p` on the host)

| Method | Path | Notes |
|---|---|---|
| GET | `/ai/status` | `{available}` — false when the claude CLI isn't on this host. |
| POST | `/ai/day-review/{date}` | Generate + store the day's review (code computes all numbers; LLM writes the coaching). 503 when unavailable or the day has no trades. Regenerating clears the day's reply thread. |
| GET | `/ai/day-review/{date}/thread` | `{messages: [{role, content, created_at}]}` — the stored conversation under this day's review, oldest first. |
| POST | `/ai/day-review/{date}/reply` | `{message}` → `{reply}` — respond to the day's review; both turns persist server-side (`day_review_messages`). 404 when the day has no review yet. |
| DELETE | `/ai/day-review/{date}/thread` | Clear the day's reply thread. |
| POST | `/ai/chat` | `{message, session_id?}` → `{reply, session_id, resumed}` — persistent chat grounded in this account's journal. Omit `session_id` to start a new stored session; pass it back to continue one (turn 2+ resumes the underlying claude conversation — the journal context is read once, on the first turn). Legacy stateless mode: send `{message, history}` with no `session_id` → `{reply, session_id: null}`, nothing stored. First turn is slow (up to ~3 min); resumed turns are much faster. |
| GET | `/ai/chats` | `{sessions: [{id, title, created_at, updated_at, n_messages}]}` — stored chat sessions, most recently active first. |
| GET | `/ai/chats/{id}` | `{messages: [{role, content, created_at}]}` — one session's transcript. 404 if not this account's. |
| DELETE | `/ai/chats/{id}` | Delete a stored chat session and its messages. |

Nightly reviews: `scripts/ai_day_review.py` (host cron) hits the day-review
endpoint after the close.

### Signals

| Method | Path | Notes |
|---|---|---|
| GET | `/signals?days=N` | Read-only feed over the quant crons' JSON: watchtower alert cards (tier STRONG/MARGINAL × class TRADE/WATCH, suggested put, zone stats) each linked to the journal trade that took it (`taken_trade_id`, short-put on that symbol within 3 days); latest Strategy #1 daily signal; latest market pulse. Paths configurable via `WATCHTOWER_DIR` / `LIVE_SIGNALS_DIR`. |
| GET | `/dd/{symbol}` | Deep-dive: code-computed DD payload — full 2y close/SMA50/SMA200 `series` (UI offers 3M/6M/1Y/2Y timeframes), watchtower support/resistance zones (touches, strength, HVN/round flags) with `zone_lookback_days` labeling the 400-trading-day detection window, and `stats` (RSI14, 5d/20d returns, 52w range, ATR, RV20, ATM IV + IV/RV ratio, SMA trend, next earnings). yfinance-backed (discretionary sleeve — allowed); 503 if the quant src tree is absent, 404 for unknown/thin symbols. |
| POST | `/dd/{symbol}/brief` | AI research brief over the DD numbers + this account's journal history on the name (`claude -p` with WebSearch): chart read, catalysts, vol context, bull/bear case, watchtower-framework lens. Decision support only — never a buy/sell call. Slow (up to ~3 min). |

### Research (personal watchlist)

Names outside Strategy #1 / the watchtower universe, snapshotted nightly by the
in-app scheduler (5:30 PM ET Mon–Fri) via the DD engine. Condition flags are
computed in `services/research.py` (`near_support` ≤3% from a zone,
`earnings_soon` ≤7d, `rsi_washout` ≤35, `iv_rich` IV/RV ≥1.3) and shown
in-app — no Discord pings by design.

| Method | Path | Notes |
|---|---|---|
| GET | `/research` | Watchlist cards: thesis, assignment stance, latest snapshot + flags + 1d change, 30d history (sparklines), journal linkage, last AI activity. |
| POST | `/research/watchlist` | `{symbol, thesis?, assignment_ok?}` — validates the ticker by snapshotting it immediately. Upserts. |
| PUT | `/research/watchlist/{symbol}` | Update thesis / assignment stance. |
| DELETE | `/research/watchlist/{symbol}` | Unfollow. Snapshots/notes/briefs are kept — research history is a record. |
| POST | `/research/refresh[?symbol=]` | Re-snapshot one name or the whole list on demand. |
| GET/POST | `/research/{symbol}/notes`, DELETE `/research/notes/{id}` | Timestamped research trail per name. |
| GET | `/research/{symbol}/briefs` | Saved AI output (briefs + daily cards + consult verdicts), newest first. |
| POST | `/research/{symbol}/brief` | Run the DD research brief (WebSearch) and save it to the trail. |
| POST | `/research/{symbol}/daily-update` | Regenerate the AI daily card (`kind: daily`) for one name. The nightly refresh writes one per watchlist name automatically: price action, volume, regime, levels, vol, what to watch — all numbers code-computed, no buy/sell calls. Overview cards carry `last_daily`. |
| POST | `/research/{symbol}/consult` | `{message}` — the consult agent: reads the DD, the LIVE candidate-put table (`dd.fetch_candidate_puts`, 25-50 DTE), thesis/stance/notes/journal history. First turn interrogates intent (goal, assignment tolerance, size); then it DOES recommend — verdict/structure/case/risks/invalidation — citing only code-computed numbers (may pick from the put table, never invent premium). The conversation is persisted server-side per name (a client-sent `history` is ignored). Replies carrying a Verdict are auto-saved as `kind: consult`. |
| GET | `/research/{symbol}/consult` | The stored name-level consult thread: `{symbol, messages: [{role, content, created_at}]}`. |
| DELETE | `/research/{symbol}/consult` | Clear the stored thread so the next consult starts fresh. |

### Settings

| Method | Path | Notes |
|---|---|---|
| GET | `/tt-accounts` | Tastytrade accounts visible to the configured OAuth login. |
| POST | `/accounts/provision` | One journal account per TT account, bound + isolated. Idempotent. |
| GET/PUT | `/settings` | `{tt_account_number, sync_from_date}` for the current account. |
| POST | `/greeks/poll` | One round of live entry/exit greek capture for open positions. |
