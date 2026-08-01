"""
Consolidated calculation functions for trade P&L, premium, and analytics.

This module centralizes all financial calculations to eliminate code duplication
and ensure consistent behavior across the application.
"""
from datetime import date, datetime
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class TradeMetrics:
    """Computed metrics for a single trade."""
    premium: float = 0.0
    unrealized_pnl: Optional[float] = None
    realized_pnl: Optional[float] = None
    profit_pct: Optional[float] = None
    target_hit: Optional[bool] = None
    capital_deployed: Optional[float] = None
    dte: int = 0
    moneyness: str = "Unknown"


@dataclass
class AggregateStats:
    """Aggregated statistics for analytics."""
    count: int = 0
    premium: float = 0.0           # Net premium (collected - paid)
    collected_premium: float = 0.0  # Total from sells only (for conversion rate)
    pnl: float = 0.0
    wins: int = 0
    losses: int = 0

    def add_trade(self, net_premium: float, raw_premium: float, action: str, pnl: Optional[float] = None):
        """
        Add a trade to the aggregate stats.

        Args:
            net_premium: Net premium (positive for sells, negative for buys)
            raw_premium: Raw premium amount (always positive)
            action: Trade action ('sell' or 'buy')
            pnl: Realized P&L if trade is closed
        """
        self.count += 1
        self.premium += net_premium
        if action == "sell":
            self.collected_premium += raw_premium  # Track collected separately
        if pnl is not None:
            self.pnl += pnl
            if pnl > 0:
                self.wins += 1
            elif pnl < 0:
                self.losses += 1
            # Breakeven (pnl == 0) doesn't count as win or loss

    def to_dict(self) -> dict:
        # Conversion rate: what percentage of collected premium was kept as profit
        conversion_rate = 0.0
        if self.collected_premium > 0:
            conversion_rate = (self.pnl / self.collected_premium) * 100

        return {
            "count": self.count,
            "premium": round(self.premium, 2),
            "collected_premium": round(self.collected_premium, 2),
            "pnl": round(self.pnl, 2),
            "wins": self.wins,
            "losses": self.losses,
            "conversion_rate": round(conversion_rate, 1)
        }


