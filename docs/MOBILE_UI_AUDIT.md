# Mobile UI Audit & Optimization Plan (2026-07-30)

> **STATUS (2026-07-30): passes 1-5 IMPLEMENTED** (commits 84a1938, fdc08b6,
> 57bb11f, f8d53e9, 50f872e — built and deployed on :8600). Still open from
> pass 5: the unified Field-component refactor, the 2-line mobile card grammar,
> and the on-device walkthrough of the five money flows listed at the bottom.

Full audit of the React frontend at phone widths (375-430px reference, dark theme,
often standalone/home-screen). Three parallel sweeps: data views, content views,
forms/text inputs — every finding below carries file:line evidence. Companion to
`UI_MODERNIZATION_PLAN.md` (feature-level); this doc is the layout/interaction layer.

**Verdict:** the responsive skeleton is genuinely good (bottom nav, bottom-sheet
modals, some `pointer: coarse` bumps, safe-area insets) — but the app fails on
phones in four systemic ways, each fixable at the root rather than per-view:

1. **Every text box triggers iOS focus-zoom** — one CSS rule fixes all of it.
2. **Nothing handles the on-screen keyboard** — `100vh` sizing, fixed bottom nav
   riding over the keyboard, composers/Save buttons occluded while typing.
3. **Three classes of silent data loss** — native `confirm()` no-ops in standalone,
   a dismissable modal wrapping a form, and save-gated editors that discard on close.
4. **Layout math that can't shrink** — `repeat(7, 1fr)` grids, `nowrap` badges fed
   variable server strings, and no-wrap flex rows cause page-level horizontal scroll.

---

## P0a — Text boxes (the complaint) and the keyboard

### 1. iOS focus-zoom: every input is under 16px  — ROOT CAUSE, one rule
`styles.css:172-179` sets `input, select, textarea { font: inherit }` and nothing
anywhere sets ≥16px. Effective sizes: body 14px; **12px inside every
`<label className="field">`** (`styles.css:185`, 34 call sites — TradeEditor
why/reflection, Login, intents, Edges, Settings passwords…); 13px in
`.filterbar` (`:378`) and `.sug-quick` (`:445`); 12px inline in TagComposer
(`ui.tsx:188`); 13px inline on the account select (`Shell.tsx:193`). iOS Safari
zooms the page on focusing any text control <16px and never zooms back.

**Fix:** add `font-size: 16px;` to the `input, select, textarea` rule (it targets
the elements directly, so it beats the `.field`/`.filterbar` inheritance), then
delete the inline `fontSize` overrides at `ui.tsx:188`, `Shell.tsx:193`, and the
13px declarations in `.filterbar`/`.sug-quick`. Do NOT add `maximum-scale=1`
(kills accessibility pinch-zoom; the meta is currently correct).

### 2. Keyboard/viewport handling — currently none
- `index.html:5` — add `interactive-widget=resizes-content` to the viewport meta.
- `styles.css:515-516` — `.chat { height: calc(100vh - …) }` → `100dvh`/`svh`;
  the composer starts below the fold with the URL bar visible. Same `100vh` in
  `.shell` (`:67`), `.sidebar` (`:71`), `.login-wrap` (`:355`).
- `ConsultChat.tsx:96` — `maxHeight: "45vh"` history pane inside `.modal-body`'s
  scroller = nested scroll containers that don't shrink for the keyboard; restructure
  the consult sheet as a flex column (history `flex:1; min-height:0`), drop the 45vh.
- **`.bottombar` (`styles.css:116`, fixed, z-50) rides on top of the iOS keyboard**,
  covering the bottom ~56px including focused inputs. Hide it while any text input
  has focus (small `visualViewport`/focus hook — none exists in the app today).
- `.modal-actions` land under the keyboard in every form sheet (TradeEditor, intents,
  Edges, tag editor): make it a `position: sticky; bottom: 0` footer inside
  `.modal-body` with a background.
- Toasts (`styles.css:224`, `bottom: 92px`) render behind the keyboard — the
  "Note to self saved" confirmation is never seen while typing.
- `Chat.tsx:39` scrolls only on new messages — add `onFocus` scroll-into-view on
  composers (Chat, ConsultChat, ReviewThread).

