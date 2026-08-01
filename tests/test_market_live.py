"""market_live — the Morning Refresh live layer. The sentiment read is pure
(quotes in, gauges out), so it's tested without any network; the endpoint test
mocks the yfinance fetch."""
from datetime import datetime, timezone

import pytest

from app.services import market_live
from app.services.market_live import market_session, sentiment_read


def _q(d1, last=100.0):
    return {"last": last, "prev_close": last / (1 + d1 / 100), "d1_pct": d1}


def test_sentiment_risk_on():
    quotes = {
        "SPY": _q(0.8), "QQQ": _q(1.1), "DIA": _q(0.5), "IWM": _q(0.9),
        "TLT": _q(-0.4), "GLD": _q(-0.2),
        "^VIX": {"last": 12.5, "prev_close": 13.2, "d1_pct": -5.3},
        "^VIX3M": {"last": 15.0, "prev_close": 15.1, "d1_pct": -0.7},
    }
    s = sentiment_read(quotes)
    assert s["label"] == "risk-on"
    assert s["score"] is not None and s["score"] >= 0.5
    by_key = {c["key"]: c for c in s["components"]}
    assert by_key["vix_level"]["read"] == "complacent"
    assert by_key["term_structure"]["read"] == "steep contango"
    assert by_key["breadth"]["read"] == "broad risk-on"
    assert by_key["haven_bid"]["read"] == "havens sold"
    # Every component score stays on the stated scale.
    assert all(-1 <= c["score"] <= 1 for c in s["components"])


def test_sentiment_risk_off():
    quotes = {
        "SPY": _q(-2.1), "QQQ": _q(-2.8), "DIA": _q(-1.6), "IWM": _q(-3.0),
        "TLT": _q(0.9), "GLD": _q(1.2),
        "^VIX": {"last": 31.0, "prev_close": 25.0, "d1_pct": 24.0},
        "^VIX3M": {"last": 28.0, "prev_close": 26.0, "d1_pct": 7.7},
    }
    s = sentiment_read(quotes)
    assert s["label"] == "risk-off"
    by_key = {c["key"]: c for c in s["components"]}
    assert by_key["vix_level"]["read"] == "fear"
    assert by_key["vix_change"]["read"] == "spiking"
    assert by_key["term_structure"]["read"] == "backwardation"
    assert by_key["haven_bid"]["read"] == "flight to safety"


def test_sentiment_mixed_is_between():
    quotes = {
        "SPY": _q(0.2), "QQQ": _q(-0.3), "DIA": _q(0.1), "IWM": _q(-0.5),
        "^VIX": {"last": 17.0, "prev_close": 16.8, "d1_pct": 1.2},
        "^VIX3M": {"last": 18.0, "prev_close": 18.0, "d1_pct": 0.0},
    }
    s = sentiment_read(quotes)
    by_key = {c["key"]: c for c in s["components"]}
    assert by_key["breadth"]["read"] == "mixed"
    assert "haven_bid" not in by_key  # TLT/GLD missing -> gauge drops out
    assert s["label"] in ("leaning risk-on", "neutral", "cautious")


def test_haven_bid_needs_magnitude_for_flight():
    # Havens up ~0.5% on a barely-red tape = a bid, not a flight.
    quotes = {
        "SPY": _q(-0.2), "QQQ": _q(-0.8), "DIA": _q(0.3), "IWM": _q(-0.03),
        "TLT": _q(0.6), "GLD": _q(0.5),
        "^VIX": {"last": 19.0, "prev_close": 18.5, "d1_pct": 2.7},
    }
    by_key = {c["key"]: c for c in sentiment_read(quotes)["components"]}
    assert by_key["haven_bid"]["read"] == "haven bid"
    assert by_key["haven_bid"]["score"] == -0.5


def test_sentiment_empty_inputs():
    s = sentiment_read({})
    assert s == {"score": None, "label": "unknown", "components": []}


def test_market_session_et():
    # July = EDT (UTC-4). 2026-07-27 is a Monday.
    assert market_session(datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)) == "pre-market"   # 8:00 ET
    assert market_session(datetime(2026, 7, 27, 14, 30, tzinfo=timezone.utc)) == "regular"     # 10:30 ET
    assert market_session(datetime(2026, 7, 27, 20, 30, tzinfo=timezone.utc)) == "after-hours" # 16:30 ET
    assert market_session(datetime(2026, 7, 27, 2, 0, tzinfo=timezone.utc)) == "closed"        # 22:00 ET Sun
    assert market_session(datetime(2026, 7, 25, 15, 0, tzinfo=timezone.utc)) == "closed"       # Saturday


@pytest.mark.asyncio
async def test_digest_live_endpoint(auth_client, monkeypatch):
    quotes = {
        "SPY": _q(0.4), "QQQ": _q(0.6), "DIA": _q(0.2), "IWM": _q(0.1),
        "TLT": _q(-0.1), "GLD": _q(0.0),
        "^VIX": {"last": 15.0, "prev_close": 15.5, "d1_pct": -3.2},
        "^VIX3M": {"last": 17.0, "prev_close": 17.1, "d1_pct": -0.6},
    }
    monkeypatch.setattr(market_live, "_fetch_quotes_blocking", lambda: quotes)
    market_live._cache.clear()

    r = await auth_client.get("/api/journal/digest/live")
    assert r.status_code == 200
    body = r.json()
    assert body["quotes"]["SPY"]["d1_pct"] == 0.4
    assert body["session"] in ("pre-market", "regular", "after-hours", "closed")
    assert body["sentiment"]["label"] != "unknown"
    assert {c["key"] for c in body["sentiment"]["components"]} == {
        "vix_level", "vix_change", "term_structure", "breadth", "haven_bid"}

    # Second call inside the TTL serves the cache — the fetch must not re-run.
    def _boom():
        raise AssertionError("fetch re-ran inside the cache TTL")
    monkeypatch.setattr(market_live, "_fetch_quotes_blocking", _boom)
    r2 = await auth_client.get("/api/journal/digest/live")
    assert r2.status_code == 200
    assert r2.json()["as_of"] == body["as_of"]
    market_live._cache.clear()
