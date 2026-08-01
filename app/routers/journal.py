"""Trade journal router — fills inbox, edges, mechanisms, grouped trades.

Vocabulary: an EDGE is the hypothesis the trader assigns to a trade (drives
journal debt + adherence); a MECHANISM is the auto-detected structure's
playbook, resolved from journal_trades.direction — never hand-assigned."""
import re
from typing import Optional
from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import fetch_all, fetch_one, get_db
from app.auth.dependencies import get_auth_context_with_api_key, AuthContext
from app.services.tastytrade import tastytrade_client
from app.services.fills_sync import sync_fills
from app.services.journal_trades import (
    create_trade_from_fills, attach_fills, delete_trade, fetch_trades, set_trade_tags,
)
from app.services.greek_poller import poll_greeks_once
from app.services.journal_analytics import (
    compute_journal_analytics, compute_motd, compute_calendar, compute_score,
    insight_badges,
)
from app.services import ai_review
from app.services.journal_settings import (
    list_tt_accounts, get_settings, save_settings, resolve_tt_account_number,
)

router = APIRouter(prefix="/api/journal", tags=["journal"])


class JournalSettings(BaseModel):
    tt_account_number: Optional[str] = None
    sync_from_date: Optional[str] = None


class DayNote(BaseModel):
    note: Optional[str] = None
    ai_feedback: Optional[str] = None


class TradeCreate(BaseModel):
    fill_ids: list[int]
    edge_id: Optional[int] = None
    is_system: Optional[bool] = None
    conviction: Optional[int] = None
    why_entered: Optional[str] = None
    thesis_worked: Optional[str] = None
    exit_reason: Optional[str] = None
    emotional_state: Optional[str] = None
    reflection: Optional[str] = None
    planned_risk: Optional[float] = None
    regime_read: Optional[str] = None


class TradeUpdate(BaseModel):
    edge_id: Optional[int] = None
    is_system: Optional[bool] = None
    conviction: Optional[int] = None
    why_entered: Optional[str] = None
    thesis_worked: Optional[str] = None
    exit_reason: Optional[str] = None
    emotional_state: Optional[str] = None
    reflection: Optional[str] = None
    planned_risk: Optional[float] = None
    regime_read: Optional[str] = None
    # CSP sold wanting the shares: assignment is a fill, not a loss
    assignment_intent: Optional[bool] = None
    # explicitly edge-less (a declared one-off) — distinct from edge unset
    no_edge: Optional[bool] = None


class AttachFills(BaseModel):
    fill_ids: list[int]


class TagCreate(BaseModel):
    name: str
    color: str = "#3987e5"  # any #rrggbb


class TagUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None


class TradeTagsSet(BaseModel):
    tag_ids: list[int]


class ChatMessage(BaseModel):
    message: str
    history: list[dict] = []
    # persistent chat: continue this stored session (None + no history = new session)
    session_id: int | None = None


class WatchlistAdd(BaseModel):
    symbol: str
    thesis: Optional[str] = None
    assignment_ok: Optional[bool] = None


class WatchlistUpdate(BaseModel):
    thesis: Optional[str] = None
    assignment_ok: Optional[bool] = None


class ResearchNote(BaseModel):
    note: str


class EdgeCreate(BaseModel):
    name: str
    criteria: Optional[str] = None
    regime: Optional[str] = None
    status: str = "developing"
    notes: Optional[str] = None


class EdgeUpdate(BaseModel):
    name: Optional[str] = None
    criteria: Optional[str] = None
    regime: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class MechanismCreate(BaseModel):
    structure: str      # a classify_strategy direction, e.g. "short_strangle"
    name: str
    rules: Optional[str] = None
    notes: Optional[str] = None


class MechanismUpdate(BaseModel):
    name: Optional[str] = None
    rules: Optional[str] = None
    notes: Optional[str] = None