### 3. Typing ergonomics
- **Enter-to-send on soft keyboards:** `Chat.tsx:108-113` and `Logbook.tsx:144-149`
  send on Enter — on a phone there is no Shift+Enter, so newlines are impossible and
  stray Returns fire requests. Gate Enter-send to fine pointers; rely on Send button.
- **`enterKeyHint`, `autoCapitalize`, `autoCorrect`, `spellCheck`: zero uses in the
  codebase.** Ticker fields (`FilterBar.tsx:94`, `Positions.tsx:405`,
  `StrangleTools.tsx:88`, `Research.tsx:475`) need
  `autoCapitalize="characters" autoCorrect="off" spellCheck={false}` — FilterBar
  only fakes uppercase visually with CSS while iOS autocorrect operates on the
  lowercase value. Login username needs `autoCapitalize="none" autoCorrect="off"`.
- **Textareas: no auto-grow anywhere; journaling fields get `rows={2}`**
  (`TradeEditor.tsx:533,537`, `Positions.tsx:440`; day note `rows={3}` at
  `Logbook.tsx:64`). The fields the app exists to collect are 2 lines tall and
  iOS ignores the resize grip. Add one auto-grow textarea component
  (`field-sizing: content` + scrollHeight fallback) and use it everywhere.
- **Modal fights `autoFocus`** (`ui.tsx:70-79`): the panel `.focus()` in a parent
  effect steals focus from child `autoFocus` → keyboard pops and dismisses. Only
  focus the panel when no child already has focus; on coarse pointers delay child
  focus until the sheet animation ends (`styles.css:311`).
- `TradeEditor.tsx:464-485` (+`:383-392`): shortcut chips are nested inside the
  `<label className="field">`, so tapping "use width − credit" also focuses the
  number input and pops the keyboard. Move chips out of the label.
- Numeric fields correctly use `inputMode="decimal"` — keep; prefer
  `type="text" inputMode="decimal"` over `type=number` (scroll-nudge, locale).

## P0b — Silent data loss

1. **Native `confirm()` no-ops in standalone mobile** — the codebase documents this
   itself at `Settings.tsx:180-181`, and the correct two-tap `armed` pattern already
   exists (`Settings.tsx:204-219`). Port it to the four remaining sites:
   `TradeEditor.tsx:121` (ungroup), `Edges.tsx:243` (delete edge),
   `Settings.tsx:301` (revoke API key), `ConsultChat.tsx:73` (clear chat).
