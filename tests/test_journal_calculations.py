import pytest
from app.services.calculations import summarize_journal_fills


def _fill(**over):
    f = {
        "underlying": "QQQ", "option_type": "call", "action": "BTO",
        "is_opening": True, "strike": 480.0, "expiration": "2026-06-28",
        "quantity": 1, "price": 0.40, "fees": 0.64, "value": -40.0,
        "executed_at": "2026-06-28T18:32:00+00:00", "trade_date": "2026-06-28",
    }
    f.update(over)
    return f


def test_closed_long_call_roundtrip():
    fills = [
        _fill(),
        _fill(action="STC", is_opening=False, value=55.0, fees=0.64,
              executed_at="2026-06-28T18:39:00+00:00"),
    ]
    s = summarize_journal_fills(fills)
    assert s["direction"] == "long_call"
    assert s["status"] == "closed"
    assert s["entry_premium"] == 40.0
    assert s["exit_premium"] == 55.0
    assert s["fees_total"] == 1.28
    # (-40 + 55) - 1.28 = 13.72
    assert s["realized_pnl"] == 13.72
    assert s["realized_pnl_pct"] == pytest.approx(34.3, abs=0.1)
    assert s["time_in_trade_seconds"] == 420
    assert s["is_0dte"] is True
    assert s["dte_at_entry"] == 0


def test_open_only_is_open_with_no_pnl():
    s = summarize_journal_fills([_fill()])
    assert s["status"] == "open"
    assert s["realized_pnl"] is None
    assert s["exit_premium"] is None
    assert s["time_in_trade_seconds"] is None


def test_multi_leg_direction_and_strikes():
    fills = [
        _fill(action="STO", strike=478.0, value=30.0),
        _fill(action="BTO", strike=480.0, value=-20.0),
    ]
    s = summarize_journal_fills(fills)
    # Short 478 / long 480 calls, same expiry -> classified, not "multi-leg".
    assert s["direction"] == "call_credit_spread"
    assert s["strikes"] == "478.0/480.0"
    assert s["quantity"] == 2


def test_non_zero_dte():
    fills = [_fill(expiration="2026-07-03", trade_date="2026-06-28")]
    s = summarize_journal_fills(fills)
    assert s["dte_at_entry"] == 5
    assert s["is_0dte"] is False


def test_raises_without_opening_fill():
    with pytest.raises(ValueError):
        summarize_journal_fills([_fill(action="STC", is_opening=False, value=55.0)])


# ---- position stats (canonical open-book math) ----

from app.services.calculations import (  # noqa: E402
    days_open, defined_risk_max_loss, position_greeks, positions_summary,
)


def _leg(sym, action, strike, qty=1, **over):
    return _fill(option_symbol=sym, action=action, strike=strike,
                 quantity=qty, **over)


def test_position_greeks_short_put_signs():
    # Short put: net -1; delta -0.25 -> position delta +25; theta -0.05 -> +5/day.
    fills = [_leg("XYZ P95", "STO", 95.0, option_type="put", value=200.0)]
    g = position_greeks(fills, {"XYZ P95": {"delta": -0.25, "theta": -0.05}})
    assert g["position_delta"] == 25.0
    assert g["position_theta"] == 5.0


def test_position_greeks_missing_leg_is_none_not_zero():
    fills = [
        _leg("XYZ P95", "STO", 95.0, option_type="put"),
        _leg("XYZ P90", "BTO", 90.0, option_type="put"),
    ]
    g = position_greeks(fills, {"XYZ P95": {"delta": -0.25, "theta": -0.05},
                                "XYZ P90": {"delta": -0.10}})  # theta missing
    assert g["position_delta"] == 15.0  # (-1)(-0.25)(100) + (+1)(-0.10)(100)
    assert g["position_theta"] is None


def test_defined_risk_put_credit_spread():
    # $5-wide 1-lot credit spread, $110 credit -> max loss 500 - 110 = 390.
    fills = [
        _leg("XYZ P95", "STO", 95.0, option_type="put", value=150.0),
        _leg("XYZ P90", "BTO", 90.0, option_type="put", value=-40.0),
    ]
    assert defined_risk_max_loss(fills, "put_credit_spread", 110.0) == 390.0


