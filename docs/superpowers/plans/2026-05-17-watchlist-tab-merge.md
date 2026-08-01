# Watchlist Tab Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the Pool and Calendar nav tabs into a single "Watchlist" tab with a vertical stack layout: collapsible pool cards above a pool-aware expiration calendar.

**Architecture:** Three frontend files only — no backend changes. `index.html` gets a new `watchlist-view` div replacing both `pool-view` and `calendar-view`. `app.js` gets new `showWatchlist()`/`loadWatchlist()` orchestration, collapse logic, filter strip logic, and an enhanced `renderCalendar()` that reads a shared `poolData` module-level variable. `style.css` gets new component styles.

**Tech Stack:** Vanilla JS, Bootstrap 5, Bootstrap Icons — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-17-watchlist-calendar-merge-design.md`

---

## File Map

| File | What changes |
|------|-------------|
| `app/static/index.html` | Replace nav items; replace `pool-view` + `calendar-view` divs with `watchlist-view`; bump `style.css?v=75` and `app.js?v=147` |
| `app/static/js/app.js` | Add `poolData` var; `showWatchlist()`, `loadWatchlist()`, `refreshWatchlist()`; redirect `showPool()`/`showCalendar()` to `showWatchlist()`; update `hideAllViews()`; add collapse logic; add filter strip logic; add `renderWatchlistIVAlertBanner()`; enhance `renderCalendar()` and `selectCalendarDay()`; add `scrollToPoolCard()` |
| `app/static/css/style.css` | Add styles for: collapse toggle, IV alert banner, filter strip, calendar nav inline, DTE window cell, pool earnings highlight, IV badge, legend additions, pool-card highlight animation |

---

## Task 1: Update nav + replace view divs in index.html

**Files:**
- Modify: `app/static/index.html`

- [ ] **Step 1: Update the nav links**

Find the two nav items (around line 76-84):
```html
<li class="nav-item">
    <a class="nav-link" href="#" id="nav-pool" onclick="showPool()">Pool</a>
</li>
<li class="nav-item">

</li>
<li class="nav-item">
    <a class="nav-link" href="#" id="nav-calendar" onclick="showCalendar()">Calendar</a>
</li>
```

Replace with a single item:
```html
<li class="nav-item">
    <a class="nav-link" href="#" id="nav-watchlist" onclick="showWatchlist()">Watchlist</a>
