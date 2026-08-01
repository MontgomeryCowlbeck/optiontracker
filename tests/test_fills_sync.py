import pytest
from app.services import fills_sync
from app.services.fills_sync import parse_fill, store_fills, sync_fills
from app.database import fetch_all


def _opt_txn(**over):
    txn = {
        "id": 1001,
        "order-id": 55,
        "transaction-type": "Trade",
        "instrument-type": "Equity Option",
        "action": "Buy to Open",
        "symbol": "QQQ   260628C00480000",
        "underlying-symbol": "QQQ",
        "quantity": "1",
        "price": "0.40",
        "commission": "0.50",
        "clearing-fees": "0.10",
        "regulatory-fees": "0.04",
        "value": "40.0",
        "value-effect": "Debit",
        "executed-at": "2026-06-28T18:32:00.000+00:00",
        "transaction-date": "2026-06-28",
    }
    txn.update(over)
    return txn


def test_parse_fill_opening_option():
    p = parse_fill(_opt_txn())
    assert p["external_id"] == "1001"
    assert p["underlying"] == "QQQ"
    assert p["option_type"] == "call"
    assert p["strike"] == 480.0
    assert p["expiration"] == "2026-06-28"
    assert p["action"] == "BTO"
    assert p["is_opening"] is True
    assert p["quantity"] == 1
    assert p["price"] == 0.40
    assert round(p["fees"], 2) == 0.64
    # Debit -> negative signed value
    assert p["value"] == -40.0


def test_parse_fill_closing_credit_sign():
    # Note: 'value-effect' has a hyphen, so it can't be a kwarg — pass via **{}.
    p = parse_fill(_opt_txn(**{"id": 1002, "action": "Sell to Close",
                               "value": "55.0", "value-effect": "Credit"}))
    assert p["action"] == "STC"
    assert p["is_opening"] is False
    assert p["value"] == 55.0


def test_parse_fill_skips_money_movement():
    assert parse_fill({"id": 9, "transaction-type": "Money Movement",
                       "transaction-sub-type": "Deposit"}) is None


def test_parse_fill_skips_equity():
    assert parse_fill({"id": 10, "transaction-type": "Trade",
                       "instrument-type": "Equity", "action": "Buy to Open"}) is None


async def test_store_fills_inserts_and_dedups(account):
    p1 = parse_fill(_opt_txn(id=2001))
    res1 = await store_fills(account["id"], [p1])
    assert res1 == {"synced": 1, "skipped": 0}
    # Re-storing the same external_id is skipped
    res2 = await store_fills(account["id"], [p1])
    assert res2 == {"synced": 0, "skipped": 1}
    rows = await fetch_all("SELECT external_id, value FROM tt_fills WHERE account_id = ?",
                           (account["id"],))
    assert len(rows) == 1
    assert rows[0]["external_id"] == "2001"


async def test_sync_fills_pulls_and_stores(account, monkeypatch):
    txns = [
        _opt_txn(id=3001),
        _opt_txn(**{"id": 3002, "action": "Sell to Close",
                    "value": "60.0", "value-effect": "Credit"}),
        {"id": 3003, "transaction-type": "Money Movement",
         "transaction-sub-type": "Deposit"},
    ]

    async def fake_get_all(account_number, start_date=None):
        return txns

    monkeypatch.setattr(fills_sync.tastytrade_client, "get_all_transactions", fake_get_all)
    res = await sync_fills(account["id"], "5WT00001")
    assert res["synced"] == 2  # money movement skipped
    rows = await fetch_all("SELECT external_id FROM tt_fills WHERE account_id = ?",
                           (account["id"],))
    assert {r["external_id"] for r in rows} == {"3001", "3002"}
