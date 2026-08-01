from app.services.journal_analytics import compute_motd


def _t(pnl, system=True, conviction=2, exit_reason="target", mech=1):
    # Adherence = named edge (edge_id) + planned exit; is_system is the
    # sleeve label only and no longer affects adherence.
    return {"realized_pnl": pnl, "is_system": system, "conviction": conviction,
            "exit_reason": exit_reason, "edge_id": mech}


def test_motd_empty():
    m = compute_motd([], current_streak=4)
    assert m["trades_today"] == 0
    assert m["current_streak"] == 4
    assert "no trades" in m["headline"].lower()


def test_motd_all_on_plan():
    m = compute_motd([_t(10), _t(-5, exit_reason="stop")], current_streak=6)
    assert m["trades_today"] == 2
    assert m["system"] == 2
    assert m["discretionary"] == 0
    assert "ran its playbook" in m["headline"].lower()


def test_motd_won_but_broke_is_warned():
    m = compute_motd([_t(80, exit_reason="panic"), _t(10)], current_streak=0)
    assert m["won_but_off_plan"] == 1
    assert "off-plan" in m["headline"].lower() or "rule-break" in m["headline"].lower()


def test_motd_lost_but_executed_is_affirmed():
    m = compute_motd([_t(-9, system=True, exit_reason="stop")], current_streak=3)
    assert "well-executed" in m["headline"].lower() or "by the book" in m["headline"].lower()


def test_motd_avg_conviction_and_pnl():
    m = compute_motd([_t(10, conviction=1), _t(20, conviction=3)], current_streak=2)
    assert m["avg_conviction"] == 2.0
    assert m["realized_pnl"] == 30.0
    assert m["wins"] == 2