</li>
```

- [ ] **Step 2: Replace pool-view and calendar-view with watchlist-view**

Delete the entire `<!-- Pool View -->` block (from `<div id="pool-view"...>` to its closing `</div>`, around lines 680-723).

Delete the entire `<!-- Calendar View -->` block (from `<div id="calendar-view"...>` to its closing `</div>`, around lines 725-790).

Insert the following `watchlist-view` div in their place (between the end of any preceding view and `<!-- Analytics View -->`):

```html
<!-- Watchlist View -->
<div id="watchlist-view" style="display: none;">
    <div class="page-header">
        <h2 class="page-title">Watchlist</h2>
        <div class="page-actions">
            <div class="pool-search-wrapper">
                <div class="pool-search-inner">
                    <i class="bi bi-search pool-search-icon"></i>
                    <input type="text" id="pool-search-input" class="pool-search-input"
                           placeholder="Add ticker…"
                           oninput="onPoolSearch(this.value)"
                           autocomplete="off">
                    <div id="pool-search-results" class="pool-search-dropdown" style="display:none;"></div>
                </div>
            </div>
            <button class="btn btn-outline-secondary btn-sm" onclick="refreshWatchlist()" title="Refresh">
                <i class="bi bi-arrow-clockwise"></i> <span class="btn-text">Refresh</span>
            </button>
        </div>
    </div>

    <!-- VIX Banner -->
    <div id="pool-vix-banner" class="pool-vix-banner mb-3" style="display:none;">
        <div class="pool-vix-label">VIX</div>
        <div class="pool-vix-level" id="pool-vix-level">—</div>
        <div class="pool-vix-context">
            <span class="text-muted small">IVR</span>
            <span id="pool-vix-rank" class="fw-semibold ms-1">—</span>
            <span class="text-muted small ms-3">IVP</span>
            <span id="pool-vix-pct" class="fw-semibold ms-1">—</span>
        </div>
        <div class="pool-vix-signal" id="pool-vix-signal"></div>
    </div>

    <!-- Collapsible Pool Section -->
    <div class="watchlist-collapse-toggle" id="watchlist-collapse-toggle" onclick="toggleWatchlistPool()">
        <span id="watchlist-collapse-icon"><i class="bi bi-chevron-down"></i></span>
        <span class="watchlist-collapse-title">Watchlist</span>
        <span class="watchlist-ticker-count" id="watchlist-ticker-count"></span>
    </div>
    <div id="watchlist-pool-section">
        <div id="pool-ticker-grid" class="pool-ticker-grid mb-4">
            <div class="text-center text-muted py-5" id="pool-empty-state">
                <i class="bi bi-collection" style="font-size:2rem;display:block;margin-bottom:0.5rem;opacity:0.4;"></i>
                Your pool is empty. Search for a ticker above to add it.
            </div>
        </div>
    </div>

    <!-- IV Alert Banner -->
    <div id="watchlist-iv-alert" class="watchlist-iv-alert mb-3" style="display:none;"></div>

    <!-- Filter Strip -->
    <div class="watchlist-filter-strip mb-3">
        <span class="watchlist-filter-label">Show on calendar:</span>
        <button class="watchlist-filter-btn active" id="filter-btn-earnings" onclick="toggleCalendarFilter('earnings')">
            <i class="bi bi-megaphone-fill me-1"></i>Earnings
        </button>
        <button class="watchlist-filter-btn active" id="filter-btn-dteWindows" onclick="toggleCalendarFilter('dteWindows')">
            <i class="bi bi-clock me-1"></i>DTE Windows
        </button>
        <button class="watchlist-filter-btn active" id="filter-btn-ivAlerts" onclick="toggleCalendarFilter('ivAlerts')">
            <i class="bi bi-lightning-fill me-1"></i>IV Alerts
        </button>
    </div>

    <!-- Calendar Nav (inline) -->
    <div class="watchlist-calendar-nav mb-3">
        <button class="btn btn-outline-secondary btn-sm" onclick="changeCalendarMonth(-1)">
            <i class="bi bi-chevron-left"></i>
        </button>
        <span class="calendar-month-label" id="calendar-month-label">May 2026</span>
        <button class="btn btn-outline-secondary btn-sm" onclick="changeCalendarMonth(1)">
            <i class="bi bi-chevron-right"></i>
        </button>
        <button class="btn btn-outline-primary btn-sm" onclick="goToToday()">Today</button>
    </div>

    <!-- Calendar Legend -->
    <div class="calendar-legend mb-3">
        <span class="legend-item"><span class="legend-dot otm"></span> OTM</span>
        <span class="legend-item"><span class="legend-dot atm"></span> ATM</span>
        <span class="legend-item"><span class="legend-dot itm"></span> ITM</span>
        <span class="legend-item"><span class="legend-dot unknown"></span> Unknown</span>
        <span class="legend-item"><span class="legend-dot earnings"></span> Earnings</span>
        <span class="legend-item"><span class="legend-dte-stripe"></span> DTE Window</span>
        <span class="legend-item"><i class="bi bi-lightning-fill legend-iv-icon"></i> IV Alert</span>
    </div>

    <!-- Calendar Grid -->
    <div class="card">
        <div class="card-body p-0">
            <div class="calendar-grid" id="calendar-grid"></div>
        </div>
    </div>

    <!-- Selected Day Details -->
    <div class="card mt-4" id="calendar-day-details" style="display: none;">
        <div class="card-header">
            <h5><i class="bi bi-calendar-event me-2"></i><span id="calendar-selected-date">Events</span></h5>
        </div>
        <div class="card-body p-0">
            <div id="calendar-day-trades"></div>
        </div>
    </div>

    <!-- Monthly Stats -->
    <div class="row g-4 mt-2">
        <div class="col-md-4">
            <div class="stat-card">
                <div class="stat-label">Expiring This Month</div>
                <div class="stat-value" id="calendar-expiring-count">0</div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="stat-card">
                <div class="stat-label">Capital Freeing Up</div>
                <div class="stat-value" id="calendar-capital-freeing">$0</div>
            </div>
        </div>
        <div class="col-md-4">
            <div class="stat-card">
                <div class="stat-label">Premium at Risk</div>
                <div class="stat-value" id="calendar-premium-risk">$0</div>
            </div>
        </div>
    </div>
