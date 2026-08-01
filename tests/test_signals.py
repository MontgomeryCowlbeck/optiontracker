"""Signals feed: watchtower cards linked to journal trades + latest S1/pulse."""
import json

from app.config import get_settings
from app.database import get_db

CARD = {"symbol": "CIFR", "tier": "STRONG", "alert_class": "TRADE", "spot": 20.1,
        "rsi": 31.2, "floor": 4.0, "fair": 1.7, "breach": 18.5, "zone_lo": 18.2,
        "zone_hi": 19.0, "zone_touches": 3, "earnings": "2026-08-30",
        "put": {"expiry": "2026-08-21", "dte": 31, "strike": 18.0, "mid": 1.14,
                "prem_pct": 6.3, "otm_pct": 10.4}}
WATCH_CARD = {**CARD, "symbol": "MU", "alert_class": "WATCH", "tier": "MARGINAL"}


def _seed_files(tmp_path):
    wt = tmp_path / "wt"
    live = tmp_path / "live"
    wt.mkdir()
    live.mkdir()
    (wt / "triggers_2026-07-20.json").write_text(json.dumps([CARD, WATCH_CARD]))
    (wt / "pulse_2026-07-21.json").write_text(json.dumps(
        {"date": "2026-07-21", "vix": {"close": 17.0}}))
    (live / "signal_2026-07-21.json").write_text(json.dumps(
        {"date": "2026-07-21", "signals": [{"ticker": "SPY", "signal": "FLAT"}]}))
    return wt, live


async def test_signals_feed(auth_client, account, tmp_path, monkeypatch):
    wt, live = _seed_files(tmp_path)
    settings = get_settings()
    monkeypatch.setattr(settings, "watchtower_dir", str(wt))
    monkeypatch.setattr(settings, "live_signals_dir", str(live))

    # A short-put trade on CIFR entered the day after the card -> "taken".
    async with get_db() as db:
        await db.execute(
            """INSERT INTO journal_trades (account_id, underlying, direction,
                                           status, entry_at)
               VALUES (?, 'CIFR', 'short_put', 'open', '2026-07-21T15:00:00')""",
            (account["id"],),
        )
        await db.commit()

    r = await auth_client.get("/api/journal/signals?days=3650")
    assert r.status_code == 200
    data = r.json()

    cards = {c["symbol"]: c for c in data["watchtower"]}
    assert cards["CIFR"]["taken_trade_id"] is not None
    assert cards["CIFR"]["alert_class"] == "TRADE"
    assert cards["MU"]["taken_trade_id"] is None
    assert cards["MU"]["alert_class"] == "WATCH"
    # TRADE sorts before WATCH within the same day.
    assert data["watchtower"][0]["symbol"] == "CIFR"

    assert data["strategy1"]["date"] == "2026-07-21"
    assert data["strategy1"]["signals"][0]["signal"] == "FLAT"
    assert data["pulse"]["vix"]["close"] == 17.0


async def test_signals_feed_empty_dirs(auth_client, account, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "watchtower_dir", str(tmp_path / "nope"))
    monkeypatch.setattr(settings, "live_signals_dir", str(tmp_path / "nada"))
    r = await auth_client.get("/api/journal/signals")
    assert r.status_code == 200
    assert r.json() == {"watchtower": [], "strategy1": None, "pulse": None}
