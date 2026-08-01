# Design: Merge Pool + Calendar into Watchlist Tab

**Date:** 2026-05-17  
**Status:** Approved  
**Branch:** initial_push

---

## Overview

Merge the existing "Pool" and "Calendar" nav tabs into a single **"Watchlist"** tab. The Pool reflects stocks the user is interested in owning or trading; the Calendar should be populated with information based on what is in the pool and/or on positions currently open. The merged tab uses a vertical stack layout with a collapsible pool section above a pool-aware calendar.

---

## Nav Changes

- Remove `nav-pool` and `nav-calendar` nav items.
- Add a single `nav-watchlist` item labeled **"Watchlist"**.
- `showPool()` and `showCalendar()` both redirect to `showWatchlist()` for backwards compatibility.
- The combined view is `watchlist-view` (replaces both `pool-view` and `calendar-view`).

---

## Page Structure (top to bottom)

```
[ Page Header: "Watchlist" | Refresh | Add Ticker search ]
[ VIX Banner (always visible) ]
[ ▼ Watchlist (N tickers) — collapse toggle ]
    [ Pool ticker card grid ]
[ IV Alert Banner (conditional: IVR ≥ 50 on any pool ticker) ]
[ Filter strip: ☑ Earnings  ☑ DTE Windows  ☑ IV Alerts ]
[ ← Month Year → | Today — calendar nav ]
[ Calendar Legend (updated) ]
[ Calendar Grid (enhanced) ]
[ Day Detail Panel (on click) ]
[ Monthly Stats row ]
```

---

## Section 1: Page Header

- Title: "Watchlist"
- Right-side actions: **Refresh** button + **Add Ticker** search bar (moved from its own row in the old pool view into the header actions area).
- On mobile: buttons collapse to icon-only per the standard `.page-header` / `.page-actions` pattern; search bar remains accessible.

---

## Section 2: VIX Banner

No changes. Stays always visible at the top, same styling and logic as the current Pool tab.

---

## Section 3: Collapsible Pool Section

### Toggle button
```
▼ Watchlist (7 tickers)     [always visible, above card grid]
```
- Clicking toggles the card grid open/closed.
- When collapsed, shows: `▶ Watchlist (7 tickers)` — no other content.
- Ticker count updates dynamically from the loaded pool data.
- Collapse state persists in `localStorage` key `watchlist_pool_collapsed`.

### Card grid
- Exactly the existing pool ticker cards — no changes to card content or layout.
- When empty: existing empty-state message shown inside the collapsible section.

---

## Section 4: IV Alert Banner

Shown **above the calendar**, hidden if no pool tickers have IVR ≥ 50.

**Content:**
```
⚡ High IV Opportunity: TSLA (IVR 67%)  NVDA (IVR 55%)  — elevated premium conditions
```

- Each ticker renders as a clickable chip. Clicking scrolls the page up to the pool card section (and expands it if collapsed).
- Styled similarly to VIX banner: amber/green color depending on severity.
- No new API calls — uses data already loaded by `loadPool()`.
- If all pool tickers have IVR < 50: banner is `display: none` (no empty space).

---

## Section 5: Filter Strip

Always visible between the IV Alert Banner and the calendar nav.

```
Show on calendar:  [✓ Earnings]  [✓ DTE Windows]  [✓ IV Alerts]
```

- Three toggle buttons (pill-style, active = filled, inactive = outline).
- State persists in `localStorage` key `watchlist_calendar_filters` as a JSON object `{ earnings: bool, dteWindows: bool, ivAlerts: bool }`.
- All three default to ON.
- Toggling re-renders the calendar immediately (no API re-fetch).

---

## Section 6: Calendar Enhancements

### 6a. 30-45 DTE Window shading (`dteWindows` filter)

- On render, compute `today + 30` and `today + 45` as date boundaries.
- Any calendar day falling in that inclusive range gets a soft teal/blue background stripe.
- A small `DTE` label appears in the top-right corner of those cells.
- Applies regardless of which pool tickers have open positions (this is a trade opportunity window, not a position tracker).
- Pure frontend math — zero new API calls.

### 6b. Pool earnings highlight (`earnings` filter)

- Earnings events are already fetched from the earnings API.
- Cross-reference earning ticker against the loaded pool ticker list (available in JS after `loadPool()`).
- **Pool earnings**: rendered with a gold star accent + slightly bolder text.
- **Non-pool earnings**: rendered as before (plain megaphone icon).
- Toggling the Earnings filter hides ALL earnings dots (pool and non-pool).

### 6c. Today IV badge (`ivAlerts` filter)

- If any pool ticker has IVR ≥ 50, today's calendar cell gets a small `⚡` badge in the corner.
- Uses the same pool data already in memory — no extra fetch.
- Toggling the IV Alerts filter removes this badge.

### 6d. Calendar nav

Moves from the page header into an inline strip just above the legend:
```
[ ← ]  May 2026  [ → ]  [ Today ]
```

### 6e. Legend update

Add two new legend items:
- `DTE` stripe: "30–45 DTE window"
- `⚡`: "IV opportunity"

---

## Section 7: Day Detail Panel (click)

No structural changes. Gains one new section when the clicked date falls inside the 30-45 DTE window AND the DTE Windows filter is ON:

```
📅 DTE Window Active
  TSLA · 37 DTE · IVR 67% · High IV — good selling conditions
  NVDA · 41 DTE · IVR 55%
```

Only pool tickers without an existing open position are shown here (if you already have a trade open, you don't need a reminder to enter one).

---

## Section 8: Monthly Stats Row

No changes — Expiring count, Capital Freeing Up, Premium at Risk remain as-is.

---

## Data Flow

```
showWatchlist()
  ├── loadPool()     → GET /api/pool/tickers → pool cards, VIX banner, IV alert banner
  └── loadCalendar() → GET /api/trades?status=open + fetchCalendarEarnings()
                       → calendar grid (enhanced with pool data from loadPool result)
```

Both fetches run in parallel. Pool data is stored in a module-level `poolData` variable so the calendar renderer can cross-reference pool tickers and IVR values without a second fetch.

---

## Frontend Files Changed

| File | Changes |
|------|---------|
| `app/static/index.html` | Remove pool-view + calendar-view divs, add watchlist-view; update nav |
| `app/static/js/app.js` | Add `showWatchlist()`, `loadWatchlist()`, collapse logic, filter strip, enhanced `renderCalendar()` |
| `app/static/css/style.css` | New styles: collapse toggle, IV alert banner, filter strip, DTE window shading, pool earnings highlight |

**No backend changes required.** All new features use existing API endpoints.

---

## Out of Scope

- Modifying the Pool or Calendar API endpoints
- Changes to the Analytics, Dashboard, or Strategies tabs
- Persisting filter preferences server-side (localStorage is sufficient)
