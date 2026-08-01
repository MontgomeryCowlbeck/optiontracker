"""Journal trades service — trades are AUTO-DETECTED from fills (position-based,
FIFO), kept current on every sync, and auto-closed when flat or expired. The
trader annotates trades (tags/edge/risk); they never group legs by hand.
Manual create/attach/ungroup remain only as repair tools.

Vocabulary: a trade's MECHANISM is its auto-detected structure (`direction`,
resolved to a playbook row in `mechanisms`); its EDGE is the hypothesis the
trader assigns (`edge_id` -> `edges`)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.database import fetch_all, get_db
from app.services.calculations import (
    coverage_adjusted_direction, summarize_journal_fills,
    net_contracts_by_symbol, expired_realized_pnl,
)

_ET = ZoneInfo("America/New_York")

# Manual journal fields a caller may set on a trade (everything the API can't derive).
MANUAL_FIELDS = [
    "edge_id", "is_system", "conviction", "why_entered",
    "thesis_worked", "exit_reason", "emotional_state", "reflection",
    "planned_risk", "regime_read",
]


async def fetch_trades(account_id: int, *, edge_id: int | None = None,
                       tag_id: int | None = None, underlying: str | None = None,
                       status: str | None = None, date_from: str | None = None,
                       date_to: str | None = None,
                       direction: str | None = None) -> list[dict]:
    """Journal trades with edge name, mechanism (structure playbook) name, and
    their tags attached, newest first. Every filter is optional; dates
    (YYYY-MM-DD) filter on the entry day."""
    where = ["t.account_id = ?"]
    params: list = [account_id]
    if edge_id is not None:
        where.append("t.edge_id = ?")
        params.append(edge_id)
    if underlying:
        where.append("t.underlying = ?")
        params.append(underlying.upper())
    if status:
        where.append("t.status = ?")
        params.append(status)
    if direction:
        where.append("t.direction = ?")
        params.append(direction)
    if date_from:
        where.append("substr(t.entry_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to:
        where.append("substr(t.entry_at, 1, 10) <= ?")
        params.append(date_to)
    if tag_id is not None:
        where.append("t.id IN (SELECT trade_id FROM trade_tags WHERE tag_id = ?)")
        params.append(tag_id)

    trades = await fetch_all(
        f"""SELECT t.*, e.name AS edge_name,
                   m.id AS mechanism_id, m.name AS mechanism_name
            FROM journal_trades t
            LEFT JOIN edges e ON t.edge_id = e.id
            LEFT JOIN mechanisms m
                ON m.account_id = t.account_id AND m.structure = t.direction
            WHERE {' AND '.join(where)}
            ORDER BY t.entry_at DESC, t.id DESC""",
        tuple(params),
    )
    tag_rows = await fetch_all(
        """SELECT tt.trade_id, tg.id, tg.color, tg.name
           FROM trade_tags tt JOIN tags tg ON tg.id = tt.tag_id
           WHERE tg.account_id = ?""",
        (account_id,),
    )
    by_trade: dict[int, list[dict]] = {}
    for r in tag_rows:
        by_trade.setdefault(r["trade_id"], []).append(
            {"id": r["id"], "color": r["color"], "name": r["name"]})
    for t in trades:
        t["tags"] = by_trade.get(t["id"], [])
    return trades


async def set_trade_tags(account_id: int, trade_id: int, tag_ids: list[int]) -> bool:
    """Replace a trade's tag set. Returns False if the trade isn't this account's;
    raises ValueError if any tag isn't."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id FROM journal_trades WHERE id = ? AND account_id = ?",
            (trade_id, account_id),
        )
        if not await cur.fetchone():
            return False
        if tag_ids:
            ph = ",".join("?" * len(tag_ids))
            cur = await db.execute(
                f"SELECT COUNT(*) FROM tags WHERE id IN ({ph}) AND account_id = ?",
                (*tag_ids, account_id),
            )
            if (await cur.fetchone())[0] != len(set(tag_ids)):
                raise ValueError("one or more tags not found")
        await db.execute("DELETE FROM trade_tags WHERE trade_id = ?", (trade_id,))
        for tid in set(tag_ids):
            await db.execute(
                "INSERT INTO trade_tags (trade_id, tag_id) VALUES (?, ?)",
                (trade_id, tid),
            )
        await db.commit()
        return True


# ---------------------------------------------------------------------------
# Auto-detection — the tracker core
# ---------------------------------------------------------------------------

