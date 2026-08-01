from app.services.journal_analytics import compute_journal_analytics


def _t(pnl, *, mech="Validated Long", system=True, underlying="QQQ",
       regime="range", exit_reason="target", exit_at="2026-06-28T18:00:00", tid=1):
    return {
        "realized_pnl": pnl, "edge_name": mech, "edge_id": 1,
        "is_system": system, "underlying": underlying, "regime_read": regime,
        "exit_reason": exit_reason, "exit_at": exit_at, "id": tid,
    }


def test_per_edge_and_overall():
    trades = [_t(100, mech="A", tid=1), _t(-50, mech="A", tid=2), _t(200, mech="B", tid=3)]
    out = compute_journal_analytics(trades)
    by = {m["edge_name"]: m for m in out["by_edge"]}
    assert by["A"]["count"] == 2
    assert by["A"]["total_pnl"] == 50
    assert by["A"]["win_rate"] == 50.0
    assert by["A"]["avg_win"] == 100
    assert by["A"]["avg_loss"] == -50
    assert by["A"]["payoff_ratio"] == 2.0
    assert out["overall"]["total_pnl"] == 250


def test_system_vs_discretionary_split():
    trades = [_t(100, system=True, tid=1), _t(-30, system=False, tid=2)]
    out = compute_journal_analytics(trades)
    assert out["system_vs_discretionary"]["system"]["total_pnl"] == 100
    assert out["system_vs_discretionary"]["discretionary"]["total_pnl"] == -30


def test_by_instrument_and_regime():
    trades = [_t(100, underlying="QQQ", regime="range", tid=1),
              _t(50, underlying="SPY", regime="trend", tid=2)]
    out = compute_journal_analytics(trades)
    assert {i["key"] for i in out["by_instrument"]} == {"QQQ", "SPY"}
    assert {r["key"] for r in out["by_regime"]} == {"range", "trend"}


def test_discipline_streak_and_dissonance():
    # Adherence is sleeve-agnostic: a discretionary trade with an edge + planned
    # exit is on-plan; a panic exit is off-plan whatever the sleeve.
    trades = [
        _t(10, system=True, exit_reason="target", exit_at="2026-06-28T10:00:00", tid=1),
        _t(-5, system=False, exit_reason="stop", exit_at="2026-06-28T11:00:00", tid=2),   # discretionary, on-plan
        _t(80, system=True, exit_reason="panic", exit_at="2026-06-28T12:00:00", tid=3),   # won but off-plan
        _t(-9, system=True, exit_reason="stop", exit_at="2026-06-28T13:00:00", tid=4),    # lost but executed
        _t(7, system=False, exit_reason="target", exit_at="2026-06-28T14:00:00", tid=5),
    ]
    out = compute_journal_analytics(trades)
    d = out["discipline"]
    assert d["current_streak"] == 2          # last two adherent
    assert d["longest_streak"] == 2
    assert d["dissonance"]["won_but_off_plan"] == 1
    assert d["dissonance"]["lost_but_well_executed"] == 2  # t2 and t4, edge + stop exit


def test_empty_is_safe():
    out = compute_journal_analytics([])
    assert out["overall"]["count"] == 0
    assert out["discipline"]["current_streak"] == 0
