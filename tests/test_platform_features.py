"""Platform features: tags, R-multiples, calendar, score, filters, AI endpoints."""
from app.database import get_db
from app.services import ai_review


async def _seed_trade(account_id, *, pnl=100.0, underlying="QQQ", mech_id=None,
                      planned_risk=None, exit_day="2026-06-28", entry_day="2026-06-27",
                      is_system=1, exit_reason="target", status="closed"):
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO journal_trades
               (account_id, underlying, direction, status, realized_pnl, edge_id,
                is_system, exit_reason, planned_risk, entry_at, exit_at)
               VALUES (?, ?, 'short_put', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, underlying, status,
             pnl if status == "closed" else None, mech_id, is_system, exit_reason,
             planned_risk, f"{entry_day}T15:00:00", f"{exit_day}T18:00:00"),
        )
        await db.commit()
        return cur.lastrowid


async def _seed_edge(account_id, name="Strategy #1"):
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO edges (account_id, name) VALUES (?, ?)",
            (account_id, name),
        )
        await db.commit()
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

async def test_tag_crud_and_trade_tagging(auth_client, account):
    r = await auth_client.post("/api/journal/tags",
                               json={"name": "sell-into-calm", "color": "#199E70"})
    assert r.status_code == 200
    tag = r.json()
    assert tag["color"] == "#199e70"  # normalized to lowercase

    # duplicate name -> 409; bad color -> 400; default color applies
    r = await auth_client.post("/api/journal/tags",
                               json={"name": "sell-into-calm"})
    assert r.status_code == 409
    r = await auth_client.post("/api/journal/tags",
                               json={"name": "x", "color": "green"})
    assert r.status_code == 400
    r = await auth_client.post("/api/journal/tags", json={"name": "defaulted"})
    assert r.json()["color"] == "#3987e5"

    # rename/recolor follows through
    r = await auth_client.put(f"/api/journal/tags/{tag['id']}",
                              json={"color": "#d03b3b"})
    assert r.json()["color"] == "#d03b3b"
    r = await auth_client.put(f"/api/journal/tags/{tag['id']}",
                              json={"name": "defaulted"})
    assert r.status_code == 409  # collides with the other tag

    trade_id = await _seed_trade(account["id"])
    r = await auth_client.put(f"/api/journal/trades/{trade_id}/tags",
                              json={"tag_ids": [tag["id"]]})
    assert r.status_code == 200
    assert [t["name"] for t in r.json()["tags"]] == ["sell-into-calm"]

    # trade payloads carry tags with color
    r = await auth_client.get("/api/journal/trades")
    assert r.json()["trades"][0]["tags"][0]["name"] == "sell-into-calm"
    r = await auth_client.get(f"/api/journal/trades/{trade_id}")
    assert r.json()["tags"][0]["color"] == "#d03b3b"

    # filter by tag
    await _seed_trade(account["id"], underlying="SPY")
    r = await auth_client.get(f"/api/journal/trades?tag_id={tag['id']}")
    assert len(r.json()["trades"]) == 1

    # analytics by_tag
    r = await auth_client.get("/api/journal/analytics")
    by_tag = r.json()["by_tag"]
    assert by_tag[0]["tag"] == "sell-into-calm"
    assert by_tag[0]["total_pnl"] == 100.0

    # delete cascades away from the trade
    r = await auth_client.delete(f"/api/journal/tags/{tag['id']}")
    assert r.status_code == 200
    r = await auth_client.get(f"/api/journal/trades/{trade_id}")
    assert r.json()["tags"] == []


async def test_set_tags_rejects_foreign_tag(auth_client, account):
    trade_id = await _seed_trade(account["id"])
    r = await auth_client.put(f"/api/journal/trades/{trade_id}/tags",
                              json={"tag_ids": [999]})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# R-multiples + planned risk
# ---------------------------------------------------------------------------

async def test_r_multiples(auth_client, account):
    await _seed_trade(account["id"], pnl=150.0, planned_risk=300.0)   # +0.5R
    await _seed_trade(account["id"], pnl=-300.0, planned_risk=300.0)  # -1R
    await _seed_trade(account["id"], pnl=50.0)                        # no risk stated

    r = await auth_client.get("/api/journal/analytics")
    rm = r.json()["r_multiples"]
    assert rm["count"] == 2
    assert rm["coverage_pct"] == 66.7
    assert rm["avg_r"] == -0.25
    assert rm["best_r"] == 0.5
    assert rm["worst_r"] == -1.0

    # planned_risk is editable after grouping
    trade_id = await _seed_trade(account["id"], pnl=100.0)
    r = await auth_client.put(f"/api/journal/trades/{trade_id}",
                              json={"planned_risk": 200.0})
    assert r.status_code == 200
    assert r.json()["planned_risk"] == 200.0


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

