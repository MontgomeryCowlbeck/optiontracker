"""Discord notifications — closing the journaling loop.

The auto-tracker already knows the moment a trade appears or closes; these
cards carry that moment to Monty's phone and prompt the journaling while the
context is fresh. Channel: a dedicated tracker webhook
(.secrets/discord_webhook_tracker.txt; env DISCORD_WEBHOOK_TRACKER wins).

Every send is best-effort and never raises into the caller — a Discord outage
must not fail a sync. TRACKER_DISABLE_DISCORD=1 silences everything (tests).
Anti-nag rules live here: one card per sync batch, entry pings only for
trades the tracker just created, everything else waits for the EOD sweep."""
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_SECRET = Path("/projects/quant/.secrets/discord_webhook_tracker.txt")
APP_URL = os.environ.get("TRACKER_APP_URL", "http://192.168.1.224:8600").rstrip("/")

GREEN, AMBER, RED = 0x199E70, 0xFAB219, 0xD03B3B


def webhook_url() -> str:
    if os.environ.get("TRACKER_DISABLE_DISCORD"):
        return ""
    env = os.environ.get("DISCORD_WEBHOOK_TRACKER", "").strip()
    if env:
        return env
    try:
        return _SECRET.read_text().strip()
    except OSError:
        return ""


async def send(embeds: list[dict]) -> bool:
    """POST embeds to the webhook. False (never an exception) on any failure."""
    url = webhook_url()
    if not url or not embeds:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={"embeds": embeds})
            return r.status_code in (200, 204)
    except Exception:
        logger.warning("discord send failed", exc_info=True)
        return False


def _trade_line(t: dict) -> str:
    bits = [t.get("underlying") or "?"]
    if t.get("direction"):
        bits.append(str(t["direction"]).replace("_", " "))
    if t.get("strikes"):
        bits.append(str(t["strikes"]))
    if t.get("expiration"):
        bits.append(f"exp {t['expiration']}")
    if t.get("entry_premium"):
        bits.append(f"${t['entry_premium']:.0f} credit")
    return " · ".join(bits)


def _debt_footer(debt_count: int) -> dict:
    return {"text": (f"journal debt: {debt_count} trade(s) · {APP_URL}"
                     if debt_count else f"journal debt: clear ✓ · {APP_URL}")}


async def notify_sync(new_trades: list[dict], closed_trades: list[dict],
                      debt_count: int, bound: dict | None = None) -> bool:
    """One card per sync batch, only when something actually happened."""
    if not new_trades and not closed_trades:
        return False
    bound = bound or {}
    fields = []
    for t in new_trades:
        how = bound.get(t["id"])
        note = (" — matched your planned trade ✓" if how == "intent"
                else " — Strategy #1 signal day, auto-tagged" if how == "signal"
                else " — name the edge, state the risk")
        fields.append({"name": f"NEW · {_trade_line(t)}",
                       "value": f"[journal it]({APP_URL}/#/positions){note}",
                       "inline": False})
    for t in closed_trades:
        pnl = t.get("realized_pnl")
        pnl_s = f"{'+' if (pnl or 0) >= 0 else ''}${pnl:.2f}" if pnl is not None else "—"
        fields.append({"name": f"CLOSED · {_trade_line(t)} → {pnl_s}",
                       "value": f"[log the exit]({APP_URL}/#/logbook) — reason + reflection",
                       "inline": False})
    n, c = len(new_trades), len(closed_trades)
    title = " / ".join(filter(None, [f"{n} new trade{'s' if n != 1 else ''}" if n else "",
                                     f"{c} closed" if c else ""]))
    embed = {
        "title": f"📓 {title}",
        "color": AMBER if debt_count else GREEN,
        "fields": fields[:10],
        "footer": _debt_footer(debt_count),
    }
    return await send([embed])


async def notify_eod(closed_today: list[dict], debt_items: list[dict]) -> bool:
    """The end-of-day sweep: today's closes + anything still unjournaled.
    Silent when there is nothing to say (no closes AND no debt)."""
    if not closed_today and not debt_items:
        return False
    pnl = sum(t.get("realized_pnl") or 0 for t in closed_today)
    lines = [f"**{len(closed_today)} closed today** · "
             f"{'+' if pnl >= 0 else ''}${pnl:.2f}"] if closed_today else []
    for t in closed_today[:8]:
        p = t.get("realized_pnl")
        lines.append(f"· {_trade_line(t)} → {'+' if (p or 0) >= 0 else ''}${(p or 0):.2f}")
    if debt_items:
        lines.append(f"\n**Unjournaled ({len(debt_items)}):**")
        for d in debt_items[:8]:
            lines.append(f"· {d.get('underlying')} {str(d.get('direction') or '').replace('_', ' ')}"
                         f" — missing {', '.join(d.get('missing') or [])}")
        lines.append(f"\nClear the queue before the tape goes cold → {APP_URL}")
    else:
        lines.append("\nJournal is clean. Streak lives another day. ✓")
    embed = {
        "title": "📓 EOD journal sweep",
        "description": "\n".join(lines),
        "color": AMBER if debt_items else GREEN,
    }
    return await send([embed])
