"""Journal analytics — the views the journal exists for. All P&L math here,
never inline. Win = pnl > 0; breakeven is neither win nor loss.

The discipline streak rewards *process*, not P&L: a by-the-book loss extends
the streak; a lucky off-plan win does not.

PHILOSOPHY (revised 2026-07-22, Monty): discretionary trading is a legitimate
sleeve of this business, not a vice to be punished — the trader is a premium
seller running systematic AND discretionary edges, not a 0DTE trader fighting
impulses. "On-plan" therefore means the trade followed ITS OWN playbook: it has
a named edge (edge_id) and exited by plan. is_system distinguishes the sleeve
for analytics; it plays no role in adherence. What gets flagged is the unnamed
bet (no edge attached) and the unplanned exit — never discretion itself.

Vocabulary: EDGE = the hypothesis the trader assigns (why the trade should
pay); MECHANISM = the auto-detected structure's playbook (how it's built),
resolved from `direction` — a mechanism can never satisfy the edge bar."""
from collections import defaultdict
from datetime import datetime

# Exits that count as "exited by plan" (vs panic / reversed). "expired" is
# planned: for a premium seller, letting theta run to zero is the plan.
PLANNED_EXITS = {"target", "stop", "time", "scratch", "expired"}


def _stats(pnls: list[float]) -> dict:
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = round(sum(pnls), 2)
    avg_win = round(sum(wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 2) if losses else 0.0
    payoff = round(abs(avg_win / avg_loss), 2) if avg_loss else None
    gross_loss = abs(sum(losses))
    profit_factor = round(sum(wins) / gross_loss, 2) if gross_loss else None
    return {
        "count": n,
        "total_pnl": total,
        "avg_pnl": round(total / n, 2) if n else 0.0,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff,
        "profit_factor": profit_factor,
        "expectancy": round(total / n, 2) if n else 0.0,
    }


def _grouped(trades: list[dict], key_fn, default_key: str) -> list[dict]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        buckets[key_fn(t) or default_key].append(t["realized_pnl"])
    rows = [{"key": k, **_stats(v)} for k, v in buckets.items()]
    rows.sort(key=lambda r: r["total_pnl"], reverse=True)
    return rows


def _is_adherent(t: dict) -> bool:
    """On-plan = a named edge + a planned exit. Sleeve-agnostic by design:
    a watchtower put that ran its playbook is exactly as adherent as a
    Strategy #1 spread."""
    return (t.get("edge_id") is not None) and (t.get("exit_reason") in PLANNED_EXITS)


def _exit_day(t: dict) -> str:
    return str(t.get("exit_at") or "")[:10]


def _is_journaled(t: dict) -> bool:
    """The completeness bar (deliberately the same spirit as adherence): a
    named edge + stated risk at entry, an exit reason once closed. A trade
    flagged assignment_intent (CSP sold wanting the shares) doesn't owe a
    risk number — its 'risk' is capital committed, computed from the strike."""
    entry_ok = (t.get("edge_id") is not None or bool(t.get("no_edge"))) and (
        t.get("planned_risk") is not None or bool(t.get("assignment_intent")))
    if t.get("status") == "closed" or t.get("realized_pnl") is not None:
        return entry_ok and bool(t.get("exit_reason"))
    return entry_ok


def journal_debt(trades: list[dict]) -> dict:
    """Trades owing journal work, open first then newest. Pure; the /debt
    endpoint, the Discord cards, and the nav badge all read this one truth."""
    items = []
    for t in trades:
        missing = []
        if t.get("edge_id") is None and not t.get("no_edge"):
            missing.append("edge")
        if t.get("planned_risk") is None and not t.get("assignment_intent"):
            missing.append("risk")
        closed = t.get("status") == "closed" or t.get("realized_pnl") is not None
        if closed and not t.get("exit_reason"):
            missing.append("exit reason")
        if missing:
            items.append({"id": t["id"], "underlying": t.get("underlying"),
                          "direction": t.get("direction"), "strikes": t.get("strikes"),
                          "status": t.get("status"), "entry_at": t.get("entry_at"),
                          "missing": missing})
    items.sort(key=lambda d: str(d.get("entry_at") or ""), reverse=True)
    items.sort(key=lambda d: d["status"] != "open")  # stable: open first, newest first
    return {"count": len(items), "items": items}


def compute_r_stats(closed: list[dict]) -> dict:
    """R-multiple stats over closed trades that declared a planned risk at entry.
    R = realized_pnl / planned_risk; only meaningful when risk was stated up front."""
    rs = [t["realized_pnl"] / t["planned_risk"]
          for t in closed
          if t.get("planned_risk") and t["planned_risk"] > 0]
    n = len(rs)
    return {
        "count": n,
        "coverage_pct": round(n / len(closed) * 100, 1) if closed else 0.0,
        "avg_r": round(sum(rs) / n, 2) if n else None,
        "best_r": round(max(rs), 2) if n else None,
        "worst_r": round(min(rs), 2) if n else None,
        "total_r": round(sum(rs), 2) if n else None,
    }


def compute_calendar(closed: list[dict]) -> list[dict]:
    """Per-day realized P&L keyed by exit date — the color calendar's data.
    One row per day that had at least one close, oldest first. `r` is the
    day's summed R-multiples over trades that declared a planned risk
    (None when no trade that day did) — feeds the R display mode."""
    days: dict[str, list[dict]] = defaultdict(list)
    for t in closed:
        day = _exit_day(t)
        if day:
            days[day].append(t)
    out = []
    for day, trades in sorted(days.items()):
        pnls = [t["realized_pnl"] for t in trades]
        rs = [t["realized_pnl"] / t["planned_risk"]
              for t in trades if t.get("planned_risk") and t["planned_risk"] > 0]
        out.append({
            "date": day,
            "pnl": round(sum(pnls), 2),
            "trades": len(pnls),
            "wins": sum(1 for p in pnls if p > 0),
            "r": round(sum(rs), 2) if rs else None,
        })
    return out


def _drawdown(closed: list[dict]) -> tuple[float, float]:
    """(max_drawdown, peak) of the cumulative realized P&L curve, in dollars.
    Trades ordered by exit time; drawdown measured from the running high."""
    ordered = sorted(closed, key=lambda t: (t.get("exit_at") or "", t.get("id") or 0))
    cum = peak = max_dd = 0.0
    for t in ordered:
        cum += t["realized_pnl"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return round(max_dd, 2), round(peak, 2)


def _clamp(x: float) -> float:
    return round(max(0.0, min(100.0, x)), 1)


# Composite-score weights (sum to 1). Profit factor carries the most because it is
# the single number that says "does this book make money per dollar it loses".
# journaling (added with the notification system) scores record-keeping itself:
# an unjournaled trade can't teach anything, so completeness is part of the score.
SCORE_WEIGHTS = {
    "win_rate": 0.13, "payoff": 0.13, "profit_factor": 0.23,
    "drawdown": 0.14, "consistency": 0.13, "recovery": 0.14,
    "journaling": 0.10,
}


def compute_score(closed: list[dict]) -> dict:
    """Zella-style composite: six 0-100 components + a weighted total.

    Scaling choices (documented, not hidden): payoff and profit factor saturate at
    3.0 (a 3:1 book maxes the component); recovery factor (total P&L / max DD)
    saturates at 5; consistency penalizes profit concentrated in one day; drawdown
    scores the max DD against the equity-curve peak."""
    stats = _stats([t["realized_pnl"] for t in closed])
    n = stats["count"]
    if n == 0:
        return {"components": {k: 0.0 for k in SCORE_WEIGHTS}, "score": 0.0,
                "trades": 0, "max_drawdown": 0.0}

    max_dd, peak = _drawdown(closed)
    total = stats["total_pnl"]

    payoff = stats["payoff_ratio"]
    pf = stats["profit_factor"]
    day_pnls = [d["pnl"] for d in compute_calendar(closed)]
    best_day = max(day_pnls) if day_pnls else 0.0

    components = {
        "win_rate": _clamp(stats["win_rate"]),
        # No losses yet: a perfect payoff/PF is unearned — cap at 100 only when real.
        "payoff": _clamp(payoff / 3 * 100) if payoff is not None else (100.0 if stats["wins"] else 0.0),
        "profit_factor": _clamp(pf / 3 * 100) if pf is not None else (100.0 if stats["wins"] else 0.0),
        "drawdown": _clamp((1 - max_dd / peak) * 100) if peak > 0 else 0.0,
        "consistency": _clamp((1 - best_day / total) * 100) if total > 0 and best_day > 0 else 0.0,
        "recovery": (_clamp(total / max_dd / 5 * 100) if max_dd > 0
                     else (100.0 if total > 0 else 0.0)),
        "journaling": _clamp(sum(1 for t in closed if _is_journaled(t)) / n * 100),
    }
    score = round(sum(components[k] * w for k, w in SCORE_WEIGHTS.items()), 1)
    return {"components": components, "score": score, "trades": n,
            "max_drawdown": max_dd}


# Structures where entry_premium is a CREDIT and "% of credit kept" means
# something — the shapes the 50%-PT and 21-DTE management rules apply to.
PREMIUM_SHAPES = {
    "short_put", "short_call", "covered_call", "put_credit_spread",
    "call_credit_spread", "short_strangle", "short_straddle",
    "iron_condor", "iron_butterfly", "jade_lizard", "reverse_jade_lizard",
}


def insight_badges(t: dict) -> list[dict]:
    """Rule-based insight badges for one CLOSED trade — plain code, no ML
    (TradeZella's 'insights', reduced to checkable rules). Each badge:
    {key, label, tone} with tone good|warn|bad. Pure; server-side only so
    every surface shows the same verdicts."""
    if t.get("realized_pnl") is None:
        return []
    out = []
    pnl = t["realized_pnl"]
    adherent = _is_adherent(t)

    if t.get("edge_id") is None and not t.get("no_edge"):
        out.append({"key": "unnamed_bet", "label": "unnamed bet", "tone": "bad"})
    if t.get("exit_reason") and t["exit_reason"] not in PLANNED_EXITS:
        out.append({"key": "off_plan_exit", "label": f"off-plan exit ({t['exit_reason']})",
                    "tone": "warn" if pnl > 0 else "bad"})
    if pnl < 0 and adherent:
        out.append({"key": "good_loss", "label": "by-the-book loss", "tone": "good"})

    premium = t.get("direction") in PREMIUM_SHAPES
    entry = t.get("entry_premium")
    exit_p = t.get("exit_premium")
    if premium and entry and entry > 0 and exit_p is not None:
        kept = (entry - exit_p) / entry
        if t.get("exit_reason") == "expired" and pnl > 0:
            out.append({"key": "full_credit", "label": "expired — full credit kept",
                        "tone": "good"})
        elif kept >= 0.5:
            out.append({"key": "pt_hit", "label": f"{round(kept * 100)}% of credit kept",
                        "tone": "good"})
        elif 0 < kept < 0.45 and t.get("exit_reason") in ("target", "discretionary"):
            out.append({"key": "cut_early",
                        "label": f"closed at {round(kept * 100)}% of credit — before the 50% PT",
                        "tone": "warn"})

    # Only meaningful for trades that STARTED outside the 21-DTE management
    # clock and were held into it — a put opened at 10 DTE was never subject
    # to the clock, so it isn't flagged.
    if premium and t.get("expiration") and t.get("exit_at") \
            and t.get("exit_reason") != "expired" \
            and (t.get("dte_at_entry") or 0) > 21:
        try:
            exit_day = datetime.strptime(str(t["exit_at"])[:10], "%Y-%m-%d")
            exp = datetime.strptime(str(t["expiration"])[:10], "%Y-%m-%d")
            if 0 < (exp - exit_day).days < 21:
                out.append({"key": "inside_21dte", "label": "held into 21 DTE",
                            "tone": "warn"})
        except ValueError:
            pass
    return out


def compute_motd(today_trades: list[dict], current_streak: int) -> dict:
    """Message of the day — a discipline digest over trades closed today.
    Coaching rewards process: praises on-plan trades, warns about lucky
    rule-breaks, affirms well-executed losers."""
    closed = [t for t in today_trades if t.get("realized_pnl") is not None]
    n = len(closed)
    system = sum(1 for t in closed if t.get("is_system"))
    convictions = [t["conviction"] for t in closed if t.get("conviction") is not None]
    avg_conv = round(sum(convictions) / len(convictions), 1) if convictions else None
    pnl = round(sum(t["realized_pnl"] for t in closed), 2) if closed else 0.0
    wins = sum(1 for t in closed if t["realized_pnl"] > 0)
    adherent = [t for t in closed if _is_adherent(t)]
    won_broke = sum(1 for t in closed if t["realized_pnl"] > 0 and not _is_adherent(t))
    lost_exec = sum(1 for t in closed if t["realized_pnl"] < 0 and _is_adherent(t))

    # A DECLARED no-edge trade is an honest record, not an unnamed bet — only
    # blank edges get flagged.
    no_edge = sum(1 for t in closed
                  if t.get("edge_id") is None and not t.get("no_edge"))
    if n == 0:
        headline = "No trades closed. Not trading is a position — patience compounds."
    elif n == len(adherent) and wins > 0:
        headline = "Every trade ran its playbook — system and discretionary alike. That's the win."
    elif n == len(adherent):
        headline = "Losses taken by the book. A well-executed loser is still a good trade."
    elif no_edge:
        headline = (f"{no_edge} trade{'s' if no_edge != 1 else ''} closed with no edge "
                    f"attached. If it's a real trade, name its edge; if it was an "
                    f"impulse, tag the mistake — either way, the record should say.")
    elif won_broke:
        headline = (f"{won_broke} winner{'s' if won_broke != 1 else ''} exited off-plan. "
                    f"The money counts; the process didn't. Note what actually happened.")
    else:
        headline = f"{len(adherent)}/{n} trades on-plan. Journal the rest against their edge."

    return {
        "trades_today": n,
        "system": system,
        "discretionary": n - system,
        "avg_conviction": avg_conv,
        "realized_pnl": pnl,
        "wins": wins,
        "current_streak": current_streak,
        "won_but_off_plan": won_broke,
        "lost_but_well_executed": lost_exec,
        "headline": headline,
    }


def compute_journal_analytics(trades: list[dict]) -> dict:
    """Analyze CLOSED trades (non-null realized_pnl)."""
    closed = [t for t in trades if t.get("realized_pnl") is not None]
    pnls = [t["realized_pnl"] for t in closed]

    by_edge = _grouped(
        closed,
        lambda t: t.get("edge_name") or ("(no edge — declared)" if t.get("no_edge") else None),
        "(unjournaled)")
    for row in by_edge:
        row["edge_name"] = row.pop("key")

    # by_mechanism = by auto-detected structure, labeled with the playbook name
    # when one exists (an unseeded direction shows its raw classifier name).
    by_mechanism = _grouped(
        closed,
        lambda t: t.get("mechanism_name") or t.get("direction"),
        "(unknown)")
    for row in by_mechanism:
        row["mechanism_name"] = row.pop("key")

    system = [t["realized_pnl"] for t in closed if t.get("is_system")]
    discretionary = [t["realized_pnl"] for t in closed if not t.get("is_system")]

    # Discipline streak — order chronologically by exit.
    ordered = sorted(closed, key=lambda t: (t.get("exit_at") or "", t.get("id") or 0))
    longest = streak = 0
    for t in ordered:
        if _is_adherent(t):
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
    current = 0
    for t in reversed(ordered):
        if _is_adherent(t):
            current += 1
        else:
            break

    won_but_off_plan = sum(1 for t in closed if t["realized_pnl"] > 0 and not _is_adherent(t))
    lost_but_executed = sum(1 for t in closed if t["realized_pnl"] < 0 and _is_adherent(t))

    # by_tag: a trade may carry several tags — it counts once per tag it carries.
    tag_buckets: dict[str, list[float]] = defaultdict(list)
    tag_colors: dict[str, str] = {}
    for t in closed:
        for tag in (t.get("tags") or []):
            label = tag["name"]
            tag_buckets[label].append(t["realized_pnl"])
            tag_colors[label] = tag.get("color")
    by_tag = [{"tag": k, "color": tag_colors[k], **_stats(v)}
              for k, v in tag_buckets.items()]
    by_tag.sort(key=lambda r: r["total_pnl"], reverse=True)

    return {
        "overall": _stats(pnls),
        "r_multiples": compute_r_stats(closed),
        "by_tag": by_tag,
        "by_edge": by_edge,
        "by_mechanism": by_mechanism,
        "system_vs_discretionary": {
            "system": _stats(system),
            "discretionary": _stats(discretionary),
        },
        "by_instrument": _grouped(closed, lambda t: t.get("underlying"), "(unknown)"),
        "by_regime": _grouped(closed, lambda t: t.get("regime_read"), "(unspecified)"),
        "discipline": {
            "current_streak": current,
            "longest_streak": longest,
            # journaling streak: consecutive most-recent closed trades with a
            # complete record (edge + risk + exit reason). Feeds the dashboard.
            "journal_streak": next(
                (i for i, t in enumerate(reversed(ordered)) if not _is_journaled(t)),
                len(ordered)),
            "dissonance": {
                "won_but_off_plan": won_but_off_plan,
                "lost_but_well_executed": lost_but_executed,
            },
        },
    }