2. **Tag rename modal is `dismissable`** (`Settings.tsx:222`) — on mobile the
   backdrop is the whole area above the sheet; a mis-tap discards the rename.
   Drop `dismissable` (per `ui.tsx:35-39`'s own doc comment).
3. **NoteBox collapses out of edit mode on keyboard "Done"** (verified):
   `onBlur` saves (`Logbook.tsx:68`), `onSave` mutates `day.note`
   (`Logbook.tsx:318`), the toast re-renders context consumers, and the `[value]`
   effect (`Logbook.tsx:30-34`) runs `setEditing(!value)` → textarea replaced by
   Markdown mid-session. Fix: don't reset `editing` when `value` changes after a
   save — sync only on date/entity change (key the component on `date`).
4. **Save-gated editors discard silently on X/Close** — TradeEditor (8 selects +
   risk + 2 textareas), IntentComposer, Edge/Mechanism forms. `dismissable=false`
   guards backdrop/ESC but not the ✕ or Cancel. Add a dirty-check (shake + arm)
   or autosave like tags already do.

## P1 — Horizontal overflow & touch targets

### Overflow (page-level horizontal scroll — three confirmed sources)
- `styles.css:398,399,611` — calendar grids use `repeat(7, 1fr)` (+ 58px rail);
  `1fr` floors at min-content, and `−$1,234` / nowrap `.fcal-chip`s push the grid
  past 351px of content width. **Fix: `repeat(7, minmax(0, 1fr))`** + ellipsis on
  cell values; drop the weekly rail below the grid under 640px.
- `styles.css:566` — `.debt-row` (Dashboard) has no `flex-wrap`; contents sum to
  ~520px in a 319px card. Add wrap + let the "missing …" span take a full row.
- `.badge` is `white-space: nowrap` with no max-width (`styles.css:196`) while
  Morning/Signals/Edges pump variable server strings into it
  (`Morning.tsx:238-247` research flags ≈330px+, `:125-129` sentiment gauges,
  `Edges.tsx:356-358`). Under 640px: `white-space: normal; overflow-wrap:
  anywhere; max-width: 100%`.
- Smaller guards: `.tile .value` needs `overflow-wrap: anywhere` (money strings in
  ~142px tiles: Dashboard/Analytics/DDPanel); Analytics avg-win/avg-loss tile
  (`Analytics.tsx:101-109`) stacks its two values; `.entry .side` needs
  `min-width: 0` + break opportunity for condor strike strings (`Logbook.tsx:203`);
  `code` needs `overflow-wrap: anywhere` (`Edges.tsx:160`); `.motd`, `.score-hero`,
  `.fill-row`, `.cal-head` need `flex-wrap`.

### Touch targets (44px floor is already project law — CLAUDE.md §3)
Extend the `pointer: coarse` block (currently only `.btn-sm`, `.seg button`,
`.chip`→40, `.swatch`→28) to:
- `.filterbar select/input` + `.sug-quick` (36px → 44px)
- `.kebab-btn` (~21×32 → 44×44; also **raise `.kebab` z-index above the
  bottombar's 50** — the menu currently opens *underneath* the bottom nav on the
  last rows (`styles.css:586` z-5) — and flip it upward near the viewport bottom;
  close on `pointerdown`, not `click` (`Positions.tsx:215-220` misses iOS taps)
- `.modal-x` (30×30 — the *only* exit on title-less sheets: TradeEditor from
  Morning/Signals) → 44×44
- `.cal-cell` (34×38, the only route into a Logbook day), `th.sortable` (~33px —
  wrap label in a real `<button>`, add `aria-sort`/keyboard)
- clickable `<span className="badge">` → real `<button>`s with a 44px floor:
  `Dashboard.tsx:103-114`, `Signals.tsx:156-165`, `:254-263`, `Morning.tsx:238`
- checkboxes: `Settings.tsx:88-98` fill-repair row isn't a `<label>` (18px target;
  TradeEditor's attach row at `:209` does it right — copy that)
- inline overrides that *undo* the floor: `StrangleTools.tsx:160-168` delete ×
  (`minHeight: 28`, destructive, fires immediately — also needs arming),
  `StrangleTools.tsx:89` (34px), `TradeEditor.tsx:386` (20px chip),
  disclosure rows with no min-height (`Research.tsx:358`, `Morning.tsx:49`,
  `StrangleTools.tsx:236,317,345,433`)
- conviction `<input type=range>` in a 30px band inside a sheet
  (`TradeEditor.tsx:394`, `Positions.tsx:434`) → replace with 5 chip buttons.
- Add `:active` pressed states — `tbody tr:hover`, `.rsch-card:hover`,
  `.cal-cell:hover`, `.starter:hover` are the only feedback today and never fire.

## P2 — Tables, hover-only data, charts

- **Analytics: six 8-column tables** (`Analytics.tsx:133-199`) ≈650px wide in a
  319px card, no sticky first column, no scroll affordance. Fix in
  `SortableTable`: sticky first column, right-edge fade, and a column-priority
  prop (name + n + P&L + Win% on ≤640px behind a "more" toggle).
- **Strangle vol watch: 11 columns** (`StrangleTools.tsx:113-208`) with the
  delete × in column 11 and an expanded-history table *nested inside* the
  scrolling table (`:170-204`). Render as stacked cards under 768px; history
  below the card, not in a `colSpan` row.
- **Settings API keys table** (`Settings.tsx:323-351`): action in the last column
  of an off-screen table → stacked rows on mobile. Add copy-to-clipboard on
  `.key-once` (`:317`) — long-press-selecting a 40-char key is the current flow.
- **Positions legs table** (`Positions.tsx:106-133`): 7 columns + 21-char OCC
  symbols → stacked label/value blocks on mobile.
- **Hover-only `title=` data that exists nowhere else** — surface it:
  moneyness cushion % (`Positions.tsx:205`), calendar day trade/win counts
  (`CalendarHeatmap.tsx:110-112` — make tap open a day-detail popover),
  forward-calendar chip meanings (`ForwardCalendar.tsx:115,124` — tap = day sheet),
  sentiment gauge detail (`Morning.tsx:126`), vol-watch column definitions
  (`StrangleTools.tsx:118-123` — render `MetricLegend` in that section too).
- **Charts:** fixed heights (`EquityChart.tsx:47` 240px, `DDPanel.tsx:66` 280px)
  → clamp by viewport; narrow the Y axes (44/48px ≈ 15% of a phone plot); recharts
  tooltips are hover-shaped — add a static latest-value readout or tap-to-pin with
  dismiss; `EquityChart.tsx:54,60` hard-codes the old `#898781` grey instead of
  `var(--muted)`.
- ConsultChat busy state (`ConsultChat.tsx:116`): multi-minute request with a 12px
  text line and no spinner — reuse Chat's `.thinking` dots. Use a `<textarea>` +
  `enterKeyHint="send"` for the consult composer (`:126`).

## P3 — App-wide UI optimization (the design pass)

1. **Navigation.** The bottom bar renders **all 10 nav items** (`Shell.tsx:76-87`)
   → ~37px per item at 375px with 10px labels. Cut to 5 (Morning, Positions,
   Logbook, Analytics, More) + a "More" sheet for the rest. The **topbar** (title +
   4-way display seg + Sync + account select, `Shell.tsx:172-204`) doesn't fit
   375px either — on mobile collapse display-mode + account into the More/overflow
   menu. Settings is 5 long stacked sections (`Settings.tsx`) — add in-page anchors.
2. **Type scale + tokens.** Kill the 9-11px tail (`.cal-week .wd` 9px, `th .arrow`
   9px, mobile `.fcal-chip` 9px, 10-10.5px labels): floor at 11px with a documented
   scale (11 label / 12 body-small / 14 body / 16 input / 19 tile / 22 h1). Define
   a real `.sym` class — it's only styled as `.entry .sym` (`styles.css:459`), so
   tickers render unstyled in Morning/Research/StrangleTools.
3. **Form system.** One `Field` component that bakes in: 16px input, 44px target,
   label semantics, `enterKeyHint`, ticker/numeric presets, auto-grow textarea;
   real `<form onSubmit>` wrappers (today Login is the app's only `<form>`, all
   else is ad-hoc `onKeyDown`). One confirm pattern (armed two-tap) and one
   disclosure-row component (44px, chevron) replacing the six bespoke ones.
4. **Density modes.** Phone cards currently stack 5-line badge-wrapped headers
   (Logbook meta rows, Signals WtCards, strangle RowCards) — define a 2-line
   mobile card grammar: line 1 = identity + one status, line 2 = numbers;
   everything else behind the expand.
5. **Feature phases continue** per `UI_MODERNIZATION_PLAN.md`: B5 trade detail
   page (mark-to-model curve) → B6 report dimensions → B4 roll campaigns → C8
   day-lock → C9 score re-weight. B5's page should be designed mobile-first from
   day one — it will be the most-opened screen on the phone.

## Suggested order & effort

| Pass | Contents | Effort |
|---|---|---|
| 1 | P0a §1 (16px rule) + viewport meta + dvh swaps + P0b 1-3 (confirm sites, dismissable, NoteBox) | ~half a session; highest value/line in the repo |
| 2 | P0a §2-3: keyboard hook (hide bottombar, sticky modal actions, focus scroll), auto-grow textarea, enterKeyHint/autoCapitalize sweep, Enter-send gating, autoFocus fixes, dirty guards | 1 session |
| 3 | P1: minmax(0,1fr) grids, wrap/overflow guards, coarse-pointer sweep, kebab z-index/flip, badge→button, :active states | 1 session |
| 4 | P2: SortableTable column priority + sticky col, stacked-card table variants, hover-data surfacing, chart sizing | 1-2 sessions |
| 5 | P3: nav restructure, type tokens, Field/Form system, card grammar; then resume UI_MODERNIZATION_PLAN B5+ | ongoing |

Verification: after each pass, walk the five money flows on an actual phone
(log a trade from the debt queue; plan an intent; journal a day note + reply to the
AI review; consult on a held position; check Analytics + calendar), plus one pass in
Safari with the keyboard up in every sheet. The repo has no frontend test rig — the
backend suite (192 tests) doesn't cover any of this; consider a Playwright smoke at
390×844 as part of pass 4-5.
