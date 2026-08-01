"""Research section — pure flag/snapshot logic + watchlist/notes API.
Network-dependent paths (yfinance snapshots, AI consult) are mocked."""
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from app.services.research import compute_flags, snapshot_from_dd


def _snap(**over):
    s = {"price": 100.0, "rsi14": 50.0, "rv20": 0.3, "atm_iv": 0.33,
         "iv_rv": 1.1, "support_lo": 90.0, "support_hi": 92.0,
         "support_dist_pct": -8.0, "support_touches": 3, "earnings": None}
    s.update(over)
    return s


def test_flags_quiet_by_default():
    assert compute_flags(_snap()) == []


def test_flag_near_support():
    assert "near_support" in compute_flags(_snap(support_dist_pct=-2.5))
    assert "near_support" in compute_flags(_snap(support_dist_pct=1.0))  # inside zone
    assert "near_support" not in compute_flags(_snap(support_dist_pct=-8.0))
    assert compute_flags(_snap(support_dist_pct=None)) == []


def test_flag_earnings_soon():
    soon = (date.today() + timedelta(days=5)).isoformat()
    far = (date.today() + timedelta(days=20)).isoformat()
    past = (date.today() - timedelta(days=2)).isoformat()
    assert "earnings_soon" in compute_flags(_snap(earnings=soon))
    assert "earnings_soon" not in compute_flags(_snap(earnings=far))
    assert "earnings_soon" not in compute_flags(_snap(earnings=past))


def test_flag_washout_and_rich():
    f = compute_flags(_snap(rsi14=30.0, iv_rv=1.5))
    assert "rsi_washout" in f and "iv_rich" in f


def test_snapshot_from_dd_compacts_and_flags():
    dd = {
        "price": 100.0,
        "stats": {"rsi14": 33.0, "rv20": 0.3, "atm_iv": 0.45,
                  "iv_rv_ratio": 1.5, "earnings": None},
        "supports": [{"lo": 97.0, "hi": 99.0, "distance_pct": -2.0,
                      "touches": 4, "strength": 2.0}],
    }
    s = snapshot_from_dd(dd)
    assert s["support_lo"] == 97.0 and s["support_touches"] == 4
    assert set(s["flags"]) == {"near_support", "rsi_washout", "iv_rich"}


def test_snapshot_from_dd_no_supports():
    dd = {"price": 50.0, "stats": {"rsi14": 60.0, "rv20": 0.4, "atm_iv": None,
                                   "iv_rv_ratio": None, "earnings": None},
          "supports": []}
    s = snapshot_from_dd(dd)
    assert s["support_lo"] is None
    assert s["flags"] == []


# ---- API (snapshot mocked so no network) ----

_FAKE_SNAP = {"price": 400.0, "rsi14": 55.0, "rv20": 0.2, "atm_iv": 0.24,
              "iv_rv": 1.2, "support_lo": 380.0, "support_hi": 385.0,
              "support_dist_pct": -4.0, "support_touches": 3,
              "earnings": None, "flags": []}


async def test_watchlist_add_list_update_remove(auth_client, account):
    with patch("app.services.research.refresh_symbol",
               new=AsyncMock(return_value=_FAKE_SNAP)) as mock_refresh:
        r = await auth_client.post("/api/journal/research/watchlist",
                                   json={"symbol": "msft", "thesis": "core follow",
                                         "assignment_ok": False})
        assert r.status_code == 200
        assert r.json()["symbol"] == "MSFT"
        mock_refresh.assert_awaited_once()

    r = await auth_client.get("/api/journal/research")
    assert r.status_code == 200
    wl = r.json()["watchlist"]
    assert len(wl) == 1
    assert wl[0]["symbol"] == "MSFT"
    assert wl[0]["thesis"] == "core follow"
    assert wl[0]["assignment_ok"] == 0

    r = await auth_client.put("/api/journal/research/watchlist/MSFT",
                              json={"assignment_ok": True})
    assert r.status_code == 200

    r = await auth_client.post("/api/journal/research/MSFT/notes",
                               json={"note": "waiting for 380 retest"})
    assert r.status_code == 200
    r = await auth_client.get("/api/journal/research/MSFT/notes")
    assert [n["note"] for n in r.json()["notes"]] == ["waiting for 380 retest"]

    r = await auth_client.delete("/api/journal/research/watchlist/MSFT")
    assert r.status_code == 200
    r = await auth_client.get("/api/journal/research")
    assert r.json()["watchlist"] == []
    # Notes survive removal — research history is a record.
    r = await auth_client.get("/api/journal/research/MSFT/notes")
    assert len(r.json()["notes"]) == 1


async def test_watchlist_bad_symbol(auth_client, account):
    r = await auth_client.post("/api/journal/research/watchlist",
                               json={"symbol": "MS/FT"})
    assert r.status_code == 400