async def test_calendar(auth_client, account):
    await _seed_trade(account["id"], pnl=100.0, exit_day="2026-06-26")
    await _seed_trade(account["id"], pnl=-40.0, exit_day="2026-06-26")
    await _seed_trade(account["id"], pnl=25.0, exit_day="2026-06-29")
    await auth_client.put("/api/journal/day-notes/2026-06-26",
                          json={"note": "chop day"})

    r = await auth_client.get("/api/journal/calendar")
    days = {d["date"]: d for d in r.json()["days"]}
    assert days["2026-06-26"]["pnl"] == 60.0
    assert days["2026-06-26"]["trades"] == 2
    assert days["2026-06-26"]["wins"] == 1
    assert days["2026-06-26"]["has_note"] is True
    assert days["2026-06-29"]["has_note"] is False


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------

async def test_score(auth_client, account):
    r = await auth_client.get("/api/journal/score")
    assert r.json()["score"] == 0.0

    await _seed_trade(account["id"], pnl=100.0, exit_day="2026-06-26")
    await _seed_trade(account["id"], pnl=80.0, exit_day="2026-06-29")
    await _seed_trade(account["id"], pnl=-60.0, exit_day="2026-06-30")

    r = await auth_client.get("/api/journal/score")
    data = r.json()
    c = data["components"]
    assert data["trades"] == 3
    assert 0 < data["score"] <= 100
    assert round(c["win_rate"], 0) == 67
    assert c["profit_factor"] == 100.0        # PF = 180/60 = 3.0 -> saturated
    assert data["max_drawdown"] == 60.0
    assert c["drawdown"] == round((1 - 60 / 180) * 100, 1)
    # all components clamped
    assert all(0 <= v <= 100 for v in c.values())


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

async def test_filters(auth_client, account):
    mech = await _seed_edge(account["id"])
    await _seed_trade(account["id"], pnl=100.0, mech_id=mech, underlying="SPY",
                      entry_day="2026-06-01", exit_day="2026-06-02")
    await _seed_trade(account["id"], pnl=-50.0, underlying="QQQ",
                      entry_day="2026-06-10", exit_day="2026-06-11")

    r = await auth_client.get(f"/api/journal/trades?edge_id={mech}")
    assert [t["underlying"] for t in r.json()["trades"]] == ["SPY"]

    r = await auth_client.get("/api/journal/trades?underlying=qqq")
    assert [t["underlying"] for t in r.json()["trades"]] == ["QQQ"]

    r = await auth_client.get("/api/journal/trades?date_from=2026-06-05")
    assert [t["underlying"] for t in r.json()["trades"]] == ["QQQ"]

    r = await auth_client.get(f"/api/journal/analytics?edge_id={mech}")
    assert r.json()["overall"]["total_pnl"] == 100.0

    r = await auth_client.get(f"/api/journal/score?edge_id={mech}")
    assert r.json()["trades"] == 1


# ---------------------------------------------------------------------------
# AI endpoints (claude mocked — no real LLM in tests)
# ---------------------------------------------------------------------------

async def test_ai_day_review_stores_feedback(auth_client, account, monkeypatch):
    async def fake_claude(prompt):
        assert "2026-06-26" in prompt
        return "**Process** — clean day."
    monkeypatch.setattr(ai_review, "_run_claude", fake_claude)

    await _seed_trade(account["id"], pnl=100.0, exit_day="2026-06-26")
    r = await auth_client.post("/api/journal/ai/day-review/2026-06-26")
    assert r.status_code == 200
    assert r.json()["ai_feedback"].startswith("**Process**")

    # persisted on the day note
    r = await auth_client.get("/api/journal/calendar")
    day = next(d for d in r.json()["days"] if d["date"] == "2026-06-26")
    assert day["has_review"] is True


async def test_ai_day_review_empty_day(auth_client, account, monkeypatch):
    monkeypatch.setattr(ai_review, "_claude_bin", lambda: "/bin/true")
    r = await auth_client.post("/api/journal/ai/day-review/2026-01-01")
    assert r.status_code == 503
    assert "nothing to review" in r.json()["detail"]