</div>
```

- [ ] **Step 3: Bump version numbers**

In `index.html` line 19, change:
```html
<link href="/static/css/style.css?v=74" rel="stylesheet">
```
to:
```html
<link href="/static/css/style.css?v=75" rel="stylesheet">
```

In `index.html` around line 3047, change:
```html
<script src="/static/js/app.js?v=146"></script>
```
to:
```html
<script src="/static/js/app.js?v=147"></script>
```

- [ ] **Step 4: Commit**

```bash
git add app/static/index.html
git commit -m "feat: add watchlist-view HTML scaffold, replace pool+calendar nav"
```

---

## Task 2: JS — Core wiring (showWatchlist, loadWatchlist, hideAllViews, backward compat)

**Files:**
- Modify: `app/static/js/app.js`

- [ ] **Step 1: Add `poolData` module-level variable**

Find the line `let poolSearchTimer = null;` (around line 14667). Add above it:

```javascript
// Shared pool data — populated by loadPool(), consumed by renderCalendar()
let poolData = [];
let watchlistPoolCollapsed = false;
let calendarFilters = { earnings: true, dteWindows: true, ivAlerts: true };
```

- [ ] **Step 2: Update `hideAllViews()`**

Find `hideAllViews()` (around line 1990). It currently has:
```javascript
document.getElementById('calendar-view').style.display = 'none';
...
document.getElementById('pool-view').style.display = 'none';
```

Replace those two lines with:
```javascript
document.getElementById('watchlist-view').style.display = 'none';
```

- [ ] **Step 3: Replace `showPool()` and `showCalendar()` with `showWatchlist()`**

Find the existing `showPool()` function (around line 14669):
```javascript
function showPool() {
    hideAllViews();
    setNavActive('nav-pool');
    document.getElementById('pool-view').style.display = 'block';
    loadPool();
}
```

Replace the entire function with:
```javascript
function showPool() { showWatchlist(); }  // backward compat

function showWatchlist() {
    hideAllViews();
    setNavActive('nav-watchlist');
    document.getElementById('watchlist-view').style.display = 'block';
    loadWatchlist();
}
```

Find the existing `showCalendar()` function (around line 1416):
```javascript
function showCalendar() {
    hideAllViews();
    setNavActive('nav-calendar');
    document.getElementById('calendar-view').style.display = 'block';
    loadCalendar();
}
```

Replace with:
```javascript
function showCalendar() { showWatchlist(); }  // backward compat
```

- [ ] **Step 4: Add `loadWatchlist()` and update `refreshPool()`**

Find `async function refreshPool()` (around line 14676):
```javascript
async function refreshPool() {
    loadPool();
}
```

Replace with:
```javascript
async function refreshWatchlist() {
    loadWatchlist();
}

async function refreshPool() { refreshWatchlist(); }  // backward compat
```

Add `loadWatchlist()` right after `showWatchlist()`:

```javascript
async function loadWatchlist() {
    try {
        await Promise.all([loadPool(), loadCalendar()]);
        // Re-render calendar now that poolData is populated from loadPool()
        renderCalendar();
        updateCalendarStats();
    } catch (error) {
        showToast('Error', 'Failed to load watchlist: ' + error.message, 'danger');
    }
}
```

- [ ] **Step 5: Update `loadPool()` to store data in `poolData`**

Find the existing `async function loadPool()` (around line 14680):
```javascript
async function loadPool() {
    try {
        const data = await apiRequest('/pool/tickers');
        renderPoolVix(data.vix);
        renderPoolTickers(data.tickers);
    } catch (e) {
        showToast('Error', 'Failed to load pool: ' + (e.message || e), 'danger');
    }
}
```

Replace with:
```javascript
async function loadPool() {
    try {
        const data = await apiRequest('/pool/tickers');
        poolData = data.tickers || [];
        renderPoolVix(data.vix);
        renderPoolTickers(data.tickers);
        renderWatchlistIVAlertBanner();
        updateWatchlistCollapseLabel();
    } catch (e) {
        showToast('Error', 'Failed to load pool: ' + (e.message || e), 'danger');
    }
}
```

- [ ] **Step 6: Commit**

```bash
git add app/static/js/app.js
git commit -m "feat: wire showWatchlist, loadWatchlist, hideAllViews update"
```

---

## Task 3: JS — Collapsible pool section

**Files:**
- Modify: `app/static/js/app.js`

- [ ] **Step 1: Add collapse functions**

After the `loadPool()` function, add:

```javascript
function initWatchlistCollapse() {
    watchlistPoolCollapsed = localStorage.getItem('watchlist_pool_collapsed') === 'true';
    applyWatchlistCollapseState();
}

