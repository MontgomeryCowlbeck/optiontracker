# frontend — React + Vite + TypeScript SPA

The platform UI. Built output lands in `../app/static/dist/`, which FastAPI
serves at `/` (assets under `/static/dist/`). The legacy vanilla SPA in
`app/static/` is untouched fallback/reference.

## Commands

```bash
export PATH="$HOME/.local/bin:$PATH"   # node 22
npm install
npm run dev      # vite dev server on :5173, proxies /api → 127.0.0.1:8600
npm run build    # tsc --noEmit + vite build → ../app/static/dist/
```

Run the backend first: `uvicorn app.main:app --port 8600` (venv active).

## Layout

- `src/api/` — fetch client (JWT + `X-Account-ID` headers, 401 → login) and response types
- `src/state/store.tsx` — toast / auth+accounts / global-filter contexts
- `src/lib/` — hash router, formatters, sanitized markdown renderer
- `src/components/` — shell + nav, shared UI (Pnl, Modal, tiles), FilterBar,
  SortableTable, TradeEditor (the full journal editor), calendar heatmap,
  score card, equity chart (recharts)
- `src/views/` — Login, Dashboard, Positions, Logbook, Analytics, Edges, Chat, Settings
  (trades auto-detect on sync; manual grouping survives only as the Settings fill-repair tool)

Design tokens (dark trading-desk theme, dataviz-skill palette) live at the top
of `src/styles.css`. All financial numbers are rendered server values — the
frontend never recomputes P&L or stats.