@router.get("/fills")
async def list_fills(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Ungrouped, non-dismissed fills — the inbox."""
    rows = await fetch_all(
        """SELECT id, external_id, order_id, underlying, option_symbol, option_type,
                  strike, expiration, action, is_opening, quantity, price, value,
                  executed_at, trade_date
           FROM tt_fills
           WHERE account_id = ? AND journal_trade_id IS NULL AND dismissed = 0
           ORDER BY executed_at DESC""",
        (auth.account_id,),
    )
    return {"fills": rows}


@router.get("/positions")
async def open_positions_endpoint(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Open trades valued at the latest captured marks: per-leg net position +
    mark/delta/theta/IV, unrealized P&L, % of entry credit kept, net greeks,
    defined-risk max loss, and a book summary. Assembly lives in
    services/positions.py (shared with the consult agent). Each position also
    carries consult_turns so the UI can show which have a conversation on file."""
    from app.services.positions import open_positions
    book = await open_positions(auth.account_id)
    counts = await fetch_all(
        """SELECT trade_id, COUNT(*) AS n FROM consult_messages
           WHERE account_id = ? AND trade_id IS NOT NULL GROUP BY trade_id""",
        (auth.account_id,),
    )
    by_trade = {c["trade_id"]: c["n"] for c in counts}
    for p in book["positions"]:
        p["consult_turns"] = by_trade.get(p["id"], 0)
    return book


@router.post("/positions/{trade_id}/consult")
async def position_consult(trade_id: int, body: ChatMessage,
                           auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """One turn with the desk consultant about a specific open position — the
    same agent as the research consult, but the clicked trade is the subject
    (no intent gate; straight to state + management). The conversation is
    persisted server-side: history comes from the stored thread, not the
    client. Verdict-carrying replies join the name's research trail."""
    trade = await fetch_one(
        "SELECT id, underlying, status FROM journal_trades WHERE id = ? AND account_id = ?",
        (trade_id, auth.account_id),
    )
    if not trade or trade["status"] != "open" or not trade["underlying"]:
        raise HTTPException(status_code=404, detail="Open trade not found")
    if not ai_review.ai_available():
        raise HTTPException(status_code=503, detail="AI unavailable on this host")
    history = await ai_review.consult_thread(auth.account_id, trade_id=trade_id)
    try:
        reply = await ai_review.consult(auth.account_id, trade["underlying"],
                                        body.message, history,
                                        focus_trade_id=trade_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    await ai_review.log_consult_turn(auth.account_id, trade["underlying"],
                                     trade_id, body.message, reply)
    if "**Verdict" in reply:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO research_briefs (account_id, symbol, kind, content) VALUES (?, ?, 'consult', ?)",
                (auth.account_id, trade["underlying"], reply),
            )
            await db.commit()
    return {"trade_id": trade_id, "symbol": trade["underlying"], "reply": reply}


@router.get("/positions/{trade_id}/consult")
async def position_consult_history(trade_id: int,
                                   auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """The stored consult conversation for this trade (works on closed trades
    too — the record outlives the position)."""
    trade = await fetch_one(
        "SELECT id, underlying FROM journal_trades WHERE id = ? AND account_id = ?",
        (trade_id, auth.account_id),
    )
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    messages = await ai_review.consult_thread(auth.account_id, trade_id=trade_id)
    return {"trade_id": trade_id, "symbol": trade["underlying"], "messages": messages}


@router.delete("/positions/{trade_id}/consult")
async def position_consult_clear(trade_id: int,
                                 auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Drop the stored conversation so the next consult starts fresh."""
    trade = await fetch_one(
        "SELECT id FROM journal_trades WHERE id = ? AND account_id = ?",
        (trade_id, auth.account_id),
    )
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    await ai_review.clear_consult(auth.account_id, trade_id=trade_id)
    return {"cleared": True}


@router.post("/fills/sync")
async def sync_fills_endpoint(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Pull TT transactions into the inbox using the configured account + start date."""
    account_number = await resolve_tt_account_number(auth.account_id)
    if not account_number:
        raise HTTPException(status_code=400, detail="No Tastytrade account configured. Set one in Settings.")
    settings = await get_settings(auth.account_id)
    return await sync_fills(auth.account_id, account_number, since_date=settings["sync_from_date"])


# ---------------------------------------------------------------------------
# Settings — which Tastytrade account to sync, and from when
# ---------------------------------------------------------------------------

@router.get("/tt-accounts")
async def tt_accounts(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """List the Tastytrade accounts visible to the configured login."""
    return {"accounts": await list_tt_accounts()}


@router.post("/accounts/provision")
async def provision_tt_accounts(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Ensure there is one journal account per Tastytrade account, each bound and
    isolated. Reuses/renames an existing unbound account (e.g. the default) before
    creating new ones. Idempotent."""
    tt = await list_tt_accounts()
    if not tt:
        raise HTTPException(status_code=400, detail="No Tastytrade accounts available")

    rows = await fetch_all(
        """SELECT a.id, a.name, js.tt_account_number AS bound
           FROM accounts a LEFT JOIN journal_settings js ON js.account_id = a.id
           WHERE a.user_id = ? ORDER BY a.id""",
        (auth.user_id,),
    )
    bound = {r["bound"]: r["id"] for r in rows if r["bound"]}
    unbound = [r["id"] for r in rows if not r["bound"]]

    async with get_db() as db:
        for a in tt:
            num = a["number"]
            name = num  # unique + unambiguous; the trader can rename later
            if num in bound:
                await db.execute("UPDATE accounts SET name = ? WHERE id = ? AND user_id = ?",
                                 (name, bound[num], auth.user_id))
                continue
            if unbound:
                aid = unbound.pop(0)
                await db.execute("UPDATE accounts SET name = ? WHERE id = ? AND user_id = ?",
                                 (name, aid, auth.user_id))
            else:
                cur = await db.execute(
                    "INSERT INTO accounts (user_id, name, is_default) VALUES (?, ?, 0)",
                    (auth.user_id, name))
                aid = cur.lastrowid
            await db.execute(
                """INSERT INTO journal_settings (account_id, tt_account_number, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(account_id) DO UPDATE SET
                       tt_account_number = excluded.tt_account_number,
                       updated_at = CURRENT_TIMESTAMP""",
                (aid, num))
        await db.commit()

    accounts = await fetch_all(
        """SELECT a.id, a.name, a.is_default, js.tt_account_number
           FROM accounts a LEFT JOIN journal_settings js ON js.account_id = a.id
           WHERE a.user_id = ? ORDER BY a.id""",
        (auth.user_id,),
    )
    return {"accounts": accounts}


@router.get("/settings")
async def read_settings(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    return await get_settings(auth.account_id)


@router.put("/settings")
async def write_settings(body: JournalSettings,
                         auth: AuthContext = Depends(get_auth_context_with_api_key)):
    return await save_settings(auth.account_id, body.tt_account_number, body.sync_from_date)


@router.post("/fills/{fill_id}/dismiss")
async def dismiss_fill(fill_id: int,
                       auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Mark a fill dismissed so it leaves the inbox without being grouped."""
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE tt_fills SET dismissed = 1 WHERE id = ? AND account_id = ?",
            (fill_id, auth.account_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Fill not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Edges — the hypotheses trades are tagged to (the WHY). Manually managed.
# ---------------------------------------------------------------------------

@router.post("/edges")
async def create_edge(body: EdgeCreate,
                      auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Create an edge."""
    async with get_db() as db:
        try:
            cur = await db.execute(
                """INSERT INTO edges (account_id, name, criteria, regime, status, notes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (auth.account_id, body.name, body.criteria, body.regime, body.status, body.notes),
            )
            await db.commit()
            new_id = cur.lastrowid
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(status_code=409, detail="An edge with that name already exists")
            raise
    return await fetch_one("SELECT * FROM edges WHERE id = ?", (new_id,))


@router.get("/edges")
async def list_edges(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """All edges for this account, newest first."""
    rows = await fetch_all(
        "SELECT * FROM edges WHERE account_id = ? ORDER BY id DESC",
        (auth.account_id,),
    )
    return {"edges": rows}


@router.get("/edges/{edge_id}")
async def get_edge(edge_id: int,
                   auth: AuthContext = Depends(get_auth_context_with_api_key)):
    row = await fetch_one(
        "SELECT * FROM edges WHERE id = ? AND account_id = ?",
        (edge_id, auth.account_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Edge not found")
    return row


@router.put("/edges/{edge_id}")
async def update_edge(edge_id: int, body: EdgeUpdate,
                      auth: AuthContext = Depends(get_auth_context_with_api_key)):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [edge_id, auth.account_id]
    async with get_db() as db:
        cur = await db.execute(
            f"UPDATE edges SET {set_clause}, updated_at = CURRENT_TIMESTAMP "
            f"WHERE id = ? AND account_id = ?",
            params,
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Edge not found")
    return await fetch_one("SELECT * FROM edges WHERE id = ?", (edge_id,))


@router.delete("/edges/{edge_id}")
async def delete_edge(edge_id: int,
                      auth: AuthContext = Depends(get_auth_context_with_api_key)):
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM edges WHERE id = ? AND account_id = ?",
            (edge_id, auth.account_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Edge not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Mechanisms — per-structure playbooks (the HOW). Auto-resolved onto trades
# via journal_trades.direction; only the playbook TEXT is edited here.
# ---------------------------------------------------------------------------

@router.get("/mechanisms")
async def list_mechanisms(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Per-structure playbooks with each structure's trade count attached.
    Lazily seeds the standard playbooks so accounts created after startup
    (or fresh test DBs) are never empty."""
    from app.database import seed_mechanisms_for_account
    async with get_db() as db:
        await seed_mechanisms_for_account(db, auth.account_id)
        await db.commit()
    rows = await fetch_all(
        """SELECT m.*, COALESCE(c.n, 0) AS trade_count
           FROM mechanisms m
           LEFT JOIN (SELECT direction, COUNT(*) AS n FROM journal_trades
                      WHERE account_id = ? GROUP BY direction) c
                ON c.direction = m.structure
           WHERE m.account_id = ?
           ORDER BY trade_count DESC, m.structure""",
        (auth.account_id, auth.account_id),
    )
    # directions in the book that have no playbook yet — the UI offers to add one
    orphans = await fetch_all(
        """SELECT direction AS structure, COUNT(*) AS trade_count
           FROM journal_trades
           WHERE account_id = ? AND direction IS NOT NULL
             AND direction NOT IN (SELECT structure FROM mechanisms WHERE account_id = ?)
           GROUP BY direction ORDER BY trade_count DESC""",
        (auth.account_id, auth.account_id),
    )
    return {"mechanisms": rows, "unplaybooked": orphans}


@router.post("/mechanisms")
async def create_mechanism(body: MechanismCreate,
                           auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Add a playbook for a structure (e.g. one the classifier emitted that
    isn't seeded). structure must match the classifier vocabulary to ever
    resolve onto trades."""
    structure = body.structure.strip().lower()
    if not structure:
        raise HTTPException(status_code=400, detail="structure is required")
    async with get_db() as db:
        try:
            cur = await db.execute(
                """INSERT INTO mechanisms (account_id, structure, name, rules, notes)
                   VALUES (?, ?, ?, ?, ?)""",
                (auth.account_id, structure, body.name, body.rules, body.notes),
            )
            await db.commit()
            new_id = cur.lastrowid
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(status_code=409,
                                    detail="That structure already has a playbook")
            raise
    return await fetch_one("SELECT * FROM mechanisms WHERE id = ?", (new_id,))


@router.put("/mechanisms/{mechanism_id}")
async def update_mechanism(mechanism_id: int, body: MechanismUpdate,
                           auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Edit a playbook's display name / rules / notes (structure is fixed)."""
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [mechanism_id, auth.account_id]
    async with get_db() as db:
        cur = await db.execute(
            f"UPDATE mechanisms SET {set_clause}, updated_at = CURRENT_TIMESTAMP "
            f"WHERE id = ? AND account_id = ?",
            params,
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Mechanism not found")
    return await fetch_one("SELECT * FROM mechanisms WHERE id = ?", (mechanism_id,))


@router.delete("/mechanisms/{mechanism_id}")
async def delete_mechanism(mechanism_id: int,
                           auth: AuthContext = Depends(get_auth_context_with_api_key)):
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM mechanisms WHERE id = ? AND account_id = ?",
            (mechanism_id, auth.account_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Mechanism not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Journal trades — grouped fills with derived P&L + manual journal fields
# ---------------------------------------------------------------------------

@router.post("/trades")
async def create_trade(body: TradeCreate,
                       auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Group selected inbox fills into a journal trade."""
    manual = body.model_dump(exclude_unset=True)
    fill_ids = manual.pop("fill_ids", [])
    try:
        trade_id = await create_trade_from_fills(auth.account_id, fill_ids, manual)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await fetch_one("SELECT * FROM journal_trades WHERE id = ?", (trade_id,))


def _trade_filters(edge_id: Optional[int] = None, tag_id: Optional[int] = None,
                   underlying: Optional[str] = None, status: Optional[str] = None,
                   date_from: Optional[str] = None, date_to: Optional[str] = None,
                   direction: Optional[str] = None) -> dict:
    """Shared filter query params — every list/analytics view accepts the same set,
    so any stat can be sliced by edge, tag, underlying, direction (mechanism),
    or date range."""
    return {"edge_id": edge_id, "tag_id": tag_id, "underlying": underlying,
            "status": status, "date_from": date_from, "date_to": date_to,
            "direction": direction}


async def _closed_trades(account_id: int, filters: dict) -> list[dict]:
    trades = await fetch_trades(account_id, **filters)
    return [t for t in trades if t["status"] == "closed" and t.get("realized_pnl") is not None]


@router.get("/trades")
async def list_trades(filters: dict = Depends(_trade_filters),
                      auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Journal trades, newest first, with edge/mechanism names + tags. Filterable."""
    return {"trades": await fetch_trades(auth.account_id, **filters)}


@router.get("/trades/{trade_id}")
async def get_trade(trade_id: int,
                    auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """A journal trade plus the fills it groups."""
    trade = await fetch_one(
        """SELECT t.*, e.name AS edge_name,
                  m.id AS mechanism_id, m.name AS mechanism_name
           FROM journal_trades t
           LEFT JOIN edges e ON t.edge_id = e.id
           LEFT JOIN mechanisms m
               ON m.account_id = t.account_id AND m.structure = t.direction
           WHERE t.id = ? AND t.account_id = ?""",
        (trade_id, auth.account_id),
    )
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    fills = await fetch_all(
        "SELECT * FROM tt_fills WHERE journal_trade_id = ? ORDER BY executed_at",
        (trade_id,),
    )
    greeks = await fetch_all(
        "SELECT * FROM greek_snapshots WHERE journal_trade_id = ? ORDER BY captured_at",
        (trade_id,),
    )
    tags = await fetch_all(
        """SELECT tg.id, tg.color, tg.name
           FROM trade_tags tt JOIN tags tg ON tg.id = tt.tag_id
           WHERE tt.trade_id = ?""",
        (trade_id,),
    )
    # width − credit and 2× credit: the editor offers both as one-tap
    # planned-risk fills (canonical math, never recomputed client-side)
    from app.services.calculations import defined_risk_max_loss, two_x_credit_risk
    direction = trade.get("direction") or ""
    entry_premium = trade.get("entry_premium") or 0
    defined_risk = defined_risk_max_loss(fills, direction, entry_premium)
    two_x_credit = two_x_credit_risk(direction, entry_premium)
    return {**trade, "fills": fills, "greeks": greeks, "tags": tags,
            "defined_risk": defined_risk, "two_x_credit": two_x_credit}


@router.put("/trades/{trade_id}")
async def update_trade(trade_id: int, body: TradeUpdate,
                       auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Edit the manual journal fields on a trade."""
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [trade_id, auth.account_id]
    async with get_db() as db:
        cur = await db.execute(
            f"UPDATE journal_trades SET {set_clause}, updated_at = CURRENT_TIMESTAMP "
            f"WHERE id = ? AND account_id = ?",
            params,
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Trade not found")
    return await fetch_one("SELECT * FROM journal_trades WHERE id = ?", (trade_id,))


@router.delete("/trades/{trade_id}")
async def ungroup_trade(trade_id: int,
                        auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Ungroup a trade — its fills return to the inbox and the trade is deleted."""
    ok = await delete_trade(auth.account_id, trade_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {"ok": True}


@router.post("/trades/{trade_id}/fills")
async def attach_trade_fills(trade_id: int, body: AttachFills,
                             auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Attach more fills (e.g. closing legs) to a trade and recompute it."""
    try:
        found = await attach_fills(auth.account_id, trade_id, body.fill_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="Trade not found")
    return await fetch_one("SELECT * FROM journal_trades WHERE id = ?", (trade_id,))


# ---------------------------------------------------------------------------
# Greeks
# ---------------------------------------------------------------------------

@router.get("/analytics")
async def journal_analytics(filters: dict = Depends(_trade_filters),
                            auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Analysis views + discipline streak over closed journal trades. Accepts the
    same filters as /trades, so every stat can be sliced by edge/tag/date."""
    return compute_journal_analytics(await _closed_trades(auth.account_id, filters))


@router.get("/strangle/screener")
async def strangle_screener(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Large-cap-ETF strangle conditions + live construction math. Decision
    support ONLY — the ledger's VRP-z candidate failed its holdout; the UI
    carries that label and this endpoint's numbers never claim an edge."""
    from app.services.strangle import screener
    return await screener()


class VolWatchAdd(BaseModel):
    symbol: str


@router.get("/strangle/watch")
async def vol_watch(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """The vol watchlist: live metrics per marked name (IV, RV20, IV/RV, IVR,
    term structure, front straddle %, earnings) + the accumulated snapshot
    history that makes IVR-without-an-index and IV-crush review possible."""
    from app.services.strangle import ivr_from_snapshots, vol_metrics

    names = await fetch_all(
        "SELECT symbol FROM vol_watchlist WHERE account_id = ? ORDER BY symbol",
        (auth.account_id,))
    rows = []
    for n in names:
        sym = n["symbol"]
        snaps = await fetch_all(
            """SELECT date, spot, iv, rv20, iv_rv, term_ratio, straddle_pct, earnings
               FROM vol_snapshots WHERE account_id = ? AND symbol = ?
               ORDER BY date DESC LIMIT 40""",
            (auth.account_id, sym))
        try:
            m = await vol_metrics(sym)
        except Exception as e:
            rows.append({"symbol": sym, "error": str(e),
                         "history": snaps})
            continue
        if m.get("ivr") is None:
            all_ivs = await fetch_all(
                "SELECT iv FROM vol_snapshots WHERE account_id = ? AND symbol = ?",
                (auth.account_id, sym))
            ivr, ivr_n = ivr_from_snapshots(m.get("iv"), [r["iv"] for r in all_ivs])
            m["ivr"], m["ivr_n"] = ivr, ivr_n
        rows.append({**m, "history": snaps})
    return {"rows": rows}


@router.post("/strangle/watch")
async def vol_watch_add(body: VolWatchAdd,
                        auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Mark a symbol for vol tracking (validated against live data, then
    snapshotted immediately so day 1 is already in the record)."""
    from app.services.strangle import snapshot_symbol
    sym = body.symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="Symbol is required")
    try:
        await snapshot_symbol(auth.account_id, sym)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO vol_watchlist (account_id, symbol) VALUES (?, ?)",
            (auth.account_id, sym))
        await db.commit()
    return {"ok": True, "symbol": sym}


@router.delete("/strangle/watch/{symbol}")
async def vol_watch_remove(symbol: str,
                           auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Unmark a symbol. Its snapshot history is kept — history is the asset."""
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM vol_watchlist WHERE account_id = ? AND symbol = ?",
            (auth.account_id, symbol.strip().upper()))
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not on the vol watchlist")
    return {"ok": True}


@router.get("/debt")
async def journal_debt_endpoint(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Trades owing journal work (missing edge / risk / exit reason) — the nav
    badge, the dashboard queue, and the Discord cards all read this."""
    from app.services.journal_analytics import journal_debt
    return journal_debt(await fetch_trades(auth.account_id))


@router.get("/digest")
async def morning_digest(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """The Morning page payload: pulse + Strategy #1 + watchtower cards +
    open-book attention items + look-ahead calendar + research flags + open
    intents + journal debt, assembled from stored data only (fast pre-market;
    the vol-watch/screener sections fetch their live numbers separately)."""
    from app.services.digest import assemble_digest
    return await assemble_digest(auth.account_id)


@router.get("/digest/live")
async def morning_digest_live(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """The Refresh-button live layer: latest prices (pre/post-aware) for the
    index/haven/vol set plus the code-computed sentiment gauges. Separate from
    /digest on purpose — the stored digest paints instantly, this one goes to
    the network (cached 60s server-side)."""
    from app.services.market_live import live_market
    return await live_market()


# ---------------------------------------------------------------------------
# Pre-trade intents — the plan stated before the fill exists
# ---------------------------------------------------------------------------

class IntentCreate(BaseModel):
    underlying: str
    direction: Optional[str] = None
    edge_id: Optional[int] = None
    is_system: Optional[bool] = None
    planned_risk: Optional[float] = None
    conviction: Optional[int] = None
    note: Optional[str] = None


@router.get("/intents")
async def list_intents(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    from app.services.intents import expire_stale_intents, open_intents
    await expire_stale_intents(auth.account_id)
    return {"intents": await open_intents(auth.account_id)}


@router.post("/intents")
async def create_intent(body: IntentCreate,
                        auth: AuthContext = Depends(get_auth_context_with_api_key)):
    sym = body.underlying.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="Underlying is required")
    if body.conviction is not None and not (1 <= body.conviction <= 5):
        raise HTTPException(status_code=400, detail="Conviction is 1-5")
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO trade_intents
               (account_id, underlying, direction, edge_id, is_system,
                planned_risk, conviction, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (auth.account_id, sym, body.direction,
             body.edge_id,
             None if body.is_system is None else int(body.is_system),
             body.planned_risk, body.conviction, body.note),
        )
        await db.commit()
        new_id = cur.lastrowid
    return await fetch_one("SELECT * FROM trade_intents WHERE id = ?", (new_id,))


@router.delete("/intents/{intent_id}")
async def cancel_intent(intent_id: int,
                        auth: AuthContext = Depends(get_auth_context_with_api_key)):
    async with get_db() as db:
        cur = await db.execute(
            """UPDATE trade_intents SET status = 'cancelled'
               WHERE id = ? AND account_id = ? AND status = 'open'""",
            (intent_id, auth.account_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Open intent not found")
    return {"ok": True}


@router.get("/calendar/forward")
async def forward_calendar(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """The look-ahead calendar: open-position expirations grouped by date,
    next earnings dates for held + research-watchlist names (yfinance via the
    dd cache — discretionary sleeve), and the 30-45 DTE entry window."""
    from datetime import date, timedelta
    from app.services import dd

    trades = await fetch_trades(auth.account_id, status="open")
    expirations: dict[str, list] = {}
    for t in trades:
        if not t.get("expiration"):
            continue
        expirations.setdefault(str(t["expiration"]), []).append({
            "id": t["id"], "underlying": t.get("underlying"),
            "direction": t.get("direction"), "strikes": t.get("strikes"),
            "max_loss": None,  # row-level risk lives on /positions; keep this light
        })

    held = {t["underlying"] for t in trades if t.get("underlying")}
    watch = await fetch_all(
        "SELECT symbol FROM research_watchlist WHERE account_id = ?",
        (auth.account_id,),
    )
    symbols = sorted(held | {w["symbol"] for w in watch})
    earnings_by_symbol = await dd.fetch_next_earnings(symbols)
    earnings: dict[str, list] = {}
    for sym, day in earnings_by_symbol.items():
        if day:
            earnings.setdefault(str(day), []).append(
                {"symbol": sym, "held": sym in held})

    today = date.today()
    return {
        "today": today.isoformat(),
        "expirations": expirations,
        "earnings": earnings,
        "dte_window": {"start": (today + timedelta(days=30)).isoformat(),
                       "end": (today + timedelta(days=45)).isoformat()},
    }


@router.get("/calendar")
async def pnl_calendar(filters: dict = Depends(_trade_filters),
                       auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Per-day realized P&L (keyed by exit date) for the color calendar, plus
    which days carry a note or AI review."""
    days = compute_calendar(await _closed_trades(auth.account_id, filters))
    notes = await fetch_all(
        """SELECT date, note IS NOT NULL AND note != '' AS has_note,
                  ai_feedback IS NOT NULL AND ai_feedback != '' AS has_review
           FROM journal_day_notes WHERE account_id = ?""",
        (auth.account_id,),
    )
    flags = {r["date"]: r for r in notes}
    for d in days:
        f = flags.get(d["date"])
        d["has_note"] = bool(f and f["has_note"])
        d["has_review"] = bool(f and f["has_review"])
    return {"days": days}


@router.get("/score")
async def journal_score(filters: dict = Depends(_trade_filters),
                        auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """The composite discipline/performance score (six 0-100 components + total).
    Filterable — e.g. score one edge to compare edges."""
    return compute_score(await _closed_trades(auth.account_id, filters))


@router.get("/logbook")
async def logbook(filters: dict = Depends(_trade_filters),
                  auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Trades grouped into per-day logbook pages (newest day first), each with
    that day's stats, a coaching message, and per-trade insight badges.
    Accepts the shared filter set — the global scope bar applies here too."""
    trades = await fetch_trades(auth.account_id, **filters)
    for t in trades:
        t["insights"] = insight_badges(t)
    by_day: dict[str, list] = {}
    for t in trades:
        # A day's page is what CONCLUDED that day (matching the calendar and the
        # day's realized P&L); open trades sit on their entry day until they close.
        if t["status"] == "closed" and t.get("exit_at"):
            day = str(t["exit_at"])[:10]
        else:
            day = str(t.get("entry_at") or "")[:10] or "undated"
        by_day.setdefault(day, []).append(t)
    order = sorted(by_day, reverse=True)

    note_rows = await fetch_all(
        "SELECT date, note, ai_feedback FROM journal_day_notes WHERE account_id = ?",
        (auth.account_id,),
    )
    notes = {r["date"]: r["note"] for r in note_rows}
    feedback = {r["date"]: r["ai_feedback"] for r in note_rows}

    days = []
    for day in order:  # already newest-first (trades sorted desc)
        day_trades = by_day[day]
        closed = [t for t in day_trades if t.get("realized_pnl") is not None]
        days.append({
            "date": day,
            "trades": day_trades,
            "stats": compute_journal_analytics(closed)["overall"],
            "message": compute_motd(closed, 0)["headline"],
            "note": notes.get(day),
            "ai_feedback": feedback.get(day),
        })
    return {"days": days}


@router.put("/day-notes/{date}")
async def save_day_note(date: str, body: DayNote,
                        auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Save the trader's note-to-self and/or pasted AI feedback for a day.
    Each field updates independently — omitting one preserves its stored value."""
    async with get_db() as db:
        await db.execute(
            """INSERT INTO journal_day_notes (account_id, date, note, ai_feedback, updated_at)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(account_id, date) DO UPDATE SET
                   note = COALESCE(excluded.note, journal_day_notes.note),
                   ai_feedback = COALESCE(excluded.ai_feedback, journal_day_notes.ai_feedback),
                   updated_at = CURRENT_TIMESTAMP""",
            (auth.account_id, date, body.note, body.ai_feedback),
        )
        await db.commit()
    row = await fetch_one(
        "SELECT note, ai_feedback FROM journal_day_notes WHERE account_id = ? AND date = ?",
        (auth.account_id, date),
    )
    return {"date": date, "note": row["note"], "ai_feedback": row["ai_feedback"]}


@router.get("/motd")
async def message_of_the_day(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Discipline digest for trades closed today."""
    today = datetime.now(_ET).date().isoformat()
    trades = await fetch_all(
        """SELECT t.* FROM journal_trades t
           WHERE t.account_id = ? AND t.status = 'closed' AND t.realized_pnl IS NOT NULL""",
        (auth.account_id,),
    )
    analytics = compute_journal_analytics(trades)
    today_trades = [t for t in trades if str(t.get("exit_at") or "").startswith(today)]
    out = compute_motd(today_trades, analytics["discipline"]["current_streak"])
    out["date"] = today
    return out


@router.get("/equity")
async def equity_curve(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Daily portfolio value (TT net-liq) vs SPY, oldest first."""
    rows = await fetch_all(
        """SELECT snapshot_date, portfolio_value, spy_price, cash_balance
           FROM benchmark_snapshots
           WHERE account_id = ?
           ORDER BY snapshot_date""",
        (auth.account_id,),
    )
    return {"curve": rows}


@router.post("/greeks/poll")
async def poll_greeks(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Capture one round of live entry/exit greeks for open positions."""
    account_number = await resolve_tt_account_number(auth.account_id)
    if not account_number:
        raise HTTPException(status_code=400, detail="No Tastytrade account configured. Set one in Settings.")
    return await poll_greeks_once(auth.account_id, account_number)


@router.get("/signals")
async def signals(days: int = 7,
                  auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Watchtower alert cards (TRADE/WATCH, linked to any journal trade that
    took them), the latest Strategy #1 daily signal, and the market pulse."""
    from app.services.signals import signals_feed
    return await signals_feed(auth.account_id, days=days)


@router.get("/dd/{symbol}")
async def deep_dive(symbol: str,
                    auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Full code-computed DD for one symbol: price/SMA series, watchtower
    support/resistance zones, RSI/52w/vol/IV context, earnings timing."""
    from app.services.dd import dd_available, fetch_dd
    if not dd_available():
        raise HTTPException(status_code=503, detail="DD engine unavailable (quant src tree not found)")
    if not symbol.isalnum() or len(symbol) > 10:
        raise HTTPException(status_code=400, detail="bad symbol")
    try:
        return await fetch_dd(symbol)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/dd/{symbol}/brief")
async def deep_dive_brief(symbol: str,
                          auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """AI research brief over the DD numbers (WebSearch for catalysts). The
    code computes every figure; the LLM adds context and both-sides prose —
    never a buy/sell call. Slow (up to ~3 min)."""
    from app.services.dd import dd_available, fetch_dd
    if not ai_review.ai_available():
        raise HTTPException(status_code=503, detail="AI unavailable on this host")
    if not dd_available():
        raise HTTPException(status_code=503, detail="DD engine unavailable (quant src tree not found)")
    if not symbol.isalnum() or len(symbol) > 10:
        raise HTTPException(status_code=400, detail="bad symbol")
    try:
        dd_payload = await fetch_dd(symbol)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        brief = await ai_review.generate_dd_brief(auth.account_id, dd_payload)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"symbol": dd_payload["symbol"], "brief": brief}


# ---------------------------------------------------------------------------
# Tags — free-form colored labels on journal trades (one shared pool per account)
# ---------------------------------------------------------------------------

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_tag_color(color: str) -> str:
    if not _COLOR_RE.match(color):
        raise HTTPException(status_code=400, detail="color must be #rrggbb")
    return color.lower()


@router.get("/tags")
async def list_tags(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    rows = await fetch_all(
        "SELECT * FROM tags WHERE account_id = ? ORDER BY name COLLATE NOCASE",
        (auth.account_id,),
    )
    return {"tags": rows}


@router.post("/tags")
async def create_tag(body: TagCreate,
                     auth: AuthContext = Depends(get_auth_context_with_api_key)):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tag name is required")
    color = _validate_tag_color(body.color)
    async with get_db() as db:
        try:
            cur = await db.execute(
                "INSERT INTO tags (account_id, name, color) VALUES (?, ?, ?)",
                (auth.account_id, name, color),
            )
            await db.commit()
            new_id = cur.lastrowid
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(status_code=409, detail="That tag already exists")
            raise
    return await fetch_one("SELECT * FROM tags WHERE id = ?", (new_id,))


@router.put("/tags/{tag_id}")
async def update_tag(tag_id: int, body: TagUpdate,
                     auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Rename and/or recolor a tag; every trade carrying it follows."""
    sets, params = [], []
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Tag name is required")
        sets.append("name = ?")
        params.append(name)
    if body.color is not None:
        sets.append("color = ?")
        params.append(_validate_tag_color(body.color))
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to update")
    async with get_db() as db:
        try:
            cur = await db.execute(
                f"UPDATE tags SET {', '.join(sets)} WHERE id = ? AND account_id = ?",
                (*params, tag_id, auth.account_id),
            )
            await db.commit()
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(status_code=409, detail="That tag name already exists")
            raise
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Tag not found")
    return await fetch_one("SELECT * FROM tags WHERE id = ?", (tag_id,))


@router.delete("/tags/{tag_id}")
async def delete_tag(tag_id: int,
                     auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Delete a tag; trade_tags rows cascade away."""
    async with get_db() as db:
        await db.execute("PRAGMA foreign_keys = ON")
        cur = await db.execute(
            "DELETE FROM tags WHERE id = ? AND account_id = ?",
            (tag_id, auth.account_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Tag not found")
    return {"ok": True}


@router.put("/trades/{trade_id}/tags")
async def set_tags_on_trade(trade_id: int, body: TradeTagsSet,
                            auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Replace the trade's tag set with the given tag ids."""
    try:
        found = await set_trade_tags(auth.account_id, trade_id, body.tag_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not found:
        raise HTTPException(status_code=404, detail="Trade not found")
    tags = await fetch_all(
        """SELECT tg.id, tg.color, tg.name
           FROM trade_tags tt JOIN tags tg ON tg.id = tt.tag_id
           WHERE tt.trade_id = ?""",
        (trade_id,),
    )
    return {"trade_id": trade_id, "tags": tags}


# ---------------------------------------------------------------------------
# AI — day reviews + chat over the journal (headless claude -p on the host)
# ---------------------------------------------------------------------------

@router.get("/ai/status")
async def ai_status(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    return {"available": ai_review.ai_available()}


@router.post("/ai/day-review/{date}")
async def ai_day_review(date: str,
                        auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Generate the day's AI review from computed facts and store it on the day."""
    try:
        review = await ai_review.generate_day_review(auth.account_id, date)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    async with get_db() as db:
        await db.execute(
            """INSERT INTO journal_day_notes (account_id, date, ai_feedback, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(account_id, date) DO UPDATE SET
                   ai_feedback = excluded.ai_feedback, updated_at = CURRENT_TIMESTAMP""",
            (auth.account_id, date, review),
        )
        # A fresh review supersedes the conversation about the old one.
        await db.execute(
            "DELETE FROM day_review_messages WHERE account_id = ? AND date = ?",
            (auth.account_id, date),
        )
        await db.commit()
    return {"date": date, "ai_feedback": review}


@router.get("/ai/day-review/{date}/thread")
async def ai_day_review_thread(date: str,
                               auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """The stored conversation under this day's review."""
    return {"messages": await ai_review.day_review_thread(auth.account_id, date)}


@router.post("/ai/day-review/{date}/reply")
async def ai_day_review_reply(date: str, body: ChatMessage,
                              auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Respond to the day's AI review; the exchange is stored on the day."""
    try:
        reply = await ai_review.day_review_reply(auth.account_id, date, body.message)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"reply": reply}


@router.delete("/ai/day-review/{date}/thread")
async def ai_day_review_thread_clear(date: str,
                                     auth: AuthContext = Depends(get_auth_context_with_api_key)):
    await ai_review.clear_day_review_thread(auth.account_id, date)
    return {"ok": True}


@router.post("/ai/chat")
async def ai_chat(body: ChatMessage,
                  auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """One chat turn grounded in this account's journal data.

    Default: a PERSISTENT session — created when session_id is absent, resumed
    when present; the reply carries the session_id to keep. A client that sends
    its own `history` (and no session_id) gets the legacy stateless behavior:
    nothing stored, no session created."""
    try:
        if body.session_id is None and body.history:
            reply = await ai_review.chat(auth.account_id, body.message, body.history)
            return {"reply": reply, "session_id": None}
        return await ai_review.chat_turn(auth.account_id, body.message, body.session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/ai/chats")
async def ai_chat_list(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Stored chat sessions, most recently active first."""
    return {"sessions": await ai_review.chat_sessions(auth.account_id)}


@router.get("/ai/chats/{session_id}")
async def ai_chat_messages(session_id: int,
                           auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Full transcript of one stored chat."""
    try:
        return {"messages": await ai_review.chat_session_messages(auth.account_id, session_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/ai/chats/{session_id}")
async def ai_chat_delete(session_id: int,
                         auth: AuthContext = Depends(get_auth_context_with_api_key)):
    try:
        await ai_review.delete_chat_session(auth.account_id, session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Research — personal watchlist with daily DD snapshots + the consult agent
# ---------------------------------------------------------------------------

def _clean_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if not s.isalnum() or len(s) > 10:
        raise HTTPException(status_code=400, detail="bad symbol")
    return s


@router.get("/research")
async def research_overview(auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Watchlist cards: thesis + latest snapshot + flags + history + journal links."""
    from app.services.research import overview
    return await overview(auth.account_id)


@router.post("/research/watchlist")
async def research_add(body: WatchlistAdd,
                       auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Add a name and snapshot it immediately (validates the ticker too)."""
    from app.services.research import refresh_symbol
    sym = _clean_symbol(body.symbol)
    try:
        await refresh_symbol(auth.account_id, sym)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    async with get_db() as db:
        await db.execute(
            """INSERT INTO research_watchlist (account_id, symbol, thesis, assignment_ok)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(account_id, symbol) DO UPDATE SET
                 thesis = COALESCE(excluded.thesis, thesis),
                 assignment_ok = COALESCE(excluded.assignment_ok, assignment_ok)""",
            (auth.account_id, sym, body.thesis,
             None if body.assignment_ok is None else int(body.assignment_ok)),
        )
        await db.commit()
    return {"symbol": sym, "added": True}


@router.put("/research/watchlist/{symbol}")
async def research_update(symbol: str, body: WatchlistUpdate,
                          auth: AuthContext = Depends(get_auth_context_with_api_key)):
    sym = _clean_symbol(symbol)
    fields, params = [], []
    if body.thesis is not None:
        fields.append("thesis = ?")
        params.append(body.thesis)
    if body.assignment_ok is not None:
        fields.append("assignment_ok = ?")
        params.append(int(body.assignment_ok))
    if not fields:
        return {"symbol": sym, "updated": False}
    async with get_db() as db:
        cur = await db.execute(
            f"UPDATE research_watchlist SET {', '.join(fields)} WHERE account_id = ? AND symbol = ?",
            (*params, auth.account_id, sym),
        )
        await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="not on the watchlist")
    return {"symbol": sym, "updated": True}


@router.delete("/research/watchlist/{symbol}")
async def research_remove(symbol: str,
                          auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Remove a name. Snapshots/notes/briefs are kept — research history is a record."""
    sym = _clean_symbol(symbol)
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM research_watchlist WHERE account_id = ? AND symbol = ?",
            (auth.account_id, sym),
        )
        await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="not on the watchlist")
    return {"symbol": sym, "removed": True}


@router.post("/research/refresh")
async def research_refresh(symbol: Optional[str] = None,
                           auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Re-snapshot one name (?symbol=) or the whole watchlist."""
    from app.services.research import refresh_all, refresh_symbol
    if symbol:
        sym = _clean_symbol(symbol)
        try:
            snap = await refresh_symbol(auth.account_id, sym)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"refreshed": 1, "failed": [], "latest": snap}
    return await refresh_all(auth.account_id)


@router.get("/research/{symbol}/notes")
async def research_notes(symbol: str,
                         auth: AuthContext = Depends(get_auth_context_with_api_key)):
    sym = _clean_symbol(symbol)
    rows = await fetch_all(
        """SELECT id, note, created_at FROM research_notes
           WHERE account_id = ? AND symbol = ? ORDER BY id DESC""",
        (auth.account_id, sym),
    )
    return {"symbol": sym, "notes": rows}


@router.post("/research/{symbol}/notes")
async def research_add_note(symbol: str, body: ResearchNote,
                            auth: AuthContext = Depends(get_auth_context_with_api_key)):
    sym = _clean_symbol(symbol)
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO research_notes (account_id, symbol, note) VALUES (?, ?, ?)",
            (auth.account_id, sym, body.note),
        )
        await db.commit()
    return {"id": cur.lastrowid, "symbol": sym}


@router.delete("/research/notes/{note_id}")
async def research_delete_note(note_id: int,
                               auth: AuthContext = Depends(get_auth_context_with_api_key)):
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM research_notes WHERE id = ? AND account_id = ?",
            (note_id, auth.account_id),
        )
        await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="note not found")
    return {"deleted": True}


@router.get("/research/{symbol}/briefs")
async def research_briefs(symbol: str,
                          auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Saved AI output for the name, newest first (briefs + consult verdicts)."""
    sym = _clean_symbol(symbol)
    rows = await fetch_all(
        """SELECT id, kind, content, created_at FROM research_briefs
           WHERE account_id = ? AND symbol = ? ORDER BY id DESC LIMIT 20""",
        (auth.account_id, sym),
    )
    return {"symbol": sym, "briefs": rows}


@router.post("/research/{symbol}/daily-update")
async def research_daily_update(symbol: str,
                                auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Regenerate the AI daily card for one name on demand (the scheduler
    writes one nightly). Code computes every number; the LLM writes the read."""
    from app.services.research import daily_update
    try:
        return await daily_update(auth.account_id, _clean_symbol(symbol))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/research/{symbol}/brief")
async def research_run_brief(symbol: str,
                             auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Run the DD research brief and SAVE it to the name's research trail."""
    from app.services.dd import dd_available, fetch_dd
    sym = _clean_symbol(symbol)
    if not ai_review.ai_available():
        raise HTTPException(status_code=503, detail="AI unavailable on this host")
    if not dd_available():
        raise HTTPException(status_code=503, detail="DD engine unavailable")
    try:
        dd_payload = await fetch_dd(sym)
        brief = await ai_review.generate_dd_brief(auth.account_id, dd_payload)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    async with get_db() as db:
        await db.execute(
            "INSERT INTO research_briefs (account_id, symbol, kind, content) VALUES (?, ?, 'brief', ?)",
            (auth.account_id, sym, brief),
        )
        await db.commit()
    return {"symbol": sym, "brief": brief}


@router.post("/research/{symbol}/consult")
async def research_consult(symbol: str, body: ChatMessage,
                           auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """One turn with the consult agent. First turn interrogates intent; the
    concluding turn (the one carrying a Verdict) is saved to the trail. The
    conversation persists server-side per name (history comes from the stored
    thread, not the client)."""
    sym = _clean_symbol(symbol)
    if not ai_review.ai_available():
        raise HTTPException(status_code=503, detail="AI unavailable on this host")
    history = await ai_review.consult_thread(auth.account_id, symbol=sym)
    try:
        reply = await ai_review.consult(auth.account_id, sym, body.message, history)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    await ai_review.log_consult_turn(auth.account_id, sym, None, body.message, reply)
    if "**Verdict**" in reply or "**Verdict" in reply:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO research_briefs (account_id, symbol, kind, content) VALUES (?, ?, 'consult', ?)",
                (auth.account_id, sym, reply),
            )
            await db.commit()
    return {"symbol": sym, "reply": reply}


@router.get("/research/{symbol}/consult")
async def research_consult_history(symbol: str,
                                   auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """The stored name-level consult conversation."""
    sym = _clean_symbol(symbol)
    messages = await ai_review.consult_thread(auth.account_id, symbol=sym)
    return {"symbol": sym, "messages": messages}


@router.delete("/research/{symbol}/consult")
async def research_consult_clear(symbol: str,
                                 auth: AuthContext = Depends(get_auth_context_with_api_key)):
    """Drop the stored conversation so the next consult starts fresh."""
    sym = _clean_symbol(symbol)
    await ai_review.clear_consult(auth.account_id, symbol=sym)
    return {"cleared": True}
