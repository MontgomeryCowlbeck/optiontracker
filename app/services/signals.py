"""Signals feed — surfaces the watchtower sweep (single-name put alerts), the
daily Strategy #1 signal, and the market pulse inside the platform, and links
each alert to the journal: was it taken?

Read-only over the JSON the quant crons already write (triggers_<date>.json,
discovery_<date>.json, pulse_<date>.json, signal_<date>.json). This module
never re-derives signal math — the files are the record of what fired."""
import json
import re
from datetime import date, timedelta
from pathlib import Path

from app.config import get_settings
from app.database import fetch_all

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\.json$")

# An alert is "taken" if a short-put journal trade on that symbol was entered
# within this many days of the card. Wide enough for a next-morning entry,
# tight enough not to claim unrelated trades.
TAKEN_WINDOW_DAYS = 3


def _dated_files(dir_path: str, prefix: str, since: str) -> list[tuple[str, Path]]:
    """[(date, path)] for prefix_<date>.json files with date >= since, ascending."""
    out = []
    for p in sorted(Path(dir_path).glob(f"{prefix}_*.json")):
        m = _DATE_RE.search(p.name)
        if m and m.group(1) >= since:
            out.append((m.group(1), p))
    return out


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _latest(dir_path: str, prefix: str):
    files = _dated_files(dir_path, prefix, "0000-00-00")
    if not files:
        return None, None
    d, p = files[-1]
    return d, _load(p)


async def _short_put_entries(account_id: int) -> list[dict]:
    """(underlying, entry day, trade id) for every short-put journal trade."""
    return await fetch_all(
        """SELECT id, underlying, substr(entry_at, 1, 10) AS entry_day
           FROM journal_trades
           WHERE account_id = ? AND direction = 'short_put'""",
        (account_id,),
    )


def _taken_by(card_date: str, symbol: str, entries: list[dict]):
    until = (date.fromisoformat(card_date)
             + timedelta(days=TAKEN_WINDOW_DAYS)).isoformat()
    for e in entries:
        if e["underlying"] == symbol and card_date <= (e["entry_day"] or "") <= until:
            return e["id"]
    return None


async def signals_feed(account_id: int, days: int = 7) -> dict:
    """The Signals view payload: watchtower cards (linked to journal trades),
    the latest Strategy #1 signal, and the latest pulse."""
    cfg = get_settings()
    since = (date.today() - timedelta(days=days)).isoformat()
    entries = await _short_put_entries(account_id)

    cards = []
    for source, prefix in (("core", "triggers"), ("discovery", "discovery")):
        for d, path in _dated_files(cfg.watchtower_dir, prefix, since):
            for t in (_load(path) or []):
                trade_id = _taken_by(d, t.get("symbol", ""), entries)
                cards.append({
                    "date": d,
                    "source": source,
                    "symbol": t.get("symbol"),
                    "tier": t.get("tier"),
                    "alert_class": t.get("alert_class", "TRADE"),
                    "spot": t.get("spot"),
                    "rsi": t.get("rsi"),
                    "floor": t.get("floor"),
                    "fair": t.get("fair"),
                    "breach": t.get("breach"),
                    "zone_lo": t.get("zone_lo"),
                    "zone_hi": t.get("zone_hi"),
                    "zone_touches": t.get("zone_touches"),
                    "earnings": t.get("earnings"),
                    "put": t.get("put"),
                    "taken_trade_id": trade_id,
                })
    # Newest first; TRADE cards before WATCH within a day.
    cards.sort(key=lambda c: (c["date"], c["alert_class"] == "TRADE",
                              c["symbol"] or ""), reverse=True)

    s1_date, s1 = _latest(cfg.live_signals_dir, "signal")
    pulse_date, pulse = _latest(cfg.watchtower_dir, "pulse")

    return {
        "watchtower": cards,
        "strategy1": {"date": s1_date, **(s1 or {})} if s1 else None,
        "pulse": {"date": pulse_date, **(pulse or {})} if pulse else None,
    }