async def _open_trade_states(account_id: int) -> list[dict]:
    """Open trades oldest-first, each with its fills and net position per leg."""
    trades = await fetch_all(
        """SELECT id, underlying, entry_at FROM journal_trades
           WHERE account_id = ? AND status = 'open'
           ORDER BY entry_at, id""",
        (account_id,),
    )
    for t in trades:
        t["fills"] = await fetch_all(
            "SELECT * FROM tt_fills WHERE journal_trade_id = ? ORDER BY executed_at, id",
            (t["id"],),
        )
        t["net"] = net_contracts_by_symbol(t["fills"])
    return trades


async def _refresh_state(state: dict, open_trades: list[dict]) -> None:
    """Re-derive a trade's fills/net after an attach; drop it from the open
    list if the attach closed it."""
    state["fills"] = await fetch_all(
        "SELECT * FROM tt_fills WHERE journal_trade_id = ? ORDER BY executed_at, id",
        (state["id"],),
    )
    state["net"] = net_contracts_by_symbol(state["fills"])
    row = await fetch_all(
        "SELECT status FROM journal_trades WHERE id = ?", (state["id"],))
    if row and row[0]["status"] == "closed":
        open_trades.remove(state)


def _matching_open_trade(open_trades: list[dict], fill: dict) -> tuple[dict | None, bool]:
    """FIFO: the oldest open trade whose net position in this leg can absorb the
    fill — closing fills need the opposite sign, opening fills the same sign
    (a scale-in). Flat trades never match, so each 0DTE round-trip on the same
    strike becomes its own trade instead of contaminating the last one.

    Returns (trade, any_sign_match): any_sign_match distinguishes "an open trade
    holds this leg but the fill doesn't fit" (manual repair) from "nothing holds
    this leg at all" (a pre-epoch orphan)."""
    sym = fill["option_symbol"]
    fill_sign = 1 if fill["action"] in ("BTO", "BTC") else -1
    want = -fill_sign if not fill["is_opening"] else fill_sign
    any_sign_match = False
    for t in open_trades:
        net = t["net"].get(sym, 0)
        if net == 0 or (1 if net > 0 else -1) != want:
            continue
        any_sign_match = True
        # A closing fill must fit inside the trade's open leg — one fill closing
        # contracts from several trades can't be split, so it goes to manual repair
        # rather than corrupting the oldest trade's books.
        if not fill["is_opening"] and abs(net) < fill["quantity"]:
            continue
        return t, True
    return None, any_sign_match


async def auto_group_fills(account_id: int) -> dict:
    """Walk ungrouped fills chronologically and place each one:
    opening + no open position -> new trade (all opening fills of the same TT
    order become its legs); opening + same-sign open leg -> scale-in attach;
    closing -> FIFO attach to the oldest open trade holding that leg (trades
    auto-close when every contract is matched).

    The journal epoch (2026-07-14) is a hard floor: fills dated before it, and
    closing fills whose opening trade predates it (no open trade holds the leg),
    are auto-dismissed — pre-epoch positions do not exist here. A closing fill
    that matches an open trade but doesn't fit its leg stays unmatched for
    manual repair."""
    from app.services.journal_settings import JOURNAL_EPOCH

    fills = await fetch_all(
        """SELECT * FROM tt_fills
           WHERE account_id = ? AND journal_trade_id IS NULL AND dismissed = 0
           ORDER BY executed_at, id""",
        (account_id,),
    )
    open_trades = await _open_trade_states(account_id)
    counts = {"trades_created": 0, "fills_attached": 0, "unmatched": 0,
              "dismissed_pre_epoch": 0}
    done: set[int] = set()

    async def _dismiss(fill_id: int) -> None:
        async with get_db() as db:
            await db.execute("UPDATE tt_fills SET dismissed = 1 WHERE id = ?", (fill_id,))
            await db.commit()

    for f in fills:
        if f["id"] in done:
            continue
        if str(f.get("trade_date") or "") < JOURNAL_EPOCH:
            await _dismiss(f["id"])
            done.add(f["id"])
            counts["dismissed_pre_epoch"] += 1
            continue
        target, sign_matched = _matching_open_trade(open_trades, f)
        if target is not None:
            await attach_fills(account_id, target["id"], [f["id"]])
            done.add(f["id"])
            counts["fills_attached"] += 1
            await _refresh_state(target, open_trades)
            continue
        if not f["is_opening"]:
            if sign_matched:
                counts["unmatched"] += 1     # fits nowhere cleanly — repair by hand
            else:
                await _dismiss(f["id"])      # closes a pre-epoch position — not ours
                done.add(f["id"])
                counts["dismissed_pre_epoch"] += 1
            continue
        # New trade: this opening fill plus its order siblings (opening legs of
        # the same TT order = the spread as placed).
        batch = [f]
        if f["order_id"]:
            batch += [g for g in fills
                      if g["id"] not in done and g["id"] != f["id"]
                      and g["is_opening"] and g["order_id"] == f["order_id"]]
        trade_id = await create_trade_from_fills(
            account_id, [b["id"] for b in batch], {})
        done.update(b["id"] for b in batch)
        counts["trades_created"] += 1
        state = {"id": trade_id, "underlying": f["underlying"]}
        open_trades.append(state)
        await _refresh_state(state, open_trades)

    return counts


