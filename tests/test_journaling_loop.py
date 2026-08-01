"""The journaling loop: debt, intents, auto-association, notify embeds."""
import json

import pytest

from app.database import get_db
from app.services import intents, notify
from app.services.journal_analytics import journal_debt


async def _trade(account_id, *, underlying="QQQ", direction="put_credit_spread",
                 status="open", edge_id=None, planned_risk=None,
                 exit_reason=None, pnl=None, entry_at="2026-07-24T14:00:00"):
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO journal_trades
               (account_id, underlying, direction, status, edge_id,
                planned_risk, exit_reason, realized_pnl, entry_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, underlying, direction, status, edge_id,
             planned_risk, exit_reason, pnl, entry_at),
        )
        await db.commit()
        return cur.lastrowid


# ---- journal debt ----

def test_journal_debt_bar():
    trades = [
        {"id": 1, "status": "open", "edge_id": 1, "planned_risk": 100.0},
        {"id": 2, "status": "open", "edge_id": None, "planned_risk": None},
        {"id": 3, "status": "closed", "realized_pnl": 5.0, "edge_id": 1,
         "planned_risk": 100.0, "exit_reason": None},
        {"id": 4, "status": "closed", "realized_pnl": 5.0, "edge_id": 1,
         "planned_risk": 100.0, "exit_reason": "target"},
    ]
    d = journal_debt(trades)
    assert d["count"] == 2
    by_id = {i["id"]: i for i in d["items"]}
    assert by_id[2]["missing"] == ["edge", "risk"]
    assert by_id[3]["missing"] == ["exit reason"]
    assert d["items"][0]["id"] == 2  # open before closed


async def test_debt_endpoint(auth_client, account):
    await _trade(account["id"])
    r = await auth_client.get("/api/journal/debt")
    assert r.status_code == 200
    assert r.json()["count"] == 1


# ---- intents ----

async def test_intent_binds_new_trade(auth_client, account):
    r = await auth_client.post("/api/journal/intents", json={
        "underlying": "qqq", "direction": "put_credit_spread",
        "planned_risk": 450.0, "conviction": 4, "note": "calm day plan",
    })
    assert r.status_code == 200
    intent_id = r.json()["id"]

    trade_id = await _trade(account["id"])
    bound = await intents.bind_new_trades(account["id"], [trade_id])
    assert bound == {trade_id: "intent"}

    r = await auth_client.get(f"/api/journal/trades/{trade_id}")
    t = r.json()
    assert t["planned_risk"] == 450.0
    assert t["conviction"] == 4
    assert t["why_entered"] == "calm day plan"

    r = await auth_client.get("/api/journal/intents")
    assert all(i["id"] != intent_id for i in r.json()["intents"])  # no longer open


async def test_intent_does_not_overwrite_and_respects_direction(auth_client, account):
    await auth_client.post("/api/journal/intents", json={
        "underlying": "QQQ", "direction": "short_put", "planned_risk": 999.0,
    })
    trade_id = await _trade(account["id"], planned_risk=100.0)  # pcs, not short_put
    bound = await intents.bind_new_trades(account["id"], [trade_id])
    assert bound == {}  # structure mismatch -> no bind

    r = await auth_client.get(f"/api/journal/trades/{trade_id}")
    assert r.json()["planned_risk"] == 100.0


async def test_intent_cancel(auth_client, account):
    r = await auth_client.post("/api/journal/intents", json={"underlying": "SPY"})
    iid = r.json()["id"]
    r = await auth_client.delete(f"/api/journal/intents/{iid}")
    assert r.status_code == 200
    r = await auth_client.delete(f"/api/journal/intents/{iid}")
    assert r.status_code == 404


# ---- Strategy #1 auto-association ----

async def test_signal_auto_association(auth_client, account, tmp_path, monkeypatch):
    sig = {"date": "2026-07-24", "signals": [
        {"ticker": "QQQ", "fires": True, "signal": "SELL", "tier": "STRONG"},
        {"ticker": "SPY", "fires": False, "signal": "FLAT"},
    ]}
    (tmp_path / "signal_2026-07-24.json").write_text(json.dumps(sig))
    monkeypatch.setattr(intents, "SIGNAL_DIR", str(tmp_path))

    qqq = await _trade(account["id"], underlying="QQQ")
    spy = await _trade(account["id"], underlying="SPY")
    bound = await intents.bind_new_trades(account["id"], [qqq, spy])
    assert bound == {qqq: "signal"}

    r = await auth_client.get(f"/api/journal/trades/{qqq}")
    t = r.json()
    assert t["is_system"] == 1
    assert t["edge_name"].lower().startswith("strategy #1")
    r = await auth_client.get(f"/api/journal/trades/{spy}")
    assert r.json()["edge_id"] is None


# ---- notify (offline: embeds built, send disabled by conftest env) ----

@pytest.mark.asyncio
async def test_notify_disabled_in_tests():
    assert notify.webhook_url() == ""
    assert await notify.send([{"title": "x"}]) is False


@pytest.mark.asyncio
async def test_notify_sync_builds_and_skips(monkeypatch):
    sent = {}

    async def fake_send(embeds):
        sent["embeds"] = embeds
        return True

    monkeypatch.setattr(notify, "send", fake_send)
    # nothing happened -> no card
    assert await notify.notify_sync([], [], 3) is False
    t = {"id": 1, "underlying": "QQQ", "direction": "put_credit_spread",
         "strikes": "550.0/555.0", "expiration": "2026-08-28", "entry_premium": 110.0}
    assert await notify.notify_sync([t], [], 1, {1: "signal"}) is True
    embed = sent["embeds"][0]
    assert "1 new trade" in embed["title"]
    assert "auto-tagged" in embed["fields"][0]["value"]
    assert "journal debt: 1" in embed["footer"]["text"]


# ---- assignment intent ----

async def test_assignment_intent_relaxes_risk_bar(auth_client, account):
    tid = await _trade(account["id"], direction="short_put")
    r = await auth_client.get("/api/journal/debt")
    assert "risk" in r.json()["items"][0]["missing"]

    r = await auth_client.put(f"/api/journal/trades/{tid}",
                              json={"assignment_intent": True})
    assert r.json()["assignment_intent"] == 1
    r = await auth_client.get("/api/journal/debt")
    assert "risk" not in r.json()["items"][0]["missing"]  # edge still owed


def test_assignment_capital():
    from app.services.calculations import assignment_capital
    fills = [
        {"option_symbol": "X P95", "option_type": "put", "strike": 95.0,
         "action": "STO", "quantity": 2},
        {"option_symbol": "X C120", "option_type": "call", "strike": 120.0,
         "action": "STO", "quantity": 1},
    ]
    assert assignment_capital(fills) == 19000.0  # 95 x 100 x 2; calls excluded
    assert assignment_capital([]) is None


async def test_declared_no_edge_clears_debt(auth_client, account):
    tid = await _trade(account["id"])
    r = await auth_client.put(f"/api/journal/trades/{tid}",
                              json={"no_edge": True, "planned_risk": 200.0})
    assert r.json()["no_edge"] == 1
    r = await auth_client.get("/api/journal/debt")
    assert r.json()["count"] == 0  # declared one-off + risk = complete record
