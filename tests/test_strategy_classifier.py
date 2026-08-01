"""classify_strategy: name the structure from opening legs, or admit custom."""
from app.services.calculations import classify_strategy


def leg(sym, otype, strike, action, qty=1, exp="2026-08-21"):
    return {"option_symbol": sym, "option_type": otype, "strike": strike,
            "action": action, "quantity": qty, "expiration": exp, "is_opening": 1}


def test_single_legs():
    assert classify_strategy([leg("A", "put", 100, "STO")]) == "short_put"
    assert classify_strategy([leg("A", "put", 100, "BTO")]) == "long_put"
    assert classify_strategy([leg("A", "call", 100, "STO")]) == "short_call"
    assert classify_strategy([leg("A", "call", 100, "BTO")]) == "long_call"


def test_verticals():
    assert classify_strategy([leg("A", "put", 100, "STO"),
                              leg("B", "put", 95, "BTO")]) == "put_credit_spread"
    assert classify_strategy([leg("A", "put", 100, "BTO"),
                              leg("B", "put", 95, "STO")]) == "put_debit_spread"
    assert classify_strategy([leg("A", "call", 105, "STO"),
                              leg("B", "call", 110, "BTO")]) == "call_credit_spread"
    assert classify_strategy([leg("A", "call", 105, "BTO"),
                              leg("B", "call", 110, "STO")]) == "call_debit_spread"


def test_calendar_and_diagonal():
    assert classify_strategy([leg("A", "put", 100, "STO", exp="2026-08-21"),
                              leg("B", "put", 100, "BTO", exp="2026-09-18")]) == "put_calendar"
    assert classify_strategy([leg("A", "call", 100, "STO", exp="2026-08-21"),
                              leg("B", "call", 105, "BTO", exp="2026-09-18")]) == "call_diagonal"


def test_straddles_strangles():
    assert classify_strategy([leg("A", "put", 100, "STO"),
                              leg("B", "call", 100, "STO")]) == "short_straddle"
    assert classify_strategy([leg("A", "put", 95, "STO"),
                              leg("B", "call", 105, "STO")]) == "short_strangle"
    assert classify_strategy([leg("A", "put", 95, "BTO"),
                              leg("B", "call", 105, "BTO")]) == "long_strangle"
    assert classify_strategy([leg("A", "put", 95, "STO"),
                              leg("B", "call", 105, "BTO")]) == "risk_reversal"


def test_jade_lizards():
    assert classify_strategy([leg("A", "put", 90, "STO"),
                              leg("B", "call", 105, "STO"),
                              leg("C", "call", 110, "BTO")]) == "jade_lizard"
    assert classify_strategy([leg("A", "call", 110, "STO"),
                              leg("B", "put", 100, "STO"),
                              leg("C", "put", 95, "BTO")]) == "reverse_jade_lizard"


def test_iron_condor_and_butterflies():
    assert classify_strategy([leg("A", "put", 90, "BTO"), leg("B", "put", 95, "STO"),
                              leg("C", "call", 105, "STO"),
                              leg("D", "call", 110, "BTO")]) == "iron_condor"
    assert classify_strategy([leg("A", "put", 95, "BTO"), leg("B", "put", 100, "STO"),
                              leg("C", "call", 100, "STO"),
                              leg("D", "call", 105, "BTO")]) == "iron_butterfly"
    assert classify_strategy([leg("A", "call", 95, "BTO"),
                              leg("B", "call", 100, "STO", qty=2),
                              leg("C", "call", 105, "BTO")]) == "long_call_butterfly"


def test_custom_fallbacks():
    # 1x2 ratio is NOT a vertical.
    assert classify_strategy([leg("A", "put", 100, "STO", qty=2),
                              leg("B", "put", 95, "BTO", qty=1)]) == "custom_2_leg"
    # Two shorts same type = no named shape.
    assert classify_strategy([leg("A", "put", 100, "STO"),
                              leg("B", "put", 95, "STO")]) == "custom_2_leg"
    # Strangle across two expirations isn't a strangle.
    assert classify_strategy([leg("A", "put", 95, "STO", exp="2026-08-21"),
                              leg("B", "call", 105, "STO", exp="2026-09-18")]) == "custom_2_leg"


def test_scale_in_nets_to_one_leg():
    # Two STO fills on the same symbol are one short leg, not two legs.
    assert classify_strategy([leg("A", "put", 100, "STO"),
                              leg("A", "put", 100, "STO")]) == "short_put"