function toggleWatchlistPool() {
    watchlistPoolCollapsed = !watchlistPoolCollapsed;
    localStorage.setItem('watchlist_pool_collapsed', watchlistPoolCollapsed);
    applyWatchlistCollapseState();
}

function applyWatchlistCollapseState() {
    const section = document.getElementById('watchlist-pool-section');
    const icon = document.getElementById('watchlist-collapse-icon');
    if (!section || !icon) return;
    section.style.display = watchlistPoolCollapsed ? 'none' : 'block';
    icon.innerHTML = watchlistPoolCollapsed
        ? '<i class="bi bi-chevron-right"></i>'
        : '<i class="bi bi-chevron-down"></i>';
}

function updateWatchlistCollapseLabel() {
    const el = document.getElementById('watchlist-ticker-count');
    if (el) el.textContent = poolData.length > 0 ? `${poolData.length} ticker${poolData.length !== 1 ? 's' : ''}` : '';
}
```

- [ ] **Step 2: Call `initWatchlistCollapse()` from `showWatchlist()`**

Find the `showWatchlist()` function added in Task 2 and update it:

```javascript
function showWatchlist() {
    hideAllViews();
    setNavActive('nav-watchlist');
    document.getElementById('watchlist-view').style.display = 'block';
    initWatchlistCollapse();
    loadWatchlist();
}
```

- [ ] **Step 3: Update `addToPool()` and `removeFromPool()` to call `loadPool()` not `loadPool()`**

These functions already call `loadPool()` — no change needed. Verify they do not call `showPool()` directly.

- [ ] **Step 4: Commit**

```bash
git add app/static/js/app.js
git commit -m "feat: add watchlist collapsible pool section with localStorage state"
```

---

## Task 4: JS — IV Alert Banner

**Files:**
- Modify: `app/static/js/app.js`

- [ ] **Step 1: Add `renderWatchlistIVAlertBanner()`**

Add this function after `updateWatchlistCollapseLabel()`:

```javascript
function renderWatchlistIVAlertBanner() {
    const banner = document.getElementById('watchlist-iv-alert');
    if (!banner) return;

    const elevated = poolData.filter(t => t.iv_rank !== null && t.iv_rank >= 50);
    if (!elevated.length) {
        banner.style.display = 'none';
        return;
    }

    const chips = elevated.map(t =>
        `<span class="iv-alert-chip" onclick="scrollToPoolCard('${t.ticker}')">` +
        `${t.ticker} <span class="iv-alert-ivr">IVR ${t.iv_rank.toFixed(1)}%</span></span>`
    ).join('');

    banner.innerHTML =
        `<span class="iv-alert-icon"><i class="bi bi-lightning-fill"></i></span>` +
        `<span class="iv-alert-text">High IV Opportunity:</span> ${chips}` +
        `<span class="iv-alert-subtitle">— elevated premium conditions</span>`;
    banner.style.display = 'flex';
}
```

- [ ] **Step 2: Add `scrollToPoolCard()`**

Add after `renderWatchlistIVAlertBanner()`:

```javascript
function scrollToPoolCard(ticker) {
    if (watchlistPoolCollapsed) {
        watchlistPoolCollapsed = false;
        localStorage.setItem('watchlist_pool_collapsed', false);
        applyWatchlistCollapseState();
    }
    setTimeout(() => {
        const card = document.querySelector(`.pool-card[data-ticker="${ticker}"]`);
        if (card) {
            card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            card.classList.add('pool-card-highlight');
            setTimeout(() => card.classList.remove('pool-card-highlight'), 1600);
        }
    }, watchlistPoolCollapsed ? 300 : 0);
}
```

- [ ] **Step 3: Commit**

```bash
git add app/static/js/app.js
git commit -m "feat: add IV alert banner with pool ticker chips"
```

---

## Task 5: JS — Filter strip

**Files:**
- Modify: `app/static/js/app.js`

- [ ] **Step 1: Add filter init and toggle functions**

Add after `scrollToPoolCard()`:

```javascript
function initCalendarFilters() {
    const saved = localStorage.getItem('watchlist_calendar_filters');
    if (saved) {
        try {
            const parsed = JSON.parse(saved);
            calendarFilters = { earnings: true, dteWindows: true, ivAlerts: true, ...parsed };
        } catch (_) {}
    }
    applyCalendarFilterButtons();
}

