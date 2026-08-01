"""Per-position consult: POST /journal/positions/{id}/consult pins the clicked
trade as subject_position so the agent evaluates THAT position (no intent gate).
The AI call and the network-touching DD/chain fetches are mocked."""
import pytest

from app.database import fetch_all, get_db
from app.services import ai_review


async def _seed_open_trade(account_id, underlying="MSFT", status="open"):
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO journal_trades
               (account_id, underlying, direction, strikes, expiration,
                entry_premium, quantity, status, entry_at)
               VALUES (?, ?, 'short_put', '500', '2026-08-21', 3.5, 1, ?,
                       '2026-07-20 14:00:00')""",
            (account_id, underlying, status),
        )
        await db.commit()
        return cur.lastrowid


@pytest.fixture
def consult_mocks(monkeypatch):
    """Silence the network-dependent inputs and capture the final prompt."""
    prompts = []

    async def fake_dd(symbol, **kw):
        return {"symbol": symbol, "series": [], "rsi": 41.0}

    async def fake_puts(symbol, **kw):
        return []

    async def fake_claude(prompt, tools=None):
        prompts.append(prompt)
        return "**Verdict** — hold toward the 50% target."

    import app.services.dd as dd
    monkeypatch.setattr(dd, "fetch_dd", fake_dd)
    monkeypatch.setattr(dd, "fetch_candidate_puts", fake_puts)
    monkeypatch.setattr(ai_review, "_run_claude", fake_claude)
    monkeypatch.setattr(ai_review, "ai_available", lambda: True)
    return prompts


@pytest.mark.asyncio
async def test_position_consult_pins_subject(auth_client, account, consult_mocks):
    trade_id = await _seed_open_trade(account["id"])
    r = await auth_client.post(
        f"/api/journal/positions/{trade_id}/consult",
        json={"message": "how is this doing?", "history": []},
    )
    assert r.status_code == 200
    assert r.json()["trade_id"] == trade_id
    # the clicked trade must appear as subject_position in the prompt context
    assert '"subject_position"' in consult_mocks[0]
    ctx = consult_mocks[0]
    assert f'"id": {trade_id}' in ctx
    # a Verdict reply joins the research trail under the underlying
    briefs = await fetch_all("SELECT symbol, kind FROM research_briefs")
    assert [dict(b) for b in briefs] == [{"symbol": "MSFT", "kind": "consult"}]


@pytest.mark.asyncio
async def test_position_consult_404_on_missing_or_closed(auth_client, account, consult_mocks):
    r = await auth_client.post(
        "/api/journal/positions/9999/consult", json={"message": "x", "history": []}
    )
    assert r.status_code == 404
    closed_id = await _seed_open_trade(account["id"], status="closed")
    r = await auth_client.post(
        f"/api/journal/positions/{closed_id}/consult", json={"message": "x", "history": []}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_position_consult_thread_persists(auth_client, account, consult_mocks):
    trade_id = await _seed_open_trade(account["id"])
    await auth_client.post(
        f"/api/journal/positions/{trade_id}/consult", json={"message": "first question"}
    )
    r = await auth_client.post(
        f"/api/journal/positions/{trade_id}/consult", json={"message": "second question"}
    )
    assert r.status_code == 200
    # the second turn's prompt carries the STORED first exchange — the server,
    # not the client, is the source of the conversation
    assert "Trader: first question" in consult_mocks[1]
    assert "Consultant: **Verdict**" in consult_mocks[1]
    # GET returns the whole thread in order
    r = await auth_client.get(f"/api/journal/positions/{trade_id}/consult")
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == "first question"
    # the positions list flags the conversation
    r = await auth_client.get("/api/journal/positions")
    pos = next(p for p in r.json()["positions"] if p["id"] == trade_id)
    assert pos["consult_turns"] == 4
    # DELETE starts fresh
    r = await auth_client.delete(f"/api/journal/positions/{trade_id}/consult")
    assert r.status_code == 200
    r = await auth_client.get(f"/api/journal/positions/{trade_id}/consult")
    assert r.json()["messages"] == []


@pytest.mark.asyncio
async def test_position_and_research_threads_are_separate(auth_client, account, consult_mocks):
    trade_id = await _seed_open_trade(account["id"])
    await auth_client.post(
        f"/api/journal/positions/{trade_id}/consult", json={"message": "about the trade"}
    )
    # the name-level research thread is untouched by the position thread
    r = await auth_client.get("/api/journal/research/MSFT/consult")
    assert r.json()["messages"] == []
    await auth_client.post(
        "/api/journal/research/MSFT/consult", json={"message": "about the name"}
    )
    r = await auth_client.get("/api/journal/research/MSFT/consult")
    assert [m["content"] for m in r.json()["messages"] if m["role"] == "user"] == ["about the name"]
    r = await auth_client.get(f"/api/journal/positions/{trade_id}/consult")
    assert [m["content"] for m in r.json()["messages"] if m["role"] == "user"] == ["about the trade"]


@pytest.mark.asyncio
async def test_research_consult_has_no_subject(auth_client, account, consult_mocks):
    """The plain research consult keeps its old shape — no subject_position key."""
    await _seed_open_trade(account["id"])
    r = await auth_client.post(
        "/api/journal/research/MSFT/consult", json={"message": "read?", "history": []}
    )
    assert r.status_code == 200
    assert '"subject_position"' not in consult_mocks[0]
    # the open trade still shows up as part of the live book on the name
    assert '"open_positions_this_name"' in consult_mocks[0]
