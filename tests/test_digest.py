"""Morning digest: the attention derivation (pure) + the assembled endpoint."""
from datetime import date, timedelta

from app.database import get_db
from app.services.digest import attention_items

TODAY = date(2026, 7, 27)


def _pos(**over):
    base = {"id": 1, "underlying": "SPY", "direction": "put_credit_spread",
            "strikes": "700/695", "expiration": "2026-09-18", "moneyness": "OTM",
            "pct_of_credit": 10.0, "unrealized_pnl": 20.0, "day_pnl": 5.0}
    return {**base, **over}


def test_attention_quiet_book_is_empty():
    assert attention_items([_pos()], {}, TODAY) == []


def test_attention_reasons_and_order():
    threatened = _pos(id=2, moneyness="ATM", expiration="2026-09-18")
    expiring = _pos(id=3, expiration=(TODAY + timedelta(days=3)).isoformat())
    clock = _pos(id=4, expiration=(TODAY + timedelta(days=15)).isoformat())
    pt = _pos(id=5, pct_of_credit=62.0)
    items = attention_items([expiring, pt, clock, threatened], {}, TODAY)
    by_id = {i["trade_id"]: i["reasons"] for i in items}
    assert by_id[2] == ["threatened"]
    assert by_id[3] == ["expiring"]
    assert by_id[4] == ["clock"]
    assert by_id[5] == ["pt_hit"]
    assert items[0]["trade_id"] == 2  # threatened outranks sooner expiries


def test_attention_pt_only_for_short_premium():
    # A long call up 60% of its debit is not a 50%-PT candidate.
    long_call = _pos(direction="long_call", pct_of_credit=60.0)
    assert attention_items([long_call], {}, TODAY) == []


def test_attention_earnings_before_expiry():
    p = _pos(underlying="MSFT", expiration="2026-08-21")
    earn = {"MSFT": "2026-07-29"}
    items = attention_items([p], earn, TODAY)
    assert items and items[0]["reasons"] == ["earnings"]
    assert items[0]["earnings"] == "2026-07-29"
    # earnings after expiry: not a reason
    assert attention_items([_pos(underlying="MSFT", expiration="2026-08-21")],
                           {"MSFT": "2026-09-01"}, TODAY) == []


async def test_digest_endpoint_shape(auth_client, account):
    account = account["id"]
    async with get_db() as db:
        await db.execute(
            """INSERT INTO journal_trades
               (account_id, underlying, direction, status, entry_at, expiration,
                entry_premium)
               VALUES (?, 'QQQ', 'short_put', 'open', '2026-07-20T15:00:00',
                       ?, 250.0)""",
            (account, (date.today() + timedelta(days=10)).isoformat()),
        )
        await db.execute(
            """INSERT INTO research_snapshots
               (account_id, symbol, date, earnings, flags)
               VALUES (?, 'MSFT', ?, ?, '["earnings_soon"]')""",
            (account, date.today().isoformat(),
             (date.today() + timedelta(days=2)).isoformat()),
        )
        await db.commit()

    r = await auth_client.get("/api/journal/digest")
    assert r.status_code == 200
    d = r.json()
    for key in ("date", "market", "strategy1", "watchtower", "book",
                "attention", "calendar", "research_flags", "intents", "debt"):
        assert key in d
    # the open QQQ trade owes journal work and sits inside the 21-DTE clock
    assert d["debt"]["count"] >= 1
    assert any(i["underlying"] == "QQQ" and "clock" in i["reasons"]
               for i in d["attention"])
    assert d["research_flags"][0]["symbol"] == "MSFT"
    assert d["research_flags"][0]["flags"] == ["earnings_soon"]
    # expiration lands inside the 14-day calendar horizon
    assert any(v for v in d["calendar"]["expirations"].values())