function applyCalendarFilterButtons() {
    ['earnings', 'dteWindows', 'ivAlerts'].forEach(key => {
        const btn = document.getElementById(`filter-btn-${key}`);
        if (!btn) return;
        btn.classList.toggle('active', calendarFilters[key]);
    });
}

function toggleCalendarFilter(key) {
    calendarFilters[key] = !calendarFilters[key];
    localStorage.setItem('watchlist_calendar_filters', JSON.stringify(calendarFilters));
    applyCalendarFilterButtons();
    renderCalendar();
}
```

- [ ] **Step 2: Call `initCalendarFilters()` from `showWatchlist()`**

Update `showWatchlist()` (modified in Task 3):

```javascript
function showWatchlist() {
    hideAllViews();
    setNavActive('nav-watchlist');
    document.getElementById('watchlist-view').style.display = 'block';
    initWatchlistCollapse();
    initCalendarFilters();
    loadWatchlist();
}
```

- [ ] **Step 3: Commit**

```bash
git add app/static/js/app.js
git commit -m "feat: add calendar filter strip with localStorage persistence"
```

---

## Task 6: JS — Enhanced renderCalendar()

**Files:**
- Modify: `app/static/js/app.js`

- [ ] **Step 1: Add DTE window date helpers at the top of `renderCalendar()`**

Find `function renderCalendar()` (around line 1463). After the existing date variables (`const today`, `const todayStr`), add:

```javascript
// DTE window: 30–45 days from today
const todayDate = new Date();
todayDate.setHours(0, 0, 0, 0);
const dte30 = new Date(todayDate);
dte30.setDate(dte30.getDate() + 30);
const dte45 = new Date(todayDate);
dte45.setDate(dte45.getDate() + 45);
const dte30Str = dte30.toISOString().split('T')[0];
const dte45Str = dte45.toISOString().split('T')[0];

// Pool ticker set for earnings cross-reference
const poolTickerSet = new Set(poolData.map(t => t.ticker));

// IV alert: any pool ticker with IVR >= 50
const hasPoolIVAlert = calendarFilters.ivAlerts && poolData.some(t => t.iv_rank !== null && t.iv_rank >= 50);
```

- [ ] **Step 2: Update the current-month day rendering loop**

Find the loop `for (let day = 1; day <= totalDays; day++)` inside `renderCalendar()`. Update the day class calculation and cell HTML:

Replace the `let dayClass = 'calendar-day';` block and the line `html += '<div class="${dayClass}"...>` up to `html += '<div class="calendar-day-number">${day}</div>';` with:

```javascript
let dayClass = 'calendar-day';
if (isToday) dayClass += ' today';
if (isSelected) dayClass += ' selected';

const inDteWindow = calendarFilters.dteWindows && dateStr >= dte30Str && dateStr <= dte45Str;
if (inDteWindow) dayClass += ' dte-window';

html += `<div class="${dayClass}" onclick="selectCalendarDay('${dateStr}')" style="position:relative;">`;
html += `<div class="calendar-day-number">${day}</div>`;

// DTE label
if (inDteWindow) {
    html += `<div class="calendar-dte-label">DTE</div>`;
}

// IV badge on today
if (isToday && hasPoolIVAlert) {
    html += `<div class="calendar-iv-badge"><i class="bi bi-lightning-fill"></i></div>`;
}
```

- [ ] **Step 3: Update earnings rendering to highlight pool tickers**

Find the earnings rendering block inside the day loop:
```javascript
if (earnings.length > 0) {
    html += '<div class="calendar-expirations">';
    earnings.forEach(earning => {
        ...
        html += `<div class="calendar-earnings-item">...`;
    });
    html += '</div>';
}
```

Replace the `if (earnings.length > 0)` block with:

```javascript
if (calendarFilters.earnings && earnings.length > 0) {
    html += '<div class="calendar-expirations">';
    earnings.forEach(earning => {
        const timeLabel = earning.time === 'bmo' ? 'BMO' : (earning.time === 'amc' ? 'AMC' : '');
        const isPool = poolTickerSet.has(earning.ticker);
        html += `
            <div class="calendar-earnings-item${isPool ? ' pool-earnings' : ''}">
                <i class="bi ${isPool ? 'bi-star-fill' : 'bi-megaphone-fill'}"></i>
                <span class="calendar-earnings-ticker">${earning.ticker}</span>
                ${timeLabel ? `<span class="calendar-earnings-label">${timeLabel}</span>` : ''}
            </div>
        `;
    });
    html += '</div>';
}
```