def _parse_journal_dt(value) -> Optional[datetime]:
    """Parse an ISO timestamp (handles trailing 'Z'). None-safe."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def classify_strategy(opening_fills: list[dict]) -> str:
    """Name the option structure from a trade's OPENING legs — the canonical
    strategy classifier (journal `direction` field).

    Legs are the net signed position per symbol at open (buys +, sells -).
    Named shapes require equal quantity on every leg (a 1x2 is not a vertical);
    anything unrecognized falls back to 'custom_N_leg' rather than a wrong name.
    Covered calls can't be detected here — stock legs aren't ingested."""
    net: dict = {}
    for f in opening_fills:
        sign = 1 if f["action"] in ("BTO", "BTC") else -1
        key = f.get("option_symbol") or (f["option_type"], f.get("strike"),
                                         str(f.get("expiration")))
        leg = net.setdefault(key, {
            "type": f["option_type"], "strike": f.get("strike"),
            "exp": str(f.get("expiration")), "qty": 0})
        leg["qty"] += sign * f["quantity"]
    legs = [l for l in net.values() if l["qty"] != 0]
    n = len(legs)

    if n == 0:
        return "flat"
    if n == 1:
        l = legs[0]
        return f"{'long' if l['qty'] > 0 else 'short'}_{l['type']}"

    equal_qty = len({abs(l["qty"]) for l in legs}) == 1
    one_exp = len({l["exp"] for l in legs}) == 1
    puts = sorted((l for l in legs if l["type"] == "put"), key=lambda l: l["strike"])
    calls = sorted((l for l in legs if l["type"] == "call"), key=lambda l: l["strike"])
    shorts = [l for l in legs if l["qty"] < 0]
    longs = [l for l in legs if l["qty"] > 0]
    fallback = f"custom_{n}_leg"
    if not equal_qty:
        # 1-2-1 butterfly is the one named unequal-quantity shape.
        if n == 3 and one_exp and (len(puts) == 3 or len(calls) == 3):
            group = puts or calls
            q = [l["qty"] for l in group]  # strike-ascending
            if (q[0] == q[2] and q[1] == -2 * q[0]):
                side = "long" if q[0] > 0 else "short"
                return f"{side}_{group[0]['type']}_butterfly"
        return fallback

    def _vertical(pair, kind):
        lo, hi = pair  # strike-ascending
        if {1 if lo["qty"] > 0 else -1, 1 if hi["qty"] > 0 else -1} != {1, -1}:
            return None
        short = lo if lo["qty"] < 0 else hi
        if kind == "put":   # credit: short the higher strike
            return "put_credit_spread" if short is hi else "put_debit_spread"
        return "call_credit_spread" if short is lo else "call_debit_spread"

    if n == 2:
        a, b = legs
        if a["type"] == b["type"]:
            if a["exp"] == b["exp"] and a["strike"] != b["strike"]:
                v = _vertical(puts or calls, a["type"])
                if v:
                    return v
            if a["exp"] != b["exp"] and {1 if a["qty"] > 0 else -1,
                                         1 if b["qty"] > 0 else -1} == {1, -1}:
                return (f"{a['type']}_calendar" if a["strike"] == b["strike"]
                        else f"{a['type']}_diagonal")
            return fallback
        # one put + one call
        if not one_exp:
            return fallback
        same_strike = puts[0]["strike"] == calls[0]["strike"]
        if len(shorts) == 2:
            return "short_straddle" if same_strike else "short_strangle"
        if len(longs) == 2:
            return "long_straddle" if same_strike else "long_strangle"
        return "risk_reversal"

    if n == 3 and one_exp:
        # Jade lizard: short put + call credit spread (no upside risk when the
        # combined credit exceeds the call-spread width — not checked here).
        if len(puts) == 1 and len(calls) == 2 and puts[0]["qty"] < 0 \
                and _vertical(calls, "call") == "call_credit_spread":
            return "jade_lizard"
        if len(calls) == 1 and len(puts) == 2 and calls[0]["qty"] < 0 \
                and _vertical(puts, "put") == "put_credit_spread":
            return "reverse_jade_lizard"
        return fallback

    if n == 4 and one_exp and len(puts) == 2 and len(calls) == 2:
        if _vertical(puts, "put") == "put_credit_spread" \
                and _vertical(calls, "call") == "call_credit_spread":
            short_put = next(l for l in puts if l["qty"] < 0)
            short_call = next(l for l in calls if l["qty"] < 0)
            return ("iron_butterfly" if short_put["strike"] == short_call["strike"]
                    else "iron_condor")
        return fallback

    return fallback


def summarize_journal_fills(fills: list[dict]) -> dict:
    """Derive a journal trade's auto fields from its grouped fills.

    Each fill dict must have: underlying, option_type, action (BTO/STO/BTC/STC),
    is_opening, strike, expiration, quantity, price, fees, value (TT's signed
    cash flow — credit positive, debit negative), executed_at, trade_date.

    Realized P&L is the canonical journal formula: sum(value) - sum(fees) across
    all fills, only when the trade is closed (opening contracts fully matched by
    closing contracts). Raises ValueError if there is no opening fill.
    """
    opening = [f for f in fills if f["is_opening"]]
    closing = [f for f in fills if not f["is_opening"]]
    if not opening:
        raise ValueError("a journal trade needs at least one opening fill")

    underlying = opening[0]["underlying"]
    expiration = min(f["expiration"] for f in fills if f.get("expiration"))

    direction = classify_strategy(opening)

    strikes = "/".join(
        str(s) for s in sorted({f["strike"] for f in opening if f.get("strike") is not None})
    )

    open_qty = sum(f["quantity"] for f in opening)
    close_qty = sum(f["quantity"] for f in closing)
    fees_total = round(sum(f.get("fees") or 0 for f in fills), 4)

    entry_dt = min(_parse_journal_dt(f["executed_at"]) for f in opening)
    exit_dt = max((_parse_journal_dt(f["executed_at"]) for f in closing), default=None) if closing else None
    entry_at = entry_dt.isoformat() if entry_dt else None
    exit_at = exit_dt.isoformat() if exit_dt else None
    time_in_trade = int((exit_dt - entry_dt).total_seconds()) if (entry_dt and exit_dt) else None

    entry_premium = round(abs(sum(f.get("value") or 0 for f in opening)), 2)
    exit_premium = round(abs(sum(f.get("value") or 0 for f in closing)), 2) if closing else None

    is_closed = bool(closing) and open_qty == close_qty
    status = "closed" if is_closed else "open"
    realized_pnl = round(sum(f.get("value") or 0 for f in fills) - fees_total, 2) if is_closed else None
    realized_pnl_pct = (round(realized_pnl / entry_premium * 100, 2)
                        if (realized_pnl is not None and entry_premium) else None)

    try:
        dte = (date.fromisoformat(str(expiration))
               - date.fromisoformat(str(opening[0]["trade_date"]))).days
    except (ValueError, TypeError):
        dte = None

    return {
        "underlying": underlying,
        "direction": direction,
        "strikes": strikes,
        "expiration": expiration,
        "dte_at_entry": dte,
        "is_0dte": (dte == 0),
        "entry_premium": entry_premium,
        "exit_premium": exit_premium,
        "quantity": open_qty,
        "fees_total": fees_total,
        "entry_at": entry_at,
        "exit_at": exit_at,
        "time_in_trade_seconds": time_in_trade,
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": realized_pnl_pct,
        "status": status,
    }


def net_contracts_by_symbol(fills: list[dict]) -> dict[str, int]:
    """Signed net position per option symbol from a trade's fills.
    Buys add, sells subtract (BTO/BTC +, STO/STC -): net > 0 long, < 0 short,
    0 flat. This is the one definition of "leg still open"."""
    net: dict[str, int] = {}
    for f in fills:
        sign = 1 if f["action"] in ("BTO", "BTC") else -1
        sym = f["option_symbol"]
        net[sym] = net.get(sym, 0) + sign * f["quantity"]
    return {s: q for s, q in net.items()}


def unrealized_journal_pnl(fills: list[dict], marks: dict[str, float]) -> dict:
    """Mark-to-market P&L for an OPEN journal trade.

    unrealized = cash so far (sum of signed fill values - fees) + what the open
    legs would liquidate for at the given marks (net * mark * 100; long legs are
    an asset, short legs a liability). Marks missing for any open leg -> pnl is
    None and the missing symbols are reported rather than silently priced at 0."""
    cash = round(sum(f.get("value") or 0 for f in fills)
                 - sum(f.get("fees") or 0 for f in fills), 2)
    open_legs = {s: q for s, q in net_contracts_by_symbol(fills).items() if q != 0}
    missing = sorted(s for s in open_legs if marks.get(s) is None)
    liquidation = None
    unrealized = None
    if not missing:
        liquidation = round(sum(q * marks[s] * 100 for s, q in open_legs.items()), 2)
        unrealized = round(cash + liquidation, 2)
    return {
        "cash_flow": cash,
        "liquidation_value": liquidation,
        "unrealized_pnl": unrealized,
        "marks_missing": missing,
    }


def coverage_adjusted_direction(direction: str, fills: list[dict],
                                shares: float) -> str:
    """A short call written against >=100 shares per contract is a covered
    call, not a naked short call. The journal ingests only option fills, so
    share coverage arrives separately (live TT equity positions at sync time).
    Symmetric on purpose: if the shares are gone, a covered_call reverts to
    short_call. Only the naked/covered short-call pair is affected."""
    if direction not in ("short_call", "covered_call"):
        return direction
    # Net short call contracts from the opening legs (sells +, buys -).
    contracts = sum(
        (f["quantity"] if f["action"] in ("STO", "STC") else -f["quantity"])
        for f in fills if f["is_opening"] and f["option_type"] == "call")
    if contracts <= 0:
        return direction
    return "covered_call" if shares >= contracts * 100 else "short_call"


def position_greeks(fills: list[dict], greeks: dict[str, dict]) -> dict:
    """Net delta/theta of a trade's OPEN legs in position terms (× contracts × 100).

    greeks maps option_symbol -> {'delta': ..., 'theta': ...} (latest snapshot).
    Sign convention follows net_contracts_by_symbol (short legs negative), so a
    short put shows positive position delta and positive daily theta. A greek is
    None when any open leg lacks it — missing is never silently priced as 0."""
    open_legs = {s: q for s, q in net_contracts_by_symbol(fills).items() if q != 0}
    out = {"position_delta": None, "position_theta": None}
    for key, field in (("position_delta", "delta"), ("position_theta", "theta")):
        vals = [(q, (greeks.get(s) or {}).get(field)) for s, q in open_legs.items()]
        if vals and all(v is not None for _, v in vals):
            out[key] = round(sum(q * float(v) * 100 for q, v in vals), 2)
    return out


# Shapes whose worst case is a strike width, not the strike itself.
_DEFINED_RISK_SHAPES = {"put_credit_spread", "call_credit_spread",
                        "iron_condor", "iron_butterfly"}


def defined_risk_max_loss(fills: list[dict], direction: str,
                          entry_premium: float) -> Optional[float]:
    """Max loss for defined-risk short-premium shapes, from the OPENING legs:
    (widest side's strike width × contracts × 100) − credit received. Only one
    side of a condor can finish ITM, so the wider wing is the worst case.
    None for shapes without a defined width (naked legs, custom structures)."""
    if direction not in _DEFINED_RISK_SHAPES or not entry_premium:
        return None
    net: dict = {}
    for f in fills:
        if not f["is_opening"] or f.get("strike") is None:
            continue
        sign = 1 if f["action"] in ("BTO", "BTC") else -1
        key = (f["option_type"], f["strike"])
        net[key] = net.get(key, 0) + sign * f["quantity"]
    legs = {k: q for k, q in net.items() if q != 0}
    if not legs:
        return None
    contracts = min(abs(q) for q in legs.values())

    def _width(kind: str) -> float:
        ks = sorted(s for (t, s), _q in legs.items() if t == kind)
        return float(ks[-1] - ks[0]) if len(ks) >= 2 else 0.0

    width = max(_width("put"), _width("call"))
    if width <= 0:
        return None
    return round(width * 100 * contracts - entry_premium, 2)


def two_x_credit_risk(direction: str, entry_premium: float) -> Optional[float]:
    """2× the credit collected — the classic manage-at-2x-credit-loss planned
    risk for short-premium trades, and the only sane number for undefined-risk
    shapes (naked puts, strangles) where width − credit doesn't exist.
    None for long/debit/custom shapes, where entry_premium is a debit."""
    if direction not in _SHORT_PREMIUM_SHAPES or not entry_premium:
        return None
    return round(2 * entry_premium, 2)


def days_open(entry_at) -> Optional[int]:
    """Calendar days a trade has been open (entry date to today)."""
    dt = _parse_journal_dt(entry_at)
    return (date.today() - dt.date()).days if dt else None


def is_short_premium(direction: str) -> bool:
    """Whether a direction is a net-short-premium shape — the trades where
    entry_premium is credit collected and %-of-credit / 50%-PT semantics apply."""
    return direction in _SHORT_PREMIUM_SHAPES


# Shapes with net-short put/call legs whose strikes drive breakeven and threat.
_SHORT_PREMIUM_SHAPES = {
    "short_put", "put_credit_spread", "short_call", "covered_call",
    "call_credit_spread", "short_strangle", "short_straddle",
    "iron_condor", "iron_butterfly", "jade_lizard", "reverse_jade_lizard",
}


def short_premium_profile(fills: list[dict], direction: str,
                          entry_premium: float, spot: Optional[float] = None,
                          greeks: Optional[dict] = None) -> dict:
    """Breakeven(s), spot cushion, and moneyness grade for short-premium shapes.

    Breakeven = short strike −/+ the full net credit per share on the put/call
    side (the classic strangle/condor convention; single-sided shapes have one).
    cushion_pct = % move in spot to the NEAREST short strike, negative once
    breached. moneyness: ITM past the strike, ATM within 2% of it (the legacy
    calculate_moneyness threshold), OTM otherwise. Without spot, moneyness
    falls back to the worst short-leg |delta| (>=0.50 ITM, >=0.35 ATM) and
    cushion stays None. Long/debit/custom shapes return all-None."""
    out = {"breakevens": None, "cushion_pct": None, "moneyness": None}
    if direction not in _SHORT_PREMIUM_SHAPES:
        return out
    open_legs = {s: q for s, q in net_contracts_by_symbol(fills).items() if q != 0}
    info = {}
    for f in fills:
        if f.get("option_symbol") and f.get("strike") is not None:
            info[f["option_symbol"]] = (f.get("option_type"), float(f["strike"]))
    short_puts = [info[s][1] for s, q in open_legs.items()
                  if q < 0 and s in info and info[s][0] == "put"]
    short_calls = [info[s][1] for s, q in open_legs.items()
                   if q < 0 and s in info and info[s][0] == "call"]
    if not short_puts and not short_calls:
        return out

    contracts = min(abs(q) for s, q in open_legs.items() if q < 0)
    cps = (entry_premium / (contracts * 100)
           if entry_premium and contracts else None)  # credit per share
    k_put = max(short_puts) if short_puts else None    # nearest-the-money short put
    k_call = min(short_calls) if short_calls else None
    if cps is not None:
        bes = []
        if k_put is not None:
            bes.append(round(k_put - cps, 2))
        if k_call is not None:
            bes.append(round(k_call + cps, 2))
        out["breakevens"] = bes or None

    if spot:
        sides = []
        if k_put is not None:
            sides.append((spot - k_put) / spot * 100)
        if k_call is not None:
            sides.append((k_call - spot) / spot * 100)
        cushion = min(sides)
        out["cushion_pct"] = round(cushion, 1)
        out["moneyness"] = ("ATM" if abs(cushion) <= 2
                            else "ITM" if cushion < 0 else "OTM")
    elif greeks:
        deltas = [abs(float((greeks.get(s) or {}).get("delta") or 0))
                  for s, q in open_legs.items() if q < 0]
        deltas = [d for d in deltas if d > 0]
        if deltas:
            worst = max(deltas)
            out["moneyness"] = ("ITM" if worst >= 0.50
                                else "ATM" if worst >= 0.35 else "OTM")
    return out


def assignment_capital(fills: list[dict]) -> Optional[float]:
    """Cash needed if every net-short put is assigned: Σ strike × 100 × |net|.
    For a cash-secured put sold WANTING the shares, this is the real commitment
    — the 'risk' is a buy order, not a loss. None when there is no short put."""
    open_legs = {s: q for s, q in net_contracts_by_symbol(fills).items() if q < 0}
    if not open_legs:
        return None
    info = {}
    for f in fills:
        if f.get("option_symbol") and f.get("strike") is not None:
            info[f["option_symbol"]] = (f.get("option_type"), float(f["strike"]))
    total = sum(info[s][1] * 100 * abs(q) for s, q in open_legs.items()
                if s in info and info[s][0] == "put")
    return round(total, 2) if total else None


def day_change_pnl(fills: list[dict], marks_now: dict, marks_ref: dict,
                   today_iso: str) -> Optional[float]:
    """Today's unrealized change for an open trade: per open leg,
    net × 100 × (mark_now − reference). Reference = the leg's last mark before
    today; legs first filled today use today's average fill price instead
    (scale-ins onto an existing leg approximate with the prior mark). None —
    never 0 — when any open leg lacks a current mark or a usable reference."""
    open_legs = {s: q for s, q in net_contracts_by_symbol(fills).items() if q != 0}
    if not open_legs:
        return None
    total = 0.0
    for s, q in open_legs.items():
        now = marks_now.get(s)
        if now is None:
            return None
        ref = marks_ref.get(s)
        if ref is None:
            todays = [f for f in fills if f["option_symbol"] == s
                      and str(f.get("trade_date") or f.get("executed_at") or "")[:10] == today_iso]
            qty = sum(f["quantity"] for f in todays)
            if not qty:
                return None
            ref = sum(abs(f.get("price") or 0) * f["quantity"] for f in todays) / qty
        total += q * 100 * (float(now) - float(ref))
    return round(total, 2)


def positions_summary(positions: list[dict]) -> dict:
    """Book-level rollup of the open positions payload. Greeks/unrealized sum
    only the positions that have them; *_covered says how many that was."""
    marked = [p for p in positions if p.get("unrealized_pnl") is not None]
    deltas = [p["position_delta"] for p in positions if p.get("position_delta") is not None]
    thetas = [p["position_theta"] for p in positions if p.get("position_theta") is not None]
    risks = [p["max_loss"] for p in positions if p.get("max_loss") is not None]
    days = [p["day_pnl"] for p in positions if p.get("day_pnl") is not None]
    return {
        "total_day_pnl": round(sum(days), 2) if days else None,
        "day_covered": len(days),
        "count": len(positions),
        "total_credit": round(sum(p.get("entry_premium") or 0 for p in positions), 2),
        "total_liquidation": round(sum(p["liquidation_value"] for p in marked), 2) if marked else None,
        "total_unrealized": round(sum(p["unrealized_pnl"] for p in marked), 2) if marked else None,
        "marked_count": len(marked),
        "net_delta": round(sum(deltas), 1) if deltas else None,
        "net_theta": round(sum(thetas), 2) if thetas else None,
        "greeks_covered": len(deltas),
        "defined_risk_total": round(sum(risks), 2) if risks else None,
        "defined_risk_count": len(risks),
    }


def expired_realized_pnl(fills: list[dict]) -> float:
    """Realized P&L for a trade whose remaining legs expired worthless: the
    canonical sum(value) - fees, with nothing further to pay or receive."""
    return round(sum(f.get("value") or 0 for f in fills)
                 - sum(f.get("fees") or 0 for f in fills), 2)


def calculate_premium(trade: dict) -> float:
    """Calculate premium amount for a trade (always positive)."""
    return trade["price"] * trade["quantity"] * 100


def calculate_net_premium(trade: dict) -> float:
    """Calculate net premium (positive for sells, negative for buys)."""
    premium = calculate_premium(trade)
    return premium if trade["action"] == "sell" else -premium


def calculate_realized_pnl(trade: dict) -> Optional[float]:
    """
    Calculate realized P&L for a closed trade.

    Returns None if trade is not closed or has no closed_price.
    """
    if not trade.get("closed_date") or trade.get("closed_price") is None:
        return None

    commission = trade.get("commission") or 0
    multiplier = trade["quantity"] * 100

    if trade["action"] == "sell":
        # Sold to open, bought to close
        return (trade["price"] - trade["closed_price"]) * multiplier - commission
    else:
        # Bought to open, sold to close
        return (trade["closed_price"] - trade["price"]) * multiplier - commission


def calculate_unrealized_pnl(trade: dict, snapshot: Optional[dict]) -> Optional[float]:
    """
    Calculate unrealized P&L based on current price snapshot.

    Uses mid price if bid/ask available, otherwise last price.
    Returns None if trade is closed or no price data available.
    Subtracts commission from P&L to reflect true profit.
    """
    if trade.get("closed_date") or not snapshot:
        return None

    bid = snapshot.get("bid")
    ask = snapshot.get("ask")
    last = snapshot.get("last")
    if bid is not None and ask is not None:
        mid = (bid + ask) / 2
        # Use last if it's inside the spread — better mark for illiquid options
        current_price = last if (last is not None and bid <= last <= ask) else mid
    else:
        current_price = last

    if current_price is None:
        return None

    multiplier = trade["quantity"] * 100
    commission = trade.get("commission") or 0

    if trade["action"] == "sell":
        return (trade["price"] - current_price) * multiplier - commission
    else:
        return (current_price - trade["price"]) * multiplier - commission


def calculate_day_pnl(trade: dict, current_snapshot: Optional[dict], prev_snapshot: Optional[dict]) -> Optional[float]:
    """
    Calculate today's P&L change: current value vs previous trading day's close.
    Uses mid price if bid/ask available, otherwise last price.
    """
    if not current_snapshot or not prev_snapshot:
        return None

    def get_price(snap: dict) -> Optional[float]:
        if snap.get("bid") and snap.get("ask"):
            return (snap["bid"] + snap["ask"]) / 2
        return snap.get("last")

    current_price = get_price(current_snapshot)
    prev_price = get_price(prev_snapshot)

    if current_price is None or prev_price is None:
        return None

    multiplier = trade["quantity"] * 100
    if trade["action"] == "sell":
        return (prev_price - current_price) * multiplier
    else:
        return (current_price - prev_price) * multiplier


def calculate_profit_pct(trade: dict, unrealized_pnl: Optional[float]) -> Optional[float]:
    """
    Calculate profit as percentage of max profit/cost.

    For sold options: profit relative to premium received.
    For bought options: profit relative to cost.
    """
    if trade.get("closed_date") or unrealized_pnl is None:
        return None

    max_profit = trade["price"] * trade["quantity"] * 100
    if max_profit <= 0:
        return None

    return (unrealized_pnl / max_profit) * 100


def check_target_hit(profit_target: Optional[int], profit_pct: Optional[float]) -> Optional[bool]:
    """Check if profit target percentage has been reached."""
    if profit_target is None or profit_pct is None:
        return None
    return profit_pct >= profit_target


_CAPITAL_STRATEGIES = {None, "naked_put", "cash_secured_put"}

def calculate_capital_deployed(trade: dict) -> Optional[float]:
    """
    Capital tied up by naked/cash-secured puts only.

    Spread legs are excluded — their risk is bounded by the long leg,
    not the full strike value. strategy_type must be in _CAPITAL_STRATEGIES
    (or absent) for the trade to count.
    """
    if trade.get("closed_date"):
        return None
    if trade["action"] == "sell" and trade.get("option_type") == "put":
        if trade.get("strategy_type") not in _CAPITAL_STRATEGIES:
            return None
        return round(float(trade["strike"]) * 100 * float(trade["quantity"]), 2)
    return None


def calculate_dte(expiration_date) -> int:
    """Calculate days to expiration from today."""
    if isinstance(expiration_date, str):
        expiration_date = date.fromisoformat(expiration_date)
    return (expiration_date - date.today()).days


def calculate_moneyness(trade: dict, underlying_price: Optional[float]) -> str:
    """
    Determine if option is ITM, ATM, or OTM.

    ATM threshold: within 2% of strike price.
    For calls: ITM if underlying > strike
    For puts: ITM if underlying < strike
    """
    if underlying_price is None:
        return "Unknown"

    strike = trade["strike"]
    pct_diff = abs(underlying_price - strike) / strike * 100

    if pct_diff <= 2:
        return "ATM"

    if trade["option_type"] == "call":
        return "ITM" if underlying_price > strike else "OTM"
    else:
        return "ITM" if underlying_price < strike else "OTM"


def compute_trade_metrics(
    trade: dict,
    snapshot: Optional[dict] = None,
    underlying_price: Optional[float] = None
) -> TradeMetrics:
    """
    Compute all metrics for a trade in a single call.

    This is the primary entry point for trade enrichment, consolidating
    all individual calculation functions.
    """
    metrics = TradeMetrics()

    metrics.premium = calculate_premium(trade)
    metrics.capital_deployed = calculate_capital_deployed(trade)
    metrics.dte = calculate_dte(trade["expiration"])
    metrics.moneyness = calculate_moneyness(trade, underlying_price)

    if trade.get("closed_date"):
        metrics.realized_pnl = calculate_realized_pnl(trade)
    else:
        metrics.unrealized_pnl = calculate_unrealized_pnl(trade, snapshot)
        metrics.profit_pct = calculate_profit_pct(trade, metrics.unrealized_pnl)
        metrics.target_hit = check_target_hit(
            trade.get("profit_target"),
            metrics.profit_pct
        )

    return metrics


def compute_analytics_single_pass(trades: list[dict]) -> dict:
    """
    Compute analytics in two passes: premium per-leg, P&L per strategy unit.

    Spreads are netted into one unit so legs don't inflate counts or distort P&L.
    """
    by_ticker: dict[str, AggregateStats] = {}
    by_month_premium: dict[str, dict] = {}
    by_month_pnl: dict[str, float] = {}
    by_strategy: dict[str, AggregateStats] = {}
    calls_stats = AggregateStats()
    puts_stats = AggregateStats()
    cumulative_pnl_data: list[tuple[str, float]] = []

    # Pass 1: premium tracking per leg (additive, grouping not needed)
    for trade in trades:
        premium = calculate_premium(trade)
        net_premium = calculate_net_premium(trade)
        ticker = trade["ticker"]
        trade_month = trade["trade_date"][:7]

        if ticker not in by_ticker:
            by_ticker[ticker] = AggregateStats()
        by_ticker[ticker].premium += net_premium
        if trade["action"] == "sell":
            by_ticker[ticker].collected_premium += premium

        if trade_month not in by_month_premium:
            by_month_premium[trade_month] = {"collected": 0.0, "paid": 0.0}
        if trade["action"] == "sell":
            by_month_premium[trade_month]["collected"] += premium
        else:
            by_month_premium[trade_month]["paid"] += premium

        option_type = (trade.get("option_type") or "put").lower()
        c_or_p = calls_stats if option_type == "call" else puts_stats
        c_or_p.premium += net_premium
        if trade["action"] == "sell":
            c_or_p.collected_premium += premium

    # Pass 2: group closed trades into strategy units for P&L metrics
    closed_trades = [t for t in trades if t.get("closed_date") and t.get("closed_price") is not None]
    by_group: dict = {}
    ungrouped_closed = []
    for t in closed_trades:
        gid = t.get("strategy_group_id")
        if gid:
            by_group.setdefault(int(gid), []).append(t)
        else:
            ungrouped_closed.append(t)

    def _accumulate_unit(legs: list, pnl: float, rep: dict) -> None:
        ticker = rep["ticker"]
        close_date = rep["closed_date"]
        close_month = close_date[:7]
        gross_premium = sum(calculate_premium(l) for l in legs if l["action"] == "sell")
        net_prem = sum(calculate_net_premium(l) for l in legs)
        is_winner = pnl > 0
        st = rep.get("strategy_type") or (
            "naked_put" if (rep.get("option_type") or "").lower() == "put" else "naked_call"
        )
        option_type = (rep.get("option_type") or "put").lower()

        if ticker not in by_ticker:
            by_ticker[ticker] = AggregateStats()
        by_ticker[ticker].pnl += pnl
        by_ticker[ticker].count += 1
        if is_winner:
            by_ticker[ticker].wins += 1
        elif pnl < 0:
            by_ticker[ticker].losses += 1

        by_month_pnl[close_month] = by_month_pnl.get(close_month, 0.0) + pnl
        cumulative_pnl_data.append((close_date, pnl))

        if st not in by_strategy:
            by_strategy[st] = AggregateStats()
        by_strategy[st].pnl += pnl
        by_strategy[st].count += 1
        by_strategy[st].premium += net_prem
        by_strategy[st].collected_premium += gross_premium
        if is_winner:
            by_strategy[st].wins += 1
        elif pnl < 0:
            by_strategy[st].losses += 1

        c_or_p = calls_stats if option_type == "call" else puts_stats
        c_or_p.pnl += pnl
        c_or_p.count += 1
        if is_winner:
            c_or_p.wins += 1
        elif pnl < 0:
            c_or_p.losses += 1

    for legs in by_group.values():
        pnl = sum(calculate_realized_pnl(l) or 0.0 for l in legs)
        rep = next((l for l in legs if l["action"] == "sell"), legs[0])
        _accumulate_unit(legs, pnl, rep)

    for t in ungrouped_closed:
        if t.get("action") != "sell":
            continue
        _accumulate_unit([t], calculate_realized_pnl(t) or 0.0, t)

    # Sort and build cumulative P&L
    cumulative_pnl_data.sort(key=lambda x: x[0])
    cumulative = 0.0
    cumulative_pnl = []
    for dt, pnl in cumulative_pnl_data:
        cumulative += pnl
        cumulative_pnl.append({"date": dt, "cumulative_pnl": round(cumulative, 2)})

    return {
        "by_ticker": {k: {"premium": round(v.premium, 2), "realized_pnl": round(v.pnl, 2), "count": v.count}
                      for k, v in sorted(by_ticker.items(), key=lambda x: x[1].pnl, reverse=True)},
        "by_month_premium": {k: {
            "collected": round(v["collected"], 2),
            "paid": round(v["paid"], 2),
            "net": round(v["collected"] - v["paid"], 2)
        } for k, v in sorted(by_month_premium.items())},
        "by_month_pnl": {k: round(v, 2) for k, v in sorted(by_month_pnl.items())},
        "by_strategy": {k: v.to_dict() for k, v in by_strategy.items()},
        "calls_stats": calls_stats.to_dict(),
        "puts_stats": puts_stats.to_dict(),
        "cumulative_pnl": cumulative_pnl
    }


# ===== STRATEGY-AWARE UNIT CALCULATION =====

def compute_closed_units(closed_trades: list[dict]) -> list[dict]:
    """
    Group closed trades by strategy_group_id and return one P&L unit per position.

    This is the canonical function for strategy-level P&L aggregation used
    across analytics views. Spreads/multi-leg strategies are netted into a
    single unit; standalone trades become individual units.

    P&L is fully commission-aware via calculate_realized_pnl().

    Args:
        closed_trades: List of closed trade dicts. Each must have:
            strategy_group_id, action, price, quantity, closed_price,
            ticker, strategy_type, option_type, closed_date, trade_date, status.
            The strategy_type field comes from joining strategy_groups.

    Returns:
        List of unit dicts, each with:
            pnl              — net realized P&L for the position (commission-aware)
            premium_collected — net opening credit (positive = credit strategy)
            ticker           — primary ticker symbol
            strategy_type    — strategy type string
            closed_date      — date position was closed
            trade_date       — date position was opened
            status           — exit type: 'closed', 'expired', or 'assigned'
    """
    by_group: dict[int, list] = {}
    ungrouped: list = []

    for t in closed_trades:
        gid = t.get("strategy_group_id")
        if gid:
            by_group.setdefault(int(gid), []).append(t)
        else:
            ungrouped.append(t)

    units: list[dict] = []

    for legs in by_group.values():
        pnl = sum(calculate_realized_pnl(t) or 0.0 for t in legs)
        premium_collected = sum(calculate_net_premium(t) for t in legs)
        rep = next((t for t in legs if t["action"] == "sell"), legs[0])
        st = rep.get("strategy_type") or (
            "naked_put" if (rep.get("option_type") or "").lower() == "put" else "naked_call"
        )
        units.append({
            "pnl":               pnl,
            "premium_collected": premium_collected,
            "ticker":            rep["ticker"],
            "strategy_type":     st,
            "closed_date":       rep["closed_date"],
            "trade_date":        rep["trade_date"],
            "status":            rep["status"],
        })

    for t in ungrouped:
        if t["action"] != "sell":
            continue
        pnl = calculate_realized_pnl(t) or 0.0
        premium_collected = calculate_net_premium(t)
        st = t.get("strategy_type") or (
            "naked_put" if (t.get("option_type") or "").lower() == "put" else "naked_call"
        )
        units.append({
            "pnl":               pnl,
            "premium_collected": premium_collected,
            "ticker":            t["ticker"],
            "strategy_type":     st,
            "closed_date":       t["closed_date"],
            "trade_date":        t["trade_date"],
            "status":            t["status"],
        })

    return units


# ===== TRUE P&L CALCULATION FUNCTIONS =====

def calculate_stock_unrealized_pnl(position: dict, current_price: float) -> float:
    """
    Calculate unrealized P&L on held stock using effective cost basis.

    Uses effective_cost_basis if available (for assigned positions),
    otherwise falls back to regular cost_basis.
    """
    basis = position.get("effective_cost_basis") or position["cost_basis"]
    return (current_price - basis) * position["shares"]


def calculate_stock_realized_pnl(position: dict) -> Optional[float]:
    """
    Calculate realized P&L on sold stock using effective cost basis.

    Returns None if position is not sold.
    Uses effective_cost_basis if available, otherwise regular cost_basis.
    """
    if not position.get("sold_price"):
        return None
    basis = position.get("effective_cost_basis") or position["cost_basis"]
    return (position["sold_price"] - basis) * position["shares"]


def compute_true_pnl(
    trades: list[dict],
    stock_positions: list[dict],
    stock_prices: dict[str, float],
    option_snapshots: dict[int, dict]
) -> dict:
    """
    Compute comprehensive True P&L breakdown.

    Args:
        trades: All trades for the account
        stock_positions: All stock positions (held and sold)
        stock_prices: Current stock prices by ticker
        option_snapshots: Latest price snapshots by trade_id

    Returns:
        Dictionary with complete P&L breakdown including:
        - Summary totals
        - By-ticker breakdown
        - By-month breakdown
    """
    from collections import defaultdict

    # Initialize totals
    totals = {
        "premium_pnl": 0.0,
        "stock_realized_pnl": 0.0,
        "stock_unrealized_pnl": 0.0,
        "options_unrealized_pnl": 0.0,
    }

    # By-ticker aggregation
    by_ticker = defaultdict(lambda: {
        "premium_pnl": 0.0,
        "stock_realized_pnl": 0.0,
        "stock_unrealized_pnl": 0.0,
        "options_unrealized_pnl": 0.0,
        "stock_shares_held": 0,
        "stock_effective_basis": None,
        "stock_market_value": 0.0,
        "total_effective_cost": 0.0,
    })

    # By-month aggregation (only realized)
    by_month = defaultdict(lambda: {
        "premium_pnl": 0.0,
        "stock_realized_pnl": 0.0,
    })

    # Process options trades
    for trade in trades:
        ticker = trade["ticker"]
        td = by_ticker[ticker]

        if trade.get("closed_date"):
            # Closed trade - realized premium P&L
            realized = calculate_realized_pnl(trade)
            if realized is not None:
                td["premium_pnl"] += realized
                totals["premium_pnl"] += realized

                # Track by month (use closed date)
                close_month = trade["closed_date"][:7]
                by_month[close_month]["premium_pnl"] += realized
        else:
            # Open trade - unrealized options P&L
            snapshot = option_snapshots.get(trade["id"])
            unrealized = calculate_unrealized_pnl(trade, snapshot)
            if unrealized is not None:
                td["options_unrealized_pnl"] += unrealized
                totals["options_unrealized_pnl"] += unrealized

    # Process stock positions
    for position in stock_positions:
        ticker = position["ticker"]
        td = by_ticker[ticker]
        current_price = stock_prices.get(ticker)

        if position.get("sold_date"):
            # Sold position - realized stock P&L
            realized = calculate_stock_realized_pnl(position)
            if realized is not None:
                td["stock_realized_pnl"] += realized
                totals["stock_realized_pnl"] += realized

                # Track by month (use sold date)
                sold_month = position["sold_date"][:7]
                by_month[sold_month]["stock_realized_pnl"] += realized
        else:
            # Held position - unrealized stock P&L
            td["stock_shares_held"] += position["shares"]
            basis = position.get("effective_cost_basis") or position["cost_basis"]
            td["total_effective_cost"] += basis * position["shares"]

            if current_price:
                unrealized = calculate_stock_unrealized_pnl(position, current_price)
                td["stock_unrealized_pnl"] += unrealized
                totals["stock_unrealized_pnl"] += unrealized
                td["stock_market_value"] += current_price * position["shares"]

    # Calculate effective basis per share for each ticker
    for ticker, td in by_ticker.items():
        if td["stock_shares_held"] > 0:
            td["stock_effective_basis"] = td["total_effective_cost"] / td["stock_shares_held"]

    # Build response
    total_realized = totals["premium_pnl"] + totals["stock_realized_pnl"]
    total_unrealized = totals["options_unrealized_pnl"] + totals["stock_unrealized_pnl"]
    true_pnl = total_realized + total_unrealized

    ticker_list = []
    for ticker, td in sorted(by_ticker.items(), key=lambda x: (
        x[1]["premium_pnl"] + x[1]["stock_realized_pnl"] +
        x[1]["stock_unrealized_pnl"] + x[1]["options_unrealized_pnl"]
    ), reverse=True):
        ticker_realized = td["premium_pnl"] + td["stock_realized_pnl"]
        ticker_unrealized = td["options_unrealized_pnl"] + td["stock_unrealized_pnl"]
        ticker_list.append({
            "ticker": ticker,
            "premium_pnl": round(td["premium_pnl"], 2),
            "stock_realized_pnl": round(td["stock_realized_pnl"], 2),
            "stock_unrealized_pnl": round(td["stock_unrealized_pnl"], 2),
            "options_unrealized_pnl": round(td["options_unrealized_pnl"], 2),
            "total_realized_pnl": round(ticker_realized, 2),
            "total_unrealized_pnl": round(ticker_unrealized, 2),
            "true_pnl": round(ticker_realized + ticker_unrealized, 2),
            "stock_shares_held": td["stock_shares_held"],
            "stock_effective_basis": round(td["stock_effective_basis"], 2) if td["stock_effective_basis"] else None,
            "stock_market_value": round(td["stock_market_value"], 2),
        })

    month_list = []
    for month, md in sorted(by_month.items()):
        month_realized = md["premium_pnl"] + md["stock_realized_pnl"]
        month_list.append({
            "month": month,
            "premium_pnl": round(md["premium_pnl"], 2),
            "stock_realized_pnl": round(md["stock_realized_pnl"], 2),
            "total_realized_pnl": round(month_realized, 2),
        })

    return {
        "premium_pnl": round(totals["premium_pnl"], 2),
        "stock_realized_pnl": round(totals["stock_realized_pnl"], 2),
        "stock_unrealized_pnl": round(totals["stock_unrealized_pnl"], 2),
        "options_unrealized_pnl": round(totals["options_unrealized_pnl"], 2),
        "total_realized_pnl": round(total_realized, 2),
        "total_unrealized_pnl": round(total_unrealized, 2),
        "true_pnl": round(true_pnl, 2),
        "by_ticker": ticker_list,
        "by_month": month_list,
    }


def compute_wheel_pnl(wheel_group: dict, trades: list[dict], stock_positions: list[dict]) -> dict:
    """
    Calculate complete wheel cycle P&L.

    Args:
        wheel_group: The strategy group record
        trades: Trades belonging to this wheel (filtered by strategy_group_id)
        stock_positions: Stock positions that may be related to wheel trades

    Returns:
        Dictionary with wheel summary including:
        - csp_premium: Premium from cash-secured puts
        - cc_premium: Premium from covered calls
        - total_premium: Combined premium
        - effective_cost_basis: Adjusted cost basis on stock
        - stock_pnl: Realized P&L when stock sold/called away
        - total_pnl: Everything combined
        - status: 'active' or 'completed'
    """
    csp_premium = 0.0
    cc_premium = 0.0
    effective_basis = None
    stock_pnl = None
    status = "active"
    legs_count = len(trades)

    # Track trade IDs for finding related stock positions
    trade_ids = {t["id"] for t in trades}

    for trade in trades:
        premium = trade["price"] * trade["quantity"] * 100

        if trade["option_type"] == "put" and trade["action"] == "sell":
            # Cash-secured put
            csp_premium += premium
        elif trade["option_type"] == "call" and trade["action"] == "sell":
            # Covered call
            cc_premium += premium

    # Find stock positions acquired from wheel trades
    wheel_stock_positions = [
        sp for sp in stock_positions
        if sp.get("acquired_from_trade_id") in trade_ids
    ]

    # Calculate effective basis and stock P&L
    if wheel_stock_positions:
        total_shares = 0
        total_effective_cost = 0.0

        for sp in wheel_stock_positions:
            basis = sp.get("effective_cost_basis") or sp["cost_basis"]
            total_shares += sp["shares"]
            total_effective_cost += basis * sp["shares"]

            if sp.get("sold_date"):
                # Stock was sold - calculate realized P&L
                realized = calculate_stock_realized_pnl(sp)
                if realized is not None:
                    stock_pnl = (stock_pnl or 0) + realized

        if total_shares > 0:
            effective_basis = total_effective_cost / total_shares

    # Determine wheel status
    # Wheel is completed if all trades are closed AND stock is sold (if any was acquired)
    all_trades_closed = all(t.get("closed_date") for t in trades)
    all_stock_sold = all(sp.get("sold_date") for sp in wheel_stock_positions) if wheel_stock_positions else True

    if all_trades_closed and all_stock_sold and trades:
        status = "completed"

    total_premium = csp_premium + cc_premium
    total_pnl = total_premium + (stock_pnl or 0)

    return {
        "wheel_id": wheel_group["id"],
        "ticker": wheel_group["underlying_ticker"],
        "start_date": wheel_group["opened_date"],
        "end_date": wheel_group.get("closed_date"),
        "status": status,
        "csp_premium": round(csp_premium, 2),
        "cc_premium": round(cc_premium, 2),
        "total_premium": round(total_premium, 2),
        "effective_cost_basis": round(effective_basis, 2) if effective_basis else None,
        "stock_pnl": round(stock_pnl, 2) if stock_pnl is not None else None,
        "total_pnl": round(total_pnl, 2),
        "legs_count": legs_count,
    }


def calculate_hold_days(trade_date: str, closed_date: str) -> int:
    """Calculate the number of days a trade was held."""
    if not trade_date or not closed_date:
        return 0
    from datetime import datetime
    open_dt = datetime.fromisoformat(trade_date) if isinstance(trade_date, str) else trade_date
    close_dt = datetime.fromisoformat(closed_date) if isinstance(closed_date, str) else closed_date
    return (close_dt - open_dt).days


def get_dte_range(dte: int) -> str:
    """Categorize DTE into ranges."""
    if dte <= 7:
        return "0-7"
    elif dte <= 21:
        return "8-21"
    elif dte <= 45:
        return "22-45"
    else:
        return "45+"


def get_day_of_week(date_str: str) -> tuple[str, int]:
    """Extract day name and index from date string."""
    from datetime import datetime
    dt = datetime.fromisoformat(date_str) if isinstance(date_str, str) else date_str
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return days[dt.weekday()], dt.weekday()


def calculate_streaks(trades_by_close_date: list[dict]) -> dict:
    """
    Calculate win/loss streak information from trades sorted by close date.

    Returns dict with:
    - current_streak: positive for wins, negative for losses
    - current_streak_type: "win" or "loss" or "none"
    - longest_win_streak
    - longest_loss_streak
    - last_10_results: list of "W" or "L"
    """
    if not trades_by_close_date:
        return {
            "current_streak": 0,
            "current_streak_type": "none",
            "longest_win_streak": 0,
            "longest_loss_streak": 0,
            "last_10_results": []
        }

    results = []
    for trade in trades_by_close_date:
        pnl = trade["pnl"] if "pnl" in trade else calculate_realized_pnl(trade)
        if pnl is not None and pnl != 0:
            results.append("W" if pnl > 0 else "L")

    if not results:
        return {
            "current_streak": 0,
            "current_streak_type": "none",
            "longest_win_streak": 0,
            "longest_loss_streak": 0,
            "last_10_results": []
        }

    # Calculate current streak (from most recent)
    current_streak = 0
    current_type = results[-1] if results else "none"
    for r in reversed(results):
        if r == current_type:
            current_streak += 1
        else:
            break

    # Calculate longest streaks
    longest_win = 0
    longest_loss = 0
    current_win = 0
    current_loss = 0

    for r in results:
        if r == "W":
            current_win += 1
            current_loss = 0
            longest_win = max(longest_win, current_win)
        else:
            current_loss += 1
            current_win = 0
            longest_loss = max(longest_loss, current_loss)

    return {
        "current_streak": current_streak if current_type == "W" else -current_streak,
        "current_streak_type": "win" if current_type == "W" else "loss",
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "last_10_results": results[-10:]
    }


def compute_extended_analytics(trades: list[dict]) -> dict:
    """
    Compute all extended analytics grouped at the strategy level.

    Spreads and multi-leg strategies are netted into a single unit so that
    individual legs don't inflate counts or distort P&L statistics.
    """
    closed_trades = [t for t in trades if t.get("closed_date") and t.get("closed_price") is not None]

    # Group closed legs into strategy-level units (same logic as compute_closed_units)
    by_group: dict = {}
    ungrouped = []
    for t in closed_trades:
        gid = t.get("strategy_group_id")
        if gid:
            by_group.setdefault(int(gid), []).append(t)
        else:
            ungrouped.append(t)

    units = []
    for legs in by_group.values():
        pnl = sum(calculate_realized_pnl(l) or 0.0 for l in legs)
        rep = next((l for l in legs if l["action"] == "sell"), legs[0])
        gross_premium = sum(calculate_premium(l) for l in legs if l["action"] == "sell")
        st = rep.get("strategy_type") or (
            "naked_put" if (rep.get("option_type") or "").lower() == "put" else "naked_call"
        )
        units.append({
            "pnl": pnl,
            "premium": gross_premium,
            "ticker": rep["ticker"],
            "strategy_type": st,
            "closed_date": rep["closed_date"],
            "trade_date": rep["trade_date"],
            "expiration": rep.get("expiration"),
            "moneyness": rep.get("moneyness", "Unknown"),
            "strike": rep.get("strike") or 0,
            "option_type": rep.get("option_type") or "",
            "action": rep.get("action") or "sell",
            "id": rep.get("id") or 0,
        })

    for t in ungrouped:
        if t.get("action") != "sell":
            continue
        st = t.get("strategy_type") or (
            "naked_put" if (t.get("option_type") or "").lower() == "put" else "naked_call"
        )
        units.append({
            "pnl": calculate_realized_pnl(t) or 0.0,
            "premium": calculate_premium(t),
            "ticker": t["ticker"],
            "strategy_type": st,
            "closed_date": t["closed_date"],
            "trade_date": t["trade_date"],
            "expiration": t.get("expiration"),
            "moneyness": t.get("moneyness", "Unknown"),
            "strike": t.get("strike") or 0,
            "option_type": t.get("option_type") or "",
            "action": t.get("action") or "sell",
            "id": t.get("id") or 0,
        })

    # Initialize accumulators
    by_ticker = {}
    by_dte = {"0-7": [], "8-21": [], "22-45": [], "45+": []}
    by_day_opened = {i: {"count": 0, "pnl": 0.0} for i in range(7)}
    by_day_closed = {i: {"count": 0, "pnl": 0.0, "wins": 0, "losses": 0} for i in range(7)}
    by_strategy = {}
    by_moneyness = {"ITM": {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0},
                    "ATM": {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0},
                    "OTM": {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0},
                    "Unknown": {"count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}}

    all_units_with_pnl = []
    winner_hold_days = []
    loser_hold_days = []

    sorted_by_close = sorted(units, key=lambda u: u.get("closed_date", ""))

    for unit in units:
        pnl = unit["pnl"]
        ticker = unit["ticker"]
        premium = unit["premium"]
        is_winner = pnl > 0

        hold_days = calculate_hold_days(unit["trade_date"], unit["closed_date"])

        # By ticker
        if ticker not in by_ticker:
            by_ticker[ticker] = {"wins": 0, "losses": 0, "total_pnl": 0.0}
        by_ticker[ticker]["wins" if is_winner else "losses"] += 1
        by_ticker[ticker]["total_pnl"] += pnl

        # By DTE (approximate DTE at entry using short leg expiration)
        expiration = unit.get("expiration")
        if expiration:
            dte_at_entry = max(0, calculate_dte(expiration) + hold_days)
        else:
            dte_at_entry = 0
        dte_range = get_dte_range(dte_at_entry)
        by_dte[dte_range].append({"pnl": pnl, "is_winner": is_winner})

        # By day of week
        _, open_day = get_day_of_week(unit["trade_date"])
        _, close_day = get_day_of_week(unit["closed_date"])
        by_day_opened[open_day]["count"] += 1
        by_day_opened[open_day]["pnl"] += pnl
        by_day_closed[close_day]["count"] += 1
        by_day_closed[close_day]["pnl"] += pnl
        by_day_closed[close_day]["wins" if is_winner else "losses"] += 1

        # By strategy_type
        strategy = unit["strategy_type"]
        if strategy not in by_strategy:
            by_strategy[strategy] = {"count": 0, "total_pnl": 0.0, "wins": 0, "losses": 0, "total_premium": 0.0}
        by_strategy[strategy]["count"] += 1
        by_strategy[strategy]["total_pnl"] += pnl
        by_strategy[strategy]["wins" if is_winner else "losses"] += 1
        by_strategy[strategy]["total_premium"] += premium

        # By moneyness (short leg's moneyness)
        moneyness = unit.get("moneyness", "Unknown")
        if moneyness not in by_moneyness:
            moneyness = "Unknown"
        by_moneyness[moneyness]["count"] += 1
        by_moneyness[moneyness]["wins" if is_winner else "losses"] += 1
        by_moneyness[moneyness]["total_pnl"] += pnl

        # Hold times
        if is_winner:
            winner_hold_days.append(hold_days)
        else:
            loser_hold_days.append(hold_days)

        pnl_pct = (pnl / premium * 100) if premium > 0 else 0
        all_units_with_pnl.append({
            "trade_id": unit["id"],
            "ticker": ticker,
            "strike": unit["strike"],
            "option_type": unit["option_type"],
            "action": unit["action"],
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "trade_date": unit["trade_date"],
            "closed_date": unit["closed_date"],
        })

    # Streaks use pre-computed pnl from units (calculate_streaks checks for "pnl" key)
    streak_info = calculate_streaks(sorted_by_close)

    # Build win rate by ticker (top 10 by trade count)
    win_rate_by_ticker = []
    for ticker, data in sorted(by_ticker.items(), key=lambda x: x[1]["wins"] + x[1]["losses"], reverse=True)[:10]:
        total = data["wins"] + data["losses"]
        win_rate_by_ticker.append({
            "ticker": ticker,
            "wins": data["wins"],
            "losses": data["losses"],
            "total_trades": total,
            "win_rate": round((data["wins"] / total * 100) if total > 0 else 0, 1),
            "total_pnl": round(data["total_pnl"], 2)
        })

    # Build win rate by DTE
    win_rate_by_dte = []
    for dte_range in ["0-7", "8-21", "22-45", "45+"]:
        data = by_dte[dte_range]
        if data:
            wins = sum(1 for d in data if d["is_winner"])
            losses = len(data) - wins
            total_pnl = sum(d["pnl"] for d in data)
            win_rate_by_dte.append({
                "dte_range": dte_range,
                "wins": wins,
                "losses": losses,
                "total_trades": len(data),
                "win_rate": round((wins / len(data) * 100) if data else 0, 1),
                "avg_pnl": round(total_pnl / len(data), 2) if data else 0
            })
        else:
            win_rate_by_dte.append({
                "dte_range": dte_range,
                "wins": 0,
                "losses": 0,
                "total_trades": 0,
                "win_rate": 0,
                "avg_pnl": 0
            })

    # Build hold time analysis
    all_hold_days = winner_hold_days + loser_hold_days
    hold_time_analysis = {
        "avg_hold_days_winners": round(sum(winner_hold_days) / len(winner_hold_days), 1) if winner_hold_days else None,
        "avg_hold_days_losers": round(sum(loser_hold_days) / len(loser_hold_days), 1) if loser_hold_days else None,
        "avg_hold_days_all": round(sum(all_hold_days) / len(all_hold_days), 1) if all_hold_days else None,
        "shortest_winner_days": min(winner_hold_days) if winner_hold_days else None,
        "longest_winner_days": max(winner_hold_days) if winner_hold_days else None,
        "shortest_loser_days": min(loser_hold_days) if loser_hold_days else None,
        "longest_loser_days": max(loser_hold_days) if loser_hold_days else None
    }

    # Build day of week performance
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_of_week_performance = []
    for i in range(7):
        opened = by_day_opened[i]
        closed = by_day_closed[i]
        total_closed = closed["wins"] + closed["losses"]
        day_of_week_performance.append({
            "day": day_names[i],
            "day_index": i,
            "trades_opened": opened["count"],
            "trades_closed": closed["count"],
            "pnl_opened": round(opened["pnl"], 2),
            "pnl_closed": round(closed["pnl"], 2),
            "win_rate_closed": round((closed["wins"] / total_closed * 100) if total_closed > 0 else 0, 1)
        })

    # Build avg profit by strategy
    avg_profit_by_strategy = []
    for strategy, data in by_strategy.items():
        total = data["wins"] + data["losses"]
        avg_profit_by_strategy.append({
            "strategy": strategy,
            "trade_count": data["count"],
            "total_pnl": round(data["total_pnl"], 2),
            "avg_pnl": round(data["total_pnl"] / data["count"], 2) if data["count"] > 0 else 0,
            "win_rate": round((data["wins"] / total * 100) if total > 0 else 0, 1),
            "avg_premium": round(data["total_premium"] / data["count"], 2) if data["count"] > 0 else 0
        })

    # Sort units for top winners and losers
    sorted_units = sorted(all_units_with_pnl, key=lambda x: x["pnl"], reverse=True)
    top_winners = sorted_units[:5]
    top_losers = sorted_units[-5:][::-1] if len(sorted_units) >= 5 else sorted_units[::-1][:5]

    # Build moneyness performance
    moneyness_performance = []
    for m in ["ITM", "ATM", "OTM", "Unknown"]:
        data = by_moneyness[m]
        if data["count"] > 0:
            moneyness_performance.append({
                "moneyness": m,
                "trade_count": data["count"],
                "wins": data["wins"],
                "losses": data["losses"],
                "win_rate": round((data["wins"] / data["count"] * 100), 1),
                "total_pnl": round(data["total_pnl"], 2),
                "avg_pnl": round(data["total_pnl"] / data["count"], 2)
            })

    return {
        "win_rate_by_ticker": win_rate_by_ticker,
        "win_rate_by_dte": win_rate_by_dte,
        "hold_time_analysis": hold_time_analysis,
        "day_of_week_performance": day_of_week_performance,
        "avg_profit_by_strategy": avg_profit_by_strategy,
        "top_winners": top_winners,
        "top_losers": top_losers,
        "streak_info": streak_info,
        "moneyness_performance": moneyness_performance
    }
