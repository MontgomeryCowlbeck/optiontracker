# UI Modernization Plan — port_tracker vs TradeZella (2026-07-26)

Source: agent research of TradeZella's 2025-26 product (marketing pages, help-center
docs, changelogs, review aggregators). This doc is the distilled plan for *our* app:
options-only, Tastytrade, single user, dark desk theme.

## Where we already beat them

TradeZella's #1 documented complaint from options traders (StonkJournal, TickerScribe):
multi-leg trades import as **ungrouped legs**, greeks live outside the journal, no roll
chaining, options analytics = one DTE report. Our auto-tracker groups spreads natively,
positions carry live greeks/max-loss/RoR, and the pricing engine can mark-to-model.
**Everything in Phase B widens this moat; nothing they ship closes it.**

Also already at parity or better: playbook rule checklists (mechanisms + sleeve-agnostic
adherence), AI day review/chat (their Zella AI is credit-metered; ours is `claude -p`),
P&L calendar (basic), Zella-Score-style composite (ScoreCard).

## Phase A — quick wins (UI coherence)

1. **Global scope bar + display-mode toggle.** Their persistent top bar (date/account/
   tag/symbol) + app-wide "View" switch is the single biggest feel-of-coherence win.
   We have FilterBar on Dashboard/Analytics only — make it persistent across pages, and
   add a display-mode toggle: **$ / R / % of max risk / % of account / Privacy** (hide
   $ for screenshots). All numbers already come from the server; this is a render-layer
   switch through `format.ts`.
2. **Calendar upgrades** (their most-loved artifact): weekly rail (weekly net P&L +
   traded-day count), monthly total in header, note-dot on days with journal entries,
   configurable cell metric ($ / R / credits opened-closed / theta collected).
3. **Insight badges** (their "Zella Insights", but rule-based code, no ML): computed
   server-side per trade/day — "closed before 50% PT", "held past 21 DTE", "unnamed bet
   (no mechanism)", "off-plan exit", "sized over risk quantum", "green-to-red day".
   Surface on logbook cards + calendar tooltips. Pairs with existing adherence logic.

## Phase B — options-native moat (their permanent gap)

4. **Roll campaigns & wheel lineage.** Link close+open pairs into one campaign (roll =
   same underlying, same day, directional continuation); CSP → assignment → covered-call
   chains as one story. Campaign-level P&L and duration. TT order data has the structure.
5. **Trade detail page** (their strongest screen, ours to out-do): underlying price
   chart with execution markers + short-strike distance band, **mark-to-model spread
   value curve** from the pricing engine (our replay-equivalent for weeks-DTE selling),
   50%-PT line, MAE/MFE from greek snapshots. Tabs: Stats / Rules / Fills / Consult.
6. **Report dimensions engine.** One breakdown engine, tabs per dimension: tag,
   mechanism, underlying, day-of-week, hold-time, **DTE-at-entry, delta-at-entry,
   credit/width, IV-rank-at-entry** (the four they don't have), plus a two-dimension
   cross-analysis. Generalizes the existing by_tag/by_mechanism code.

## Phase C — discipline machinery

7. **Pre-trade intents.** Journal the plan *before* the fill exists; auto-bind to the
   trade on import (underlying + date + structure match). Kills retroactive narration.
8. **Day close ritual.** Their "Finish My Day" locks the day's record immutably —
   fits our philosophy; a soft lock (edits flagged, not blocked) is enough for one user.
9. **Score re-weighted for premium selling.** Their Zella Score 2.0 bands (PF 25%,
   avgW/L 20%, maxDD 20%, win% 15%, recovery 10%, consistency 10%) structurally
   mis-score a 96%-win negative-skew book. Keep the pattern (transparent bands, radar),
   swap sleeves: adherence, 10%-ceiling compliance, tail exposure, PT discipline,
   journaling completeness.
10. **Dashboard as config-defined widget layout** (lite): KPI strip + rearrangeable
    panels from a saved config — not a drag-drop grid builder for one user.

## Explicitly skip

Backtesting + tick replay (we have the research harness), AI credit systems, prop-firm
challenges, Spaces/social/mentor mode, mobile apps (responsive web is fine). Their AI
*agents* (auto-tagger, session review vs plan) → implement as plain code + the existing
`claude -p` pattern where wanted, not as a chat product.

## Suggested order

A1 (scope bar + display modes) → A2 (calendar) → B5 (trade detail) → A3 (badges) →
B6 (reports) → B4 (campaigns) → C7-C9. Each is independently shippable.