- [ ] **Step 4: Commit**

```bash
git add app/static/js/app.js
git commit -m "feat: enhance renderCalendar with DTE window, pool earnings highlight, IV badge"
```

---

## Task 7: JS — Enhanced selectCalendarDay()

**Files:**
- Modify: `app/static/js/app.js`

- [ ] **Step 1: Add DTE window section to day detail panel**

Find `function selectCalendarDay(dateStr)` (around line 1583). At the end of the function, after the expirations block is built into `html` but before `tradesContainer.innerHTML = html;`, add:

```javascript
// DTE Window Active section
const todayForDte = new Date();
todayForDte.setHours(0, 0, 0, 0);
const dte30d = new Date(todayForDte); dte30d.setDate(dte30d.getDate() + 30);
const dte45d = new Date(todayForDte); dte45d.setDate(dte45d.getDate() + 45);
const dte30dStr = dte30d.toISOString().split('T')[0];
const dte45dStr = dte45d.toISOString().split('T')[0];

if (calendarFilters.dteWindows && dateStr >= dte30dStr && dateStr <= dte45dStr && poolData.length > 0) {
    const openTickers = new Set(calendarTrades.filter(t => t.status === 'open').map(t => t.ticker));
    const clickedDate = new Date(dateStr + 'T00:00:00');
    const dteNum = Math.round((clickedDate - todayForDte) / (1000 * 60 * 60 * 24));
    const candidates = poolData.filter(t => !openTickers.has(t.ticker));

    if (candidates.length > 0) {
        if (html) html += '<div class="calendar-section-header mt-3"><i class="bi bi-clock me-2"></i>DTE Window Active</div>';
        html += candidates.map(t => {
            const ivrStr = t.iv_rank !== null ? ` · IVR ${t.iv_rank.toFixed(1)}%` : '';
            const quality = t.iv_rank !== null && t.iv_rank >= 50 ? ' · High IV — good selling conditions' : '';
            return `
                <div class="calendar-trade-item">
                    <div class="calendar-trade-info">
                        <div class="calendar-trade-moneyness dte-dot"></div>
                        <div>
                            <div class="calendar-trade-ticker">${t.ticker}</div>
                            <div class="calendar-trade-details">${dteNum} DTE${ivrStr}${quality}</div>
                        </div>
                    </div>
                </div>`;
        }).join('');
    }
}
```

- [ ] **Step 2: Guard `detailsCard.style.display = 'none'` to also check DTE window**

Find the early-return block in `selectCalendarDay()`:
```javascript
if (expirations.length === 0 && earnings.length === 0) {
    detailsCard.style.display = 'none';
    return;
}
```

Replace with:
```javascript
const todayCheck = new Date(); todayCheck.setHours(0, 0, 0, 0);
const d30 = new Date(todayCheck); d30.setDate(d30.getDate() + 30);
const d45 = new Date(todayCheck); d45.setDate(d45.getDate() + 45);
const inDteCheck = calendarFilters.dteWindows && dateStr >= d30.toISOString().split('T')[0] && dateStr <= d45.toISOString().split('T')[0];
const hasDteCandidates = inDteCheck && poolData.some(t => !calendarTrades.filter(tr => tr.status === 'open').map(tr => tr.ticker).includes(t.ticker));

if (expirations.length === 0 && earnings.length === 0 && !hasDteCandidates) {
    detailsCard.style.display = 'none';
    return;
}
```

- [ ] **Step 3: Commit**

```bash
git add app/static/js/app.js
git commit -m "feat: add DTE window active section to calendar day detail panel"
```

---

## Task 8: CSS — New styles

**Files:**
- Modify: `app/static/css/style.css`

- [ ] **Step 1: Add all new styles at the end of style.css**

Append the following block:

```css
/* ============================================================
   WATCHLIST TAB — merged Pool + Calendar
   ============================================================ */

/* Collapse toggle */
.watchlist-collapse-toggle {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    cursor: pointer;
    padding: 0.5rem 0;
    margin-bottom: 0.75rem;
    border-bottom: 1px solid var(--border-color);
    color: var(--text-secondary);
    font-size: 0.875rem;
    font-weight: 600;
    user-select: none;
    transition: color 0.15s;
}
.watchlist-collapse-toggle:hover { color: var(--text-primary); }
.watchlist-collapse-title { flex: 1; }
.watchlist-ticker-count {
    background: var(--surface-elevated);
    border-radius: 99px;
    padding: 0.1rem 0.5rem;
    font-size: 0.75rem;
    color: var(--text-muted);
    font-weight: 400;
}

/* IV Alert Banner */
.watchlist-iv-alert {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.625rem 1rem;
    background: rgba(245, 158, 11, 0.1);
    border: 1px solid var(--accent-amber);
    border-radius: var(--border-radius);
    font-size: 0.875rem;
    flex-wrap: wrap;
}
.iv-alert-icon { color: var(--accent-amber); }
.iv-alert-text { font-weight: 600; color: var(--text-primary); }
.iv-alert-subtitle { color: var(--text-muted); font-size: 0.8rem; }
.iv-alert-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    background: var(--surface-elevated);
    border: 1px solid var(--border-color);
    border-radius: 99px;
    padding: 0.15rem 0.65rem;
    cursor: pointer;
    font-weight: 600;
    font-size: 0.8rem;
    transition: border-color 0.15s;
}
.iv-alert-chip:hover { border-color: var(--accent-amber); }
.iv-alert-ivr { font-weight: 400; color: var(--text-muted); font-size: 0.75rem; }

/* Filter Strip */
.watchlist-filter-strip {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
}
.watchlist-filter-label {
    font-size: 0.8rem;
    color: var(--text-muted);
}
.watchlist-filter-btn {
    display: inline-flex;
    align-items: center;
    padding: 0.25rem 0.75rem;
    border-radius: 99px;
    border: 1px solid var(--border-color);
    background: transparent;
    color: var(--text-muted);
    font-size: 0.8rem;
    cursor: pointer;
    transition: all 0.15s;
    line-height: 1.4;
}
.watchlist-filter-btn.active {
    background: var(--surface-elevated);
    color: var(--text-primary);
    border-color: var(--text-muted);
}
.watchlist-filter-btn:hover { border-color: var(--text-primary); color: var(--text-primary); }

/* Calendar Nav (inline) */
.watchlist-calendar-nav {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}
.watchlist-calendar-nav .calendar-month-label {
    font-weight: 600;
    min-width: 130px;
    text-align: center;
    font-size: 1rem;
}

/* DTE Window cell shading */
.calendar-day.dte-window {
    background: linear-gradient(135deg, transparent 70%, rgba(20, 184, 166, 0.15) 70%);
}
.calendar-dte-label {
    position: absolute;
    top: 2px;
    right: 3px;
    font-size: 0.5rem;
    color: rgb(20, 184, 166);
    font-weight: 700;
    letter-spacing: 0.05em;
    line-height: 1;
    pointer-events: none;
}
.calendar-iv-badge {
    position: absolute;
    top: 2px;
    left: 3px;
    font-size: 0.6rem;
    color: var(--accent-amber);
    line-height: 1;
    pointer-events: none;
}

/* Pool earnings highlight */
.calendar-earnings-item.pool-earnings {
    background: rgba(234, 179, 8, 0.08);
    border-left: 2px solid var(--accent-amber);
    font-weight: 600;
    padding-left: 2px;
}

/* Legend additions */
.legend-dte-stripe {
    display: inline-block;
    width: 14px;
    height: 14px;
    background: linear-gradient(135deg, transparent 60%, rgba(20, 184, 166, 0.4) 60%);
    border: 1px solid var(--border-color);
    border-radius: 2px;
    vertical-align: middle;
}
.legend-iv-icon {
    color: var(--accent-amber);
    font-size: 0.75rem;
    vertical-align: middle;
}

/* DTE dot in day detail */
.calendar-trade-moneyness.dte-dot {
    background: rgba(20, 184, 166, 0.7);
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
}

/* Pool card scroll highlight animation */
.pool-card-highlight {
    animation: pool-card-highlight-pulse 1.5s ease-out;
}
@keyframes pool-card-highlight-pulse {
    0%   { box-shadow: 0 0 0 3px var(--accent-amber); }
    100% { box-shadow: none; }
}

/* Mobile adjustments */
@media (max-width: 768px) {
    .watchlist-filter-strip { gap: 0.35rem; }
    .watchlist-filter-btn { font-size: 0.75rem; padding: 0.2rem 0.55rem; }
    .watchlist-calendar-nav .calendar-month-label { min-width: 110px; font-size: 0.9rem; }
    .iv-alert-chip { font-size: 0.75rem; }
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/css/style.css
git commit -m "feat: add watchlist tab CSS (collapse toggle, IV alert, filter strip, DTE window)"
```

