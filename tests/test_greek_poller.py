import pytest
from app.services import greek_poller
from app.services.greek_poller import poll_greeks_once
from app.database import fetch_all

TT_SYM = "QQQ   260628C00480000"
OCC_SYM = "QQQ260628C00480000"


def _position(sym=TT_SYM, qty=1):
    return {"instrument-type": "Equity Option", "symbol": sym, "quantity": qty}


def _mock(monkeypatch, positions, delta=0.48, iv=0.25):
    async def fake_positions(account_number):
        return positions

    async def fake_quotes(symbols, include_greeks=True):
        return {s: {"greeks": {"delta": delta, "mid_iv": iv}} for s in symbols}

    monkeypatch.setattr(greek_poller.tastytrade_client, "get_positions", fake_positions)
    monkeypatch.setattr(greek_poller.tastytrade_client, "get_option_quotes", fake_quotes)


async def test_first_poll_records_entry(account, monkeypatch):
    _mock(monkeypatch, [_position()], delta=0.48)
    res = await poll_greeks_once(account["id"], "5WT1")
    assert res["entries"] == 1
    rows = await fetch_all("SELECT * FROM greek_snapshots WHERE account_id = ?", (account["id"],))
    assert len(rows) == 1
    assert rows[0]["snapshot_type"] == "entry"
    assert rows[0]["option_symbol"] == TT_SYM
    assert rows[0]["delta"] == 0.48


async def test_second_poll_records_interim(account, monkeypatch):
    _mock(monkeypatch, [_position()], delta=0.48)
    await poll_greeks_once(account["id"], "5WT1")
    _mock(monkeypatch, [_position()], delta=0.55)
    res = await poll_greeks_once(account["id"], "5WT1")
    assert res["interims"] == 1
    types = [r["snapshot_type"] for r in await fetch_all(
        "SELECT snapshot_type FROM greek_snapshots WHERE account_id = ? ORDER BY id", (account["id"],))]
    assert types == ["entry", "interim"]


async def test_disappearance_records_exit_with_carried_greeks(account, monkeypatch):
    _mock(monkeypatch, [_position()], delta=0.61)
    await poll_greeks_once(account["id"], "5WT1")          # entry
    _mock(monkeypatch, [], delta=0.0)                       # position gone
    res = await poll_greeks_once(account["id"], "5WT1")
    assert res["exits"] == 1
    last = (await fetch_all(
        "SELECT * FROM greek_snapshots WHERE account_id = ? AND snapshot_type = 'exit'",
        (account["id"],)))[0]
    assert last["delta"] == 0.61  # carried over from last known snapshot


async def test_empty_positions_no_error(account, monkeypatch):
    _mock(monkeypatch, [])
    res = await poll_greeks_once(account["id"], "5WT1")
    assert res == {"entries": 0, "interims": 0, "exits": 0}