async def close_expired_trades(account_id: int) -> int:
    """Close open trades whose every open leg is past expiration — expiring
    worthless produces no fill, so this sweep is what ends those trades.
    P&L is the canonical expired formula; exit stamps at expiry close."""
    today = datetime.now(_ET).date().isoformat()
    open_trades = await _open_trade_states(account_id)
    closed = 0
    async with get_db() as db:
        for t in open_trades:
            open_syms = {s for s, q in t["net"].items() if q != 0}
            exps = {str(f["expiration"]) for f in t["fills"]
                    if f["option_symbol"] in open_syms and f.get("expiration")}
            if not exps or max(exps) >= today:
                continue
            pnl = expired_realized_pnl(t["fills"])
            entry_premium = round(abs(sum(f.get("value") or 0
                                          for f in t["fills"] if f["is_opening"])), 2)
            pct = round(pnl / entry_premium * 100, 2) if entry_premium else None
            await db.execute(
                """UPDATE journal_trades
                   SET status = 'closed', exit_premium = 0, realized_pnl = ?,
                       realized_pnl_pct = ?, exit_at = ?,
                       exit_reason = COALESCE(exit_reason, 'expired'),
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND status = 'open'""",
                (pnl, pct, f"{max(exps)}T16:00:00", t["id"]),
            )
            closed += 1
        await db.commit()
    return closed


async def reclassify_covered_calls(account_id: int,
                                   shares_by_underlying: dict[str, float]) -> int:
    """Upgrade open short_call trades to covered_call when the account holds
    the shares (>=100/contract), and revert if the shares are gone. Only OPEN
    trades: coverage at close time for historical trades is unknowable from
    live positions, so closed rows keep whatever they were labeled while open."""
    rows = await fetch_all(
        """SELECT id, underlying, direction FROM journal_trades
           WHERE account_id = ? AND status = 'open'
             AND direction IN ('short_call', 'covered_call')""",
        (account_id,),
    )
    changed = 0
    async with get_db() as db:
        for t in rows:
            fills = await fetch_all(
                "SELECT * FROM tt_fills WHERE journal_trade_id = ?", (t["id"],))
            new = coverage_adjusted_direction(
                t["direction"], fills,
                shares_by_underlying.get((t["underlying"] or "").upper(), 0))
            if new != t["direction"]:
                await db.execute(
                    "UPDATE journal_trades SET direction = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new, t["id"]),
                )
                changed += 1
        await db.commit()
    return changed


async def _fetch_groupable_fills(db, account_id: int, fill_ids: list[int]) -> list[dict]:
    """Fetch the requested fills if they are owned, ungrouped, and not dismissed."""
    placeholders = ",".join("?" * len(fill_ids))
    cur = await db.execute(
        f"""SELECT * FROM tt_fills
            WHERE id IN ({placeholders}) AND account_id = ?
              AND journal_trade_id IS NULL AND dismissed = 0""",
        (*fill_ids, account_id),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in await cur.fetchall()]


def _validate_single_underlying(fills: list[dict]) -> None:
    if len({f["underlying"] for f in fills}) != 1:
        raise ValueError("all fills in a trade must share one underlying")


async def _link_greek_snapshots(db, account_id: int, trade_id: int, option_symbols) -> None:
    """Link any unlinked greek snapshots for the trade's option symbols to it."""
    syms = {s for s in option_symbols if s}
    if not syms:
        return
    ph = ",".join("?" * len(syms))
    await db.execute(
        f"""UPDATE greek_snapshots SET journal_trade_id = ?
            WHERE account_id = ? AND journal_trade_id IS NULL
              AND option_symbol IN ({ph})""",
        (trade_id, account_id, *syms),
    )


