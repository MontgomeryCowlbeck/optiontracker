# Trade Journal — Grouping Core + Mechanisms (Plan 2)

> Executed inline (subagent dispatch is unavailable in this environment). TDD per task, Docker-run test suite, one commit per task.

**Goal:** Turn the fills inbox into journaled trades: manage mechanisms (edges), group selected fills into a `journal_trade` with derived P&L, attach later closes, and edit the 8 manual fields.

**Architecture:** A pure `summarize_journal_fills()` in `calculations.py` derives every auto field (incl. realized P&L = Σ`value` − Σ`fees`) from a set of grouped fills. A thin `journal_trades` service handles validation + persistence. New endpoints live in the existing `app/routers/journal.py`.

**Tech Stack:** FastAPI · aiosqlite · pytest (Docker `ot-test` image, `.env` masked).

## Global Constraints
- Deps pinned; DB access only via `app/database.py` helpers.
- All endpoints use `get_auth_context_with_api_key`, scoped by `auth.account_id`.
- **No inline P&L** — all grouping math in `calculations.py`. Win = `pnl > 0`.
- Idempotent migrations; tests under `tests/`; additive (don't delete old domain).

---

### Task 1: Mechanisms CRUD
**Files:** modify `app/routers/journal.py`; create `tests/test_mechanisms_api.py`

Pydantic `MechanismCreate{name, criteria?, regime?, status?='developing', notes?}` and `MechanismUpdate{all optional}`. Endpoints (all account-scoped):
- `POST /api/journal/mechanisms` → insert; 409 on duplicate name (UNIQUE account_id,name); return row.
- `GET /api/journal/mechanisms` → `{mechanisms:[...]}` newest first.
- `GET /api/journal/mechanisms/{id}` → row or 404.
- `PUT /api/journal/mechanisms/{id}` → update provided fields, set `updated_at=CURRENT_TIMESTAMP`; 404 if not owned.
- `DELETE /api/journal/mechanisms/{id}` → delete; 404 if not owned. (Linked trades keep their `mechanism_id`; FK is non-enforced as elsewhere.)

Tests: create+get, list scoping, duplicate→409, update persists, delete→gone/404, cross-account 404.

---

### Task 2: `summarize_journal_fills` (pure calc)
**Files:** modify `app/services/calculations.py`; create `tests/test_journal_calculations.py`

```python
from datetime import datetime, date

def _parse_dt(s):
    if not s:
        return None
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))

def summarize_journal_fills(fills: list[dict]) -> dict:
    """Derive a journal trade's auto fields from its grouped fills.
    Each fill: option_type, action(BTO/STO/BTC/STC), is_opening, strike,
    expiration, quantity, price, fees, value (TT signed cash), executed_at,
    trade_date, underlying. Raises ValueError if no opening fill."""
    opening = [f for f in fills if f["is_opening"]]
    closing = [f for f in fills if not f["is_opening"]]
    if not opening:
        raise ValueError("a journal trade needs at least one opening fill")

    underlying = opening[0]["underlying"]
    expiration = min(f["expiration"] for f in fills if f.get("expiration"))

    sides = {("long" if f["action"] in ("BTO", "BTC") else "short", f["option_type"])
             for f in opening}
    if len(sides) == 1:
        side, otype = next(iter(sides))
        direction = f"{side}_{otype}"
    else:
        direction = "multi-leg"

    strikes = "/".join(str(s) for s in sorted({f["strike"] for f in opening if f.get("strike") is not None}))

    open_qty = sum(f["quantity"] for f in opening)
    close_qty = sum(f["quantity"] for f in closing)
    fees_total = round(sum(f.get("fees") or 0 for f in fills), 4)

    entry_dt = min(_parse_dt(f["executed_at"]) for f in opening)
    exit_dt = max((_parse_dt(f["executed_at"]) for f in closing), default=None) if closing else None
    entry_at = entry_dt.isoformat() if entry_dt else None
    exit_at = exit_dt.isoformat() if exit_dt else None
    tit = int((exit_dt - entry_dt).total_seconds()) if (entry_dt and exit_dt) else None

    entry_premium = round(abs(sum(f.get("value") or 0 for f in opening)), 2)
    exit_premium = round(abs(sum(f.get("value") or 0 for f in closing)), 2) if closing else None

    is_closed = bool(closing) and open_qty == close_qty
    status = "closed" if is_closed else "open"
    realized_pnl = round(sum(f.get("value") or 0 for f in fills) - fees_total, 2) if is_closed else None
    realized_pnl_pct = (round(realized_pnl / entry_premium * 100, 2)
                        if (realized_pnl is not None and entry_premium) else None)

    try:
        dte = (date.fromisoformat(str(expiration)) - date.fromisoformat(str(opening[0]["trade_date"]))).days
    except (ValueError, TypeError):
        dte = None

    return {
        "underlying": underlying, "direction": direction, "strikes": strikes,
        "expiration": expiration, "dte_at_entry": dte, "is_0dte": (dte == 0),
        "entry_premium": entry_premium, "exit_premium": exit_premium,
        "quantity": open_qty, "fees_total": fees_total,
        "entry_at": entry_at, "exit_at": exit_at, "time_in_trade_seconds": tit,
        "realized_pnl": realized_pnl, "realized_pnl_pct": realized_pnl_pct,
        "status": status,
    }
```
Tests: long_call closed round-trip (e.g. open value −40 fee .64, close value +55 fee .64 → pnl 13.72, pct ~34.3, status closed); open-only (status open, pnl/exit None); 0dte flag true when exp==trade_date; multi-leg direction + joined strikes; time_in_trade seconds; ValueError when no opening.

---

### Task 3: Create journal trade from fills
**Files:** create `app/services/journal_trades.py`; modify `app/routers/journal.py`; create `tests/test_journal_trades_create.py`

`async def create_trade_from_fills(account_id, fill_ids, manual: dict) -> int`:
- Fetch fills by id, scoped account + `journal_trade_id IS NULL` + `dismissed=0`.
- Validate: every requested id present (else `ValueError("fill not available")`); ≥1 opening; single underlying (else ValueError). Summarize.
- Insert `journal_trades` (summary fields + manual: mechanism_id, is_system, conviction, why_entered, thesis_worked, exit_reason, emotional_state, reflection — all optional).
- `UPDATE tt_fills SET journal_trade_id=? WHERE id IN (...) AND account_id=?`.
- Return new id.

`MANUAL_FIELDS = ["mechanism_id","is_system","conviction","why_entered","thesis_worked","exit_reason","emotional_state","reflection"]` (shared constant for create + update).

Endpoint `POST /api/journal/trades` body `{fill_ids:[int], **manual}` → create, return the trade row; `ValueError` → 400.
Tests: group open+close → trade row, fills linked (journal_trade_id set, leave inbox), P&L from summary, manual persisted; no-opening→400; mixed underlying→400; already-grouped id→400.

---

### Task 4: Read / update / attach-fills + list
**Files:** modify `app/routers/journal.py`, `app/services/journal_trades.py`; create `tests/test_journal_trades_manage.py`; update `API.md`

- `GET /api/journal/trades` → `{trades:[...]}` newest first, each with `mechanism_name` (LEFT JOIN mechanisms).
- `GET /api/journal/trades/{id}` → trade + `fills:[...]` (its linked fills) or 404.
- `PUT /api/journal/trades/{id}` → update provided MANUAL_FIELDS, set `updated_at`; 404 if not owned.
- `POST /api/journal/trades/{id}/fills` body `{fill_ids:[int]}` → service `attach_fills(account_id, trade_id, fill_ids)`: validate fills ungrouped + same underlying as trade; link them; re-run `summarize_journal_fills` over ALL fills now linked to the trade; UPDATE the trade's auto fields + status. Returns updated trade. ValueError→400, missing trade→404.

Tests: attach closing fill to open trade flips status→closed and sets realized_pnl; PUT updates fields; GET list + detail include fills/mechanism_name; attach mismatched underlying→400. Update API.md (Trade Journal section + changelog) with all Plan-2 endpoints.