async def test_ai_review_reply_thread(auth_client, account, monkeypatch):
    async def fake_claude(prompt):
        if "Trader: the exit was planned" in prompt:
            assert "YOUR REVIEW OF THE DAY" in prompt
            assert "**Process** — clean day." in prompt
            return "Fair — with that context the exit reads on-plan."
        return "**Process** — clean day."
    monkeypatch.setattr(ai_review, "_run_claude", fake_claude)
    await _seed_trade(account["id"], pnl=100.0, exit_day="2026-06-26")

    # no review yet -> nothing to respond to
    r = await auth_client.post("/api/journal/ai/day-review/2026-06-26/reply",
                               json={"message": "hello?"})
    assert r.status_code == 404

    await auth_client.post("/api/journal/ai/day-review/2026-06-26")
    r = await auth_client.post("/api/journal/ai/day-review/2026-06-26/reply",
                               json={"message": "the exit was planned"})
    assert r.status_code == 200
    assert r.json()["reply"].startswith("Fair")

    # both turns persisted on the day, oldest first
    r = await auth_client.get("/api/journal/ai/day-review/2026-06-26/thread")
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "the exit was planned"

    # regenerating the review clears the conversation about the old one
    await auth_client.post("/api/journal/ai/day-review/2026-06-26")
    r = await auth_client.get("/api/journal/ai/day-review/2026-06-26/thread")
    assert r.json()["messages"] == []

    # explicit clear works too
    await auth_client.post("/api/journal/ai/day-review/2026-06-26/reply",
                           json={"message": "the exit was planned"})
    r = await auth_client.delete("/api/journal/ai/day-review/2026-06-26/thread")
    assert r.status_code == 200
    r = await auth_client.get("/api/journal/ai/day-review/2026-06-26/thread")
    assert r.json()["messages"] == []


