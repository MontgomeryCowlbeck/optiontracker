"""DD panel — pure builder tests (no network). build_dd gets a synthetic
daily frame; the yfinance IO wrapper is not exercised here."""
import numpy as np
import pandas as pd
import pytest

from app.services.dd import build_dd, dd_available

pytestmark = pytest.mark.skipif(
    not dd_available(), reason="quant src tree (watchtower) not on this host")


def _frame(days=400, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-07-21", periods=days)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, days)))
    high = close * (1 + rng.uniform(0.001, 0.02, days))
    low = close * (1 - rng.uniform(0.001, 0.02, days))
    return pd.DataFrame({
        "Open": close, "High": high, "Low": low, "Close": close,
        "Volume": rng.integers(1e6, 5e6, days),
    }, index=idx)


def test_build_dd_shape_and_stats():
    dd = build_dd("TEST", _frame(), atm_iv=0.45, iv_dte=35,
                  earnings="2026-08-05")
    assert dd["symbol"] == "TEST"
    assert dd["as_of"] == "2026-07-21"
    s = dd["stats"]
    assert 0 <= s["rsi14"] <= 100
    assert s["lo52"] <= dd["price"] <= s["hi52"] * 1.05
    assert s["atm_iv"] == 0.45 and s["iv_dte"] == 35
    assert s["rv20"] is not None and s["rv20"] > 0
    assert s["iv_rv_ratio"] == pytest.approx(0.45 / s["rv20"], rel=0.01)
    assert s["earnings"] == "2026-08-05"
    assert isinstance(s["above_sma200"], bool)
    # Chart series: full history so the UI can show timeframes covering the
    # whole zone-detection window (labeled via zone_lookback_days).
    assert len(dd["series"]) == 400
    assert dd["zone_lookback_days"] == 400
    last = dd["series"][-1]
    assert last["c"] == dd["price"]
    assert last["sma50"] is not None and last["sma200"] is not None
    # Zones serialize with the fields the panel renders.
    for z in dd["supports"] + dd["resistances"]:
        assert set(z) >= {"kind", "lo", "hi", "touches", "strength",
                          "last_touch", "distance_pct"}
    assert all(z["kind"] == "support" for z in dd["supports"])


def test_build_dd_without_iv():
    dd = build_dd("TEST", _frame(seed=11))
    assert dd["stats"]["atm_iv"] is None
    assert dd["stats"]["iv_rv_ratio"] is None
