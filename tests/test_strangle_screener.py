"""Strangle screener math (pure parts — no network in tests)."""
import pandas as pd

from app.services.strangle import _bs_delta, _nearest_by_delta, vrp_series, zscore_last


def test_vrp_series_and_z():
    idx = pd.date_range("2025-01-01", periods=400, freq="B")
    closes = pd.Series(100.0, index=idx) * (1.001 ** pd.Series(range(400), index=idx))
    vix = pd.Series(20.0, index=idx)
    vrp, rv20 = vrp_series(closes, vix)
    # constant drift -> rv ~0, vrp ~0.20, z ~0 (no dispersion -> None guard)
    assert abs(vrp.iloc[-1] - 0.20) < 0.02
    assert zscore_last(vrp) is None or abs(zscore_last(vrp)) < 3

    # inject a vol spike at the end -> positive z
    vix2 = vix.copy()
    vix2.iloc[-1] = 35.0
    vrp2, _ = vrp_series(closes, vix2)
    z = zscore_last(vrp2)
    assert z is not None and z > 2


def test_bs_delta_sanity():
    atm_call = _bs_delta(100, 100, 38 / 365, 0.2, "call")
    assert 0.45 < atm_call < 0.60
    otm_put = _bs_delta(100, 85, 38 / 365, 0.2, "put")
    assert -0.20 < otm_put < 0.0
    assert _bs_delta(100, 100, 0, 0.2, "call") is None


def test_nearest_by_delta():
    rows = [{"strike": 90, "delta_est": -0.10}, {"strike": 95, "delta_est": -0.25},
            {"strike": 100, "delta_est": -0.50}]
    assert _nearest_by_delta(rows, 0.25)["strike"] == 95
    assert _nearest_by_delta([], 0.25) is None


def test_ivr_from_snapshots():
    from app.services.strangle import ivr_from_snapshots
    ivs = [0.20 + i * 0.001 for i in range(80)]  # 0.20 .. 0.279
    ivr, n = ivr_from_snapshots(0.279, ivs)
    assert n == 80 and ivr == 100.0
    ivr, _ = ivr_from_snapshots(0.20, ivs)
    assert ivr == 0.0
    ivr, n = ivr_from_snapshots(0.25, ivs[:10])  # too little history
    assert ivr is None and n == 10


async def test_vol_watch_crud(auth_client, account, monkeypatch):
    from app.services import strangle

    async def fake_metrics(sym):
        if sym == "BAD":
            raise ValueError("not enough history for BAD")
        return {"symbol": sym, "spot": 100.0, "iv": 0.30, "rv20": 0.20,
                "iv_rv": 1.5, "term_ratio": 1.05, "straddle_pct": 4.2,
                "earnings": "2026-08-05", "ivr": None, "ivr_source": "snapshots"}

    monkeypatch.setattr(strangle, "vol_metrics", fake_metrics)

    r = await auth_client.post("/api/journal/strangle/watch", json={"symbol": "nvda"})
    assert r.status_code == 200 and r.json()["symbol"] == "NVDA"
    r = await auth_client.post("/api/journal/strangle/watch", json={"symbol": "BAD"})
    assert r.status_code == 404

    r = await auth_client.get("/api/journal/strangle/watch")
    row = r.json()["rows"][0]
    assert row["symbol"] == "NVDA"
    assert row["iv_rv"] == 1.5
    assert len(row["history"]) == 1  # snapshotted on add
    assert row["ivr_n"] == 1  # collecting

    r = await auth_client.delete("/api/journal/strangle/watch/NVDA")
    assert r.status_code == 200
    r = await auth_client.get("/api/journal/strangle/watch")
    assert r.json()["rows"] == []