async def create_trade_from_fills(account_id: int, fill_ids: list[int], manual: dict) -> int:
    """Group the given fills into a new journal_trade. Returns the new trade id.
    Raises ValueError on validation failure."""
    if not fill_ids:
        raise ValueError("no fills selected")

    async with get_db() as db:
        rows = await _fetch_groupable_fills(db, account_id, fill_ids)
        if len(rows) != len(set(fill_ids)):
            raise ValueError("one or more fills are unavailable (not found, already grouped, or dismissed)")
        _validate_single_underlying(rows)

        summary = summarize_journal_fills(rows)  # raises ValueError if no opening fill

        cols = ["account_id"]
        vals = [account_id]
        for k, v in summary.items():
            cols.append(k)
            vals.append(v)
        for f in MANUAL_FIELDS:
            if f in manual and manual[f] is not None:
                cols.append(f)
                vals.append(manual[f])

        placeholders = ",".join("?" * len(vals))
        cur = await db.execute(
            f"INSERT INTO journal_trades ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        trade_id = cur.lastrowid

        ph = ",".join("?" * len(fill_ids))
        await db.execute(
            f"UPDATE tt_fills SET journal_trade_id = ? WHERE id IN ({ph}) AND account_id = ?",
            (trade_id, *fill_ids, account_id),
        )
        await _link_greek_snapshots(db, account_id, trade_id, [r["option_symbol"] for r in rows])
        await db.commit()
        return trade_id


async def delete_trade(account_id: int, trade_id: int) -> bool:
    """Ungroup a trade: return its fills to the inbox, unlink its greek
    snapshots, and delete the trade. Returns False if not found."""
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id FROM journal_trades WHERE id = ? AND account_id = ?",
            (trade_id, account_id),
        )
        if not await cur.fetchone():
            return False
        await db.execute(
            "UPDATE tt_fills SET journal_trade_id = NULL WHERE journal_trade_id = ? AND account_id = ?",
            (trade_id, account_id),
        )
        await db.execute(
            "UPDATE greek_snapshots SET journal_trade_id = NULL WHERE journal_trade_id = ? AND account_id = ?",
            (trade_id, account_id),
        )
        await db.execute(
            "DELETE FROM journal_trades WHERE id = ? AND account_id = ?",
            (trade_id, account_id),
        )
        await db.commit()
        return True


async def attach_fills(account_id: int, trade_id: int, fill_ids: list[int]) -> bool:
    """Attach more fills (e.g. the closing legs) to an existing trade and
    recompute its derived fields. Returns False if the trade does not exist
    for this account; raises ValueError on fill validation failure."""
    if not fill_ids:
        raise ValueError("no fills selected")

    async with get_db() as db:
        cur = await db.execute(
            "SELECT underlying FROM journal_trades WHERE id = ? AND account_id = ?",
            (trade_id, account_id),
        )
        trade = await cur.fetchone()
        if not trade:
            return False
        trade_underlying = trade[0]

        rows = await _fetch_groupable_fills(db, account_id, fill_ids)
        if len(rows) != len(set(fill_ids)):
            raise ValueError("one or more fills are unavailable (not found, already grouped, or dismissed)")
        if any(r["underlying"] != trade_underlying for r in rows):
            raise ValueError("fills must match the trade's underlying")

        ph = ",".join("?" * len(fill_ids))
        await db.execute(
            f"UPDATE tt_fills SET journal_trade_id = ? WHERE id IN ({ph}) AND account_id = ?",
            (trade_id, *fill_ids, account_id),
        )

        cur2 = await db.execute("SELECT * FROM tt_fills WHERE journal_trade_id = ?", (trade_id,))
        cols2 = [d[0] for d in cur2.description]
        all_fills = [dict(zip(cols2, r)) for r in await cur2.fetchall()]
        summary = summarize_journal_fills(all_fills)

        set_clause = ", ".join(f"{k} = ?" for k in summary)
        await db.execute(
            f"UPDATE journal_trades SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*summary.values(), trade_id),
        )
        await _link_greek_snapshots(db, account_id, trade_id, [r["option_symbol"] for r in all_fills])
        await db.commit()
        return True