def test_defined_risk_iron_condor_uses_wider_wing():
    fills = [
        _leg("XYZ P95", "STO", 95.0, option_type="put"),
        _leg("XYZ P90", "BTO", 90.0, option_type="put"),      # $5 put wing
        _leg("XYZ C110", "STO", 110.0, option_type="call"),
        _leg("XYZ C120", "BTO", 120.0, option_type="call"),   # $10 call wing
    ]
    # Worst case is the $10 wing: 1000 - 200 credit = 800.
    assert defined_risk_max_loss(fills, "iron_condor", 200.0) == 800.0


def test_defined_risk_none_for_naked_and_custom():
    fills = [_leg("XYZ P95", "STO", 95.0, option_type="put")]
    assert defined_risk_max_loss(fills, "short_put", 150.0) is None
    assert defined_risk_max_loss(fills, "custom_3_leg", 150.0) is None


def test_two_x_credit_short_premium_shapes():
    from app.services.calculations import two_x_credit_risk
    assert two_x_credit_risk("short_put", 150.0) == 300.0
    assert two_x_credit_risk("short_strangle", 212.5) == 425.0
    assert two_x_credit_risk("put_credit_spread", 110.0) == 220.0


def test_two_x_credit_none_for_debit_custom_and_zero():
    from app.services.calculations import two_x_credit_risk
    assert two_x_credit_risk("long_call", 150.0) is None
    assert two_x_credit_risk("custom_3_leg", 150.0) is None
    assert two_x_credit_risk("short_put", 0) is None


def test_days_open():
    from datetime import date, timedelta
    entry = (date.today() - timedelta(days=6)).isoformat() + "T14:30:00"
    assert days_open(entry) == 6
    assert days_open(None) is None


def test_positions_summary_partial_coverage():
    positions = [
        {"entry_premium": 100.0, "unrealized_pnl": 40.0, "liquidation_value": -60.0,
         "position_delta": 25.0, "position_theta": 5.0, "max_loss": 400.0},
        {"entry_premium": 200.0, "unrealized_pnl": None, "liquidation_value": None,
         "position_delta": None, "position_theta": None, "max_loss": None},
    ]
    s = positions_summary(positions)
    assert s["count"] == 2
    assert s["total_credit"] == 300.0
    assert s["total_unrealized"] == 40.0 and s["marked_count"] == 1
    assert s["net_delta"] == 25.0 and s["net_theta"] == 5.0
    assert s["greeks_covered"] == 1
    assert s["defined_risk_total"] == 400.0 and s["defined_risk_count"] == 1


# ---- covered-call coverage adjustment ----

from app.services.calculations import coverage_adjusted_direction  # noqa: E402


def test_short_call_with_shares_is_covered():
    fills = [_leg("AEO C15", "STO", 15.0, option_type="call", value=80.0)]
    assert coverage_adjusted_direction("short_call", fills, 100) == "covered_call"
    # 2 contracts need 200 shares.
    fills2 = [_leg("AEO C15", "STO", 15.0, qty=2, option_type="call")]
    assert coverage_adjusted_direction("short_call", fills2, 100) == "short_call"
    assert coverage_adjusted_direction("short_call", fills2, 200) == "covered_call"


def test_covered_call_reverts_when_shares_gone():
    fills = [_leg("AEO C15", "STO", 15.0, option_type="call")]
    assert coverage_adjusted_direction("covered_call", fills, 0) == "short_call"


def test_coverage_leaves_other_shapes_alone():
    fills = [
        _leg("XYZ P95", "STO", 95.0, option_type="put"),
        _leg("XYZ P90", "BTO", 90.0, option_type="put"),
    ]
    assert coverage_adjusted_direction("put_credit_spread", fills, 500) == "put_credit_spread"
    assert coverage_adjusted_direction("short_put", fills, 500) == "short_put"


# ---- short-premium profile (breakeven / cushion / moneyness) ----

