"""Phase A backend: rule-based insight badges + calendar R column."""
from app.services.journal_analytics import compute_calendar, insight_badges


def _t(**kw):
    base = {"status": "closed", "realized_pnl": 50.0, "edge_id": 1,
            "exit_reason": "target", "direction": "put_credit_spread",
            "entry_premium": 2.0, "exit_premium": 1.0,
            "expiration": "2026-08-15", "exit_at": "2026-07-01T15:00:00",
            "dte_at_entry": 45, "planned_risk": None, "no_edge": None}
    base.update(kw)
    return base


def _keys(t):
    return {b["key"] for b in insight_badges(t)}


def test_open_trade_has_no_badges():
    assert insight_badges(_t(realized_pnl=None)) == []


def test_unnamed_bet_and_declared_no_edge():
    assert "unnamed_bet" in _keys(_t(edge_id=None))
    assert "unnamed_bet" not in _keys(_t(edge_id=None, no_edge=1))


def test_off_plan_exit_tone_tracks_pnl():
    badges = insight_badges(_t(exit_reason="panic", realized_pnl=-20.0))
    off = next(b for b in badges if b["key"] == "off_plan_exit")
    assert off["tone"] == "bad"
    off_win = next(b for b in insight_badges(_t(exit_reason="panic", realized_pnl=20.0))
                   if b["key"] == "off_plan_exit")
    assert off_win["tone"] == "warn"


def test_good_loss_requires_adherence():
    assert "good_loss" in _keys(_t(realized_pnl=-30.0, exit_reason="stop"))
    assert "good_loss" not in _keys(_t(realized_pnl=-30.0, exit_reason="panic"))


def test_credit_kept_badges():
    # 50% kept -> pt_hit
    assert "pt_hit" in _keys(_t(entry_premium=2.0, exit_premium=1.0))
    # 25% kept on a target exit -> cut early
    assert "cut_early" in _keys(_t(entry_premium=2.0, exit_premium=1.5))
    # expired winner -> full credit
    assert "full_credit" in _keys(_t(exit_reason="expired", exit_premium=0.0))
    # debit shapes never get credit badges
    assert not {"pt_hit", "cut_early"} & _keys(_t(direction="long_call"))


def test_inside_21dte_badge():
    assert "inside_21dte" in _keys(_t(exit_at="2026-08-01T15:00:00"))   # 14 days out
    assert "inside_21dte" not in _keys(_t(exit_at="2026-07-01T15:00:00"))  # 45 days out
    assert "inside_21dte" not in _keys(
        _t(exit_at="2026-08-14T15:00:00", exit_reason="expired"))
    # a trade OPENED inside the clock was never subject to it
    assert "inside_21dte" not in _keys(
        _t(exit_at="2026-08-01T15:00:00", dte_at_entry=10))


def test_calendar_r_column():
    closed = [
        _t(exit_at="2026-07-01T15:00:00", realized_pnl=50.0, planned_risk=100.0),
        _t(exit_at="2026-07-01T16:00:00", realized_pnl=-25.0, planned_risk=100.0),
        _t(exit_at="2026-07-02T15:00:00", realized_pnl=10.0),  # no risk declared
    ]
    days = {d["date"]: d for d in compute_calendar(closed)}
    assert days["2026-07-01"]["r"] == 0.25
    assert days["2026-07-02"]["r"] is None
