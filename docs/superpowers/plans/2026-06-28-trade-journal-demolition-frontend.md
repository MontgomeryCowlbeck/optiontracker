# Trade Journal — Demolition + Logbook Frontend (Plan 5)

> Executed inline. Demolition verified by boot + full suite; frontend verified by serving + manual browser review.

**Goal:** Remove the old app's domain and ship the "Logbook" frontend for the journal.

## Global Constraints
- Keep: `auth`, `journal` routers; `tastytrade.py`, `fills_sync`, `journal_trades`, `greek_poller`, `journal_analytics`, `calculations.py`, `database.py`, `models.py` (auth needs it), `rate_limit`. Same container name + port 8082.
- Framely/API-key consumers repoint to `/api/journal/*` (already API-key enabled). Old `mirror.py` removed.

### Task 1: Demolition
**Files:** delete `app/routers/{trades,prices,cash,benchmarks,prospects,watchlist,mirror,sync,analytics,pool}.py`, `app/services/tastytrade_sync.py`, `app/services/cache.py`, `app/services/provider.py`, `app/static/js/{app.js,widgets.js}`, `app/static/css/style.css`, `app/static/index.html`; rewrite `app/main.py` (include only auth + journal) and `app/services/scheduler.py` (jobs: fills sync intraday+postclose, greek poll, TT balance snapshot — all resolving the TT account via `get_accounts()`; remove price/benchmark/iv/old-sync jobs).
Verify: container boots; `GET /health` 200; full pytest suite still passes (52).

### Task 2: Logbook shell
**Files:** create `app/static/index.html`, `app/static/css/style.css`, `app/static/js/app.js` (shell only: design tokens, login, nav, API client).
Design tokens (from spec): ground `#E9ECE6`, ink `#1C2321`, graphite `#5A645E`, range `#2E6E6A`, trend `#B07A2E`, win `#4F7A4E`, loss `#A6533F`; IBM Plex Serif (headings) / Sans (UI) / Mono (numbers). Money muted; discipline prominent.

### Task 3: Logbook features
**Files:** extend `app/static/js/app.js`, `style.css`.
Views: Inbox (fills, multi-select → Group), Mechanisms (list/create/edit), Trades (list as logbook entries with regime tab; detail with fills+greeks; edit 8 manual fields; attach closes), Analytics (discipline streak hero, dissonance, per-mechanism/system/instrument/regime tables), Equity curve. Mobile-first.
Verify: rebuild container; index served; log in; smoke each view.