async def test_ai_chat_legacy_stateless(auth_client, account, monkeypatch):
    async def fake_claude(prompt):
        assert "Trader: how am I doing?" in prompt
        assert "analytics" in prompt
        return "Steady."
    monkeypatch.setattr(ai_review, "_run_claude", fake_claude)

    await _seed_trade(account["id"], pnl=100.0)
    r = await auth_client.post("/api/journal/ai/chat",
                               json={"message": "how am I doing?",
                                     "history": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.json() == {"reply": "Steady.", "session_id": None}
    # stateless: nothing stored
    r = await auth_client.get("/api/journal/ai/chats")
    assert r.json()["sessions"] == []


async def test_ai_chat_sessions_persist_and_resume(auth_client, account, monkeypatch):
    calls = []

    async def fake_claude_json(prompt, resume=None):
        calls.append({"prompt": prompt, "resume": resume})
        return f"Reply {len(calls)}.", f"cli-sid-{len(calls)}"
    monkeypatch.setattr(ai_review, "_run_claude_json", fake_claude_json)

    await _seed_trade(account["id"], pnl=100.0)
    # turn 1: no session_id → session created, full journal context in prompt
    r = await auth_client.post("/api/journal/ai/chat",
                               json={"message": "how am I doing?"})
    assert r.status_code == 200
    body = r.json()
    sid = body["session_id"]
    assert body["reply"] == "Reply 1." and sid and body["resumed"] is False
    assert calls[0]["resume"] is None
    assert "analytics" in calls[0]["prompt"]

    # turn 2: resumes the CLI conversation — only the message travels
    r = await auth_client.post("/api/journal/ai/chat",
                               json={"message": "and my worst habit?", "session_id": sid})
    assert r.json()["reply"] == "Reply 2." and r.json()["resumed"] is True
    assert calls[1] == {"prompt": "and my worst habit?", "resume": "cli-sid-1"}

    # list + transcript
    r = await auth_client.get("/api/journal/ai/chats")
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["title"] == "how am I doing?"
    assert sessions[0]["n_messages"] == 4
    r = await auth_client.get(f"/api/journal/ai/chats/{sid}")
    roles = [m["role"] for m in r.json()["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]

    # delete
    r = await auth_client.delete(f"/api/journal/ai/chats/{sid}")
    assert r.status_code == 200
    assert (await auth_client.get("/api/journal/ai/chats")).json()["sessions"] == []
    assert (await auth_client.get(f"/api/journal/ai/chats/{sid}")).status_code == 404


async def test_ai_chat_rebuilds_when_cli_session_evicted(auth_client, account, monkeypatch):
    calls = []

    async def fake_claude_json(prompt, resume=None):
        calls.append({"prompt": prompt, "resume": resume})
        if resume is not None:
            raise RuntimeError("no such session")
        return "Rebuilt.", "cli-sid-new"
    monkeypatch.setattr(ai_review, "_run_claude_json", fake_claude_json)

    await _seed_trade(account["id"], pnl=100.0)
    r = await auth_client.post("/api/journal/ai/chat", json={"message": "hello"})
    sid = r.json()["session_id"]
    r = await auth_client.post("/api/journal/ai/chat",
                               json={"message": "still there?", "session_id": sid})
    body = r.json()
    # resume failed → transparent full-context rebuild, stored history included
    assert body["reply"] == "Rebuilt." and body["resumed"] is False
    assert calls[1]["resume"] == "cli-sid-new"  # the failed resume attempt
    rebuild = calls[2]
    assert rebuild["resume"] is None
    assert "Trader: hello" in rebuild["prompt"] and "analytics" in rebuild["prompt"]


async def test_ai_status(auth_client):
    r = await auth_client.get("/api/journal/ai/status")
    assert r.status_code == 200
    assert isinstance(r.json()["available"], bool)


async def test_regime_read_persists(auth_client, account):
    trade_id = await _seed_trade(account["id"])
    r = await auth_client.put(f"/api/journal/trades/{trade_id}",
                              json={"regime_read": "range"})
    assert r.status_code == 200
    assert r.json()["regime_read"] == "range"


async def test_sync_from_date_defaults_to_journal_epoch(auth_client, account):
    r = await auth_client.get("/api/journal/settings")
    assert r.json()["sync_from_date"] == "2026-07-14"


async def test_logbook_groups_closed_trades_by_exit_day(auth_client, account):
    # Opened the 20th, closed the 21st -> lives on the 21st's page, and the
    # 21st's stats carry its P&L (consistent with the calendar).
    await _seed_trade(account["id"], pnl=80.0,
                      entry_day="2026-07-20", exit_day="2026-07-21")
    # Still open, entered the 20th -> stays on the 20th.
    await _seed_trade(account["id"], entry_day="2026-07-20", exit_day="2026-07-20",
                      status="open")
    r = await auth_client.get("/api/journal/logbook")
    days = {d["date"]: d for d in r.json()["days"]}
    assert [t["realized_pnl"] for t in days["2026-07-21"]["trades"]] == [80.0]
    assert days["2026-07-21"]["stats"]["total_pnl"] == 80.0
    assert days["2026-07-20"]["trades"][0]["status"] == "open"
    assert list(days) == ["2026-07-21", "2026-07-20"]  # newest first


async def test_tags_color_migration(tmp_path):
    """Old category-taxonomy tags tables migrate to name+color: ids preserved,
    category mapped to its old display color, cross-category name dupes suffixed."""
    import aiosqlite
    from app.database import migrate_tags_colors

    async with aiosqlite.connect(tmp_path / "mig.db") as db:
        await db.execute("""
            CREATE TABLE tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, category, name)
            )
        """)
        await db.executemany(
            "INSERT INTO tags (id, account_id, category, name) VALUES (?, ?, ?, ?)",
            [
                (1, 1, "setup", "calm"),
                (2, 1, "mistake", "chased"),
                (3, 1, "emotion", "chased"),  # name dupe across categories
                (4, 2, "context", "chased"),  # other account: no suffix needed
            ],
        )
        await migrate_tags_colors(db)
        await db.commit()

        cur = await db.execute("SELECT id, account_id, name, color FROM tags ORDER BY id")
        rows = await cur.fetchall()
        assert rows == [
            (1, 1, "calm", "#3987e5"),
            (2, 1, "chased", "#d03b3b"),
            (3, 1, "chased-2", "#d55181"),
            (4, 2, "chased", "#199e70"),
        ]
        # idempotent: a second run is a no-op
        await migrate_tags_colors(db)


# ---------------------------------------------------------------------------
# Forward calendar
# ---------------------------------------------------------------------------

async def test_forward_calendar(auth_client, account, monkeypatch):
    from app.services import dd

    async def fake_earnings(symbols):
        assert "QQQ" in symbols
        return {s: ("2026-08-05" if s == "QQQ" else None) for s in symbols}

    monkeypatch.setattr(dd, "fetch_next_earnings", fake_earnings)

    async with get_db() as db:
        await db.execute(
            """INSERT INTO journal_trades
               (account_id, underlying, direction, status, strikes, expiration, entry_at)
               VALUES (?, 'QQQ', 'put_credit_spread', 'open', '550.0/555.0',
                       '2026-08-15', '2026-07-20T15:00:00')""",
            (account["id"],),
        )
        await db.commit()

    r = await auth_client.get("/api/journal/calendar/forward")
    assert r.status_code == 200
    data = r.json()
    exp = data["expirations"]["2026-08-15"]
    assert exp[0]["underlying"] == "QQQ"
    assert data["earnings"]["2026-08-05"][0] == {"symbol": "QQQ", "held": True}
    assert data["dte_window"]["start"] < data["dte_window"]["end"]