---

## Task 9: Smoke test

No automated tests exist for the frontend. Manually verify each feature after starting the app:

```bash
docker compose build --no-cache && docker compose up -d
# App at http://localhost:8082
```

- [ ] **Check 1: Nav**
  - "Pool" and "Calendar" nav items are gone
  - "Watchlist" nav item is present and active on click

- [ ] **Check 2: VIX banner**
  - Shows at top of page when data loads

- [ ] **Check 3: Collapse toggle**
  - "▼ Watchlist (N tickers)" label shows correct count
  - Clicking collapses the grid; chevron flips to ▶
  - Reload page — collapsed state is remembered
  - Click again — expands; state remembered

- [ ] **Check 4: Pool cards**
  - All existing pool cards render correctly inside the collapsible section
  - Add ticker search works from the page header
  - Remove (×) button works

- [ ] **Check 5: IV Alert Banner**
  - If any pool ticker has IVR ≥ 50: amber banner appears with chips
  - Clicking a chip scrolls to the card (and expands if collapsed)
  - If no elevated tickers: banner is not visible

- [ ] **Check 6: Filter strip**
  - Three pill buttons present; all active (filled) by default
  - Toggling Earnings: earnings dots disappear from calendar
  - Toggling DTE Windows: teal shading disappears from calendar
  - Toggling IV Alerts: ⚡ badge disappears from today cell
  - Reload page — filter state is remembered

- [ ] **Check 7: Calendar DTE window**
  - Days 30–45 days from today have teal corner stripe + "DTE" label
  - Click one of those days: day detail shows "DTE Window Active" section with pool tickers that have no open position
  - Tickers with open positions are absent from the DTE section

- [ ] **Check 8: Pool earnings**
  - Pool ticker earnings show with ⭐ icon and gold border-left
  - Non-pool earnings show with plain megaphone icon

- [ ] **Check 9: Existing calendar features still work**
  - Open position expiration dots still show on correct days
  - Clicking an expiration day still shows the trade detail list
  - Month navigation (← →) and Today button work
  - Monthly stats (Expiring count, Capital Freeing, Premium at Risk) still update

- [ ] **Check 10: Mobile**
  - All above on a 390px viewport (iPhone)
  - Filter buttons wrap but remain tappable
  - Pool cards grid responsive

- [ ] **Step: Final commit if all checks pass**

```bash
git add .
git commit -m "chore: watchlist merge smoke test passed"
```

---

## Self-Review Notes

**Spec coverage:**
- [x] Nav: Pool+Calendar → Watchlist (Task 1)
- [x] Page header with search in actions (Task 1 HTML)
- [x] VIX banner always visible (Task 1 HTML — same element IDs)
- [x] Collapsible pool section with localStorage (Tasks 2, 3)
- [x] IV Alert Banner with clickable chips (Task 4)
- [x] Filter strip with localStorage (Task 5)
- [x] DTE window shading + label (Task 6)
- [x] Pool earnings highlight (Task 6)
- [x] Today IV badge (Task 6)
- [x] Calendar nav inline (Task 1 HTML)
- [x] Legend updates (Task 1 HTML)
- [x] Day detail DTE Window section (Task 7)
- [x] CSS for all new components (Task 8)
- [x] No backend changes (confirmed — all Tasks use existing endpoints)

**Type consistency check:**
- `poolData` — Set in `loadPool()`, read in `renderCalendar()`, `selectCalendarDay()`, `renderWatchlistIVAlertBanner()`, `scrollToPoolCard()` ✓
- `calendarFilters` — Set in `initCalendarFilters()` / `toggleCalendarFilter()`, read in `renderCalendar()` / `selectCalendarDay()` ✓
- `watchlistPoolCollapsed` — Set in `toggleWatchlistPool()` / `initWatchlistCollapse()`, read in `applyWatchlistCollapseState()` / `scrollToPoolCard()` ✓
- `filter-btn-earnings` / `filter-btn-dteWindows` / `filter-btn-ivAlerts` — IDs match HTML and JS ✓
- `watchlist-pool-section` / `watchlist-collapse-toggle` — IDs match HTML and JS ✓