from app.services.calculations import short_premium_profile, day_change_pnl  # noqa: E402


def _pcs_fills():
    # 555/550 put credit spread, 1-lot, $110 total credit -> $1.10/share.
    return [
        _leg("QQQ P555", "STO", 555.0, option_type="put", value=180.0),
        _leg("QQQ P550", "BTO", 550.0, option_type="put", value=-70.0),
    ]


def test_short_premium_profile_put_credit_spread():
    p = short_premium_profile(_pcs_fills(), "put_credit_spread", 110.0, spot=580.0)
    assert p["breakevens"] == [553.9]  # 555 - 1.10
    assert p["cushion_pct"] == pytest.approx(4.3, abs=0.05)  # (580-555)/580
    assert p["moneyness"] == "OTM"


def test_short_premium_profile_grades_threat():
    fills = _pcs_fills()
    # spot within 2% of the short strike -> ATM
    atm = short_premium_profile(fills, "put_credit_spread", 110.0, spot=560.0)
    assert atm["moneyness"] == "ATM"
    # breached -> ITM, cushion negative
    itm = short_premium_profile(fills, "put_credit_spread", 110.0, spot=540.0)
    assert itm["moneyness"] == "ITM"
    assert itm["cushion_pct"] < 0


def test_short_premium_profile_covered_call_and_delta_fallback():
    fills = [_leg("QQQ C560", "STO", 560.0, option_type="call", value=300.0)]
    p = short_premium_profile(fills, "covered_call", 300.0, spot=545.0)
    assert p["breakevens"] == [563.0]  # 560 + 3.00
    assert p["moneyness"] == "OTM"
    # no spot: grade from the short leg's |delta|
    d = short_premium_profile(fills, "covered_call", 300.0, spot=None,
                              greeks={"QQQ C560": {"delta": 0.41}})
    assert d["moneyness"] == "ATM"
    assert d["cushion_pct"] is None


def test_short_premium_profile_strangle_two_breakevens():
    fills = [
        _leg("SPY P540", "STO", 540.0, option_type="put", value=150.0),
        _leg("SPY C580", "STO", 580.0, option_type="call", value=100.0),
    ]
    p = short_premium_profile(fills, "short_strangle", 250.0, spot=544.0)
    assert p["breakevens"] == [537.5, 582.5]
    # nearest threat is the put side: (544-540)/544 = 0.74% -> ATM
    assert p["moneyness"] == "ATM"


def test_short_premium_profile_long_shapes_none():
    fills = [_leg("QQQ C560", "BTO", 560.0, option_type="call", value=-300.0)]
    p = short_premium_profile(fills, "long_call", 300.0, spot=545.0)
    assert p == {"breakevens": None, "cushion_pct": None, "moneyness": None}


# ---- day change P&L ----

def test_day_change_pnl_short_spread():
    fills = _pcs_fills()
    # short leg cheapened 1.80 -> 1.50, long leg decayed 0.70 -> 0.60:
    # (-1)(100)(1.50-1.80) + (+1)(100)(0.60-0.70) = +30 - 10 = +20
    v = day_change_pnl(fills,
                       {"QQQ P555": 1.50, "QQQ P550": 0.60},
                       {"QQQ P555": 1.80, "QQQ P550": 0.70},
                       "2026-07-26")
    assert v == 20.0


def test_day_change_pnl_opened_today_uses_fill_price():
    fills = [_leg("QQQ P555", "STO", 555.0, option_type="put",
                  price=1.80, trade_date="2026-07-26")]
    # no prior mark; entered short at 1.80, now 1.60 -> +20
    v = day_change_pnl(fills, {"QQQ P555": 1.60}, {}, "2026-07-26")
    assert v == 20.0


def test_day_change_pnl_missing_reference_is_none():
    fills = [_leg("QQQ P555", "STO", 555.0, option_type="put",
                  trade_date="2026-07-20")]
    assert day_change_pnl(fills, {"QQQ P555": 1.60}, {}, "2026-07-26") is None
    assert day_change_pnl(fills, {}, {"QQQ P555": 1.80}, "2026-07-26") is None
