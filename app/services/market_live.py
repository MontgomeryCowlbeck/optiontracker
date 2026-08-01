"""Live market read for the Morning digest's Refresh — latest prices plus a
code-computed sentiment read.

The digest itself stays stored-only (fast pre-market paint); THIS module is the
opt-in live layer behind the Refresh button. yfinance is allowed here — this is
decision-support on the discretionary surface, not the Strategy #1 signal path.

HONESTY CONTRACT (same as pulse/DD): code computes ALL numbers. "Sentiment"
here is not an LLM vibe — it is a fixed set of observable gauges (VIX level and
1-day change, VIX/VIX3M term structure, index breadth, haven bid) each scored
on [-1, +1], averaged into a composite with a plain-English label. The
components are always returned alongside the composite so the label can never
say more than its inputs. Missing inputs drop out of the average rather than
defaulting.

Prices: one batched minute-bar download with prepost=True (a Morning tool must
see pre-market tape), backed by a daily download for previous closes. Cached
60s so repeated taps don't hammer yfinance.
"""
import asyncio
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EQUITY_TICKERS = ["SPY", "QQQ", "DIA", "IWM"]
HAVEN_TICKERS = ["TLT", "GLD"]
VOL_TICKERS = ["^VIX", "^VIX3M"]
ALL_TICKERS = EQUITY_TICKERS + HAVEN_TICKERS + VOL_TICKERS

_CACHE_TTL_S = 60
_cache: dict[str, tuple[float, dict]] = {}

_ET = ZoneInfo("America/New_York")


def _round2(x, nd: int = 2):
    return None if x is None else round(float(x), nd)


def market_session(now_utc: datetime) -> str:
    """pre-market / regular / after-hours / closed, from the ET wall clock."""
    et = now_utc.astimezone(_ET)
    if et.weekday() >= 5:
        return "closed"
    minutes = et.hour * 60 + et.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "pre-market"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "after-hours"
    return "closed"


def _score_vix_level(vix):
    if vix is None:
        return None
    if vix < 14:
        return 1.0, "complacent"
    if vix < 20:
        return 0.5, "calm"
    if vix < 28:
        return -0.5, "elevated"
    return -1.0, "fear"


def _score_vix_change(chg_pct):
    if chg_pct is None:
        return None
    if chg_pct <= -5:
        return 0.5, "bleeding off"
    if chg_pct < 5:
        return 0.0, "steady"
    if chg_pct < 15:
        return -0.5, "bid"
    return -1.0, "spiking"


def _score_term_structure(ratio):
    """VIX / VIX3M. Contango (near < far) is the calm norm; inversion is the
    classic stress tell."""
    if ratio is None:
        return None
    if ratio < 0.90:
        return 1.0, "steep contango"
    if ratio < 1.00:
        return 0.5, "contango"
    if ratio < 1.03:
        return -0.5, "flattening"
    return -1.0, "backwardation"


def _score_breadth(up: int, n: int):
    if n == 0:
        return None
    frac = up / n
    if frac >= 1.0:
        return 1.0, "broad risk-on"
    if frac >= 0.75:
        return 0.5, "leaning up"
    if frac > 0.25:
        return 0.0, "mixed"
    if frac > 0.0:
        return -0.5, "leaning down"
    return -1.0, "broad risk-off"


def _score_haven_bid(tlt_d1, gld_d1, equity_median_d1):
    """The full flight-to-safety read needs magnitude on both sides — havens
    up a little on a barely-red tape is a bid, not a flight."""
    if tlt_d1 is None or gld_d1 is None or equity_median_d1 is None:
        return None
    if tlt_d1 > 0.3 and gld_d1 > 0.3 and equity_median_d1 < -0.2:
        return -1.0, "flight to safety"
    if tlt_d1 > 0 and gld_d1 > 0 and equity_median_d1 < 0:
        return -0.5, "haven bid"
    if tlt_d1 < 0 and gld_d1 < 0 and equity_median_d1 > 0:
        return 0.5, "havens sold"
    return 0.0, "no haven signal"


def _composite_label(score: float) -> str:
    if score >= 0.5:
        return "risk-on"
    if score >= 0.15:
        return "leaning risk-on"
    if score > -0.15:
        return "neutral"
    if score > -0.5:
        return "cautious"
    return "risk-off"


def sentiment_read(quotes: dict[str, dict]) -> dict:
    """Pure. quotes = {sym: {last, prev_close, d1_pct}}; returns the component
    gauges + composite. Every number in here is derivable from the inputs."""
    vix = (quotes.get("^VIX") or {}).get("last")
    vix_chg = (quotes.get("^VIX") or {}).get("d1_pct")
    vix3m = (quotes.get("^VIX3M") or {}).get("last")
    ratio = (vix / vix3m) if (vix and vix3m) else None

    eq_moves = [(quotes.get(t) or {}).get("d1_pct") for t in EQUITY_TICKERS]
    eq_moves = [m for m in eq_moves if m is not None]
    eq_up = sum(1 for m in eq_moves if m > 0)
    eq_median = sorted(eq_moves)[len(eq_moves) // 2] if eq_moves else None

    components = []

    def _add(key, label, scored, value, detail):
        if scored is None:
            return
        score, read = scored
        components.append({"key": key, "label": label, "value": value,
                           "read": read, "detail": detail, "score": score})

    _add("vix_level", "VIX", _score_vix_level(vix), _round2(vix),
         "complacent <14 · calm <20 · elevated <28 · fear 28+")
    _add("vix_change", "VIX 1d", _score_vix_change(vix_chg),
         _round2(vix_chg), "% vs prior close")
    _add("term_structure", "VIX/VIX3M", _score_term_structure(ratio),
         _round2(ratio, 3), "near/far — >1 is the stress inversion")
    _add("breadth", "Index breadth",
         _score_breadth(eq_up, len(eq_moves)) if eq_moves else None,
         f"{eq_up}/{len(eq_moves)} up",
         "SPY QQQ DIA IWM on the day")
    _add("haven_bid", "Haven bid",
         _score_haven_bid(
             (quotes.get("TLT") or {}).get("d1_pct"),
             (quotes.get("GLD") or {}).get("d1_pct"),
             eq_median),
         None, "TLT+GLD vs equity tape")

    if not components:
        return {"score": None, "label": "unknown", "components": []}
    score = sum(c["score"] for c in components) / len(components)
    return {"score": round(score, 2), "label": _composite_label(score),
            "components": components}


def _fetch_quotes_blocking() -> dict[str, dict]:
    """{sym: {last, prev_close, d1_pct, as_of}} from two batched downloads:
    minute bars (prepost — the Morning use case IS pre-market) for the latest
    print, daily bars for the reference previous close."""
    import pandas as pd
    import yfinance as yf

    daily = yf.download(ALL_TICKERS, period="10d", interval="1d",
                        auto_adjust=False, progress=False, group_by="ticker")
    minute = yf.download(ALL_TICKERS, period="1d", interval="1m",
                         prepost=True, auto_adjust=False, progress=False,
                         group_by="ticker")

    out: dict[str, dict] = {}
    for sym in ALL_TICKERS:
        last = prev_close = as_of = None
        try:
            dcl = daily[sym]["Close"].dropna()
        except (KeyError, TypeError):
            dcl = pd.Series(dtype=float)
        try:
            mcl = minute[sym]["Close"].dropna()
        except (KeyError, TypeError):
            mcl = pd.Series(dtype=float)

        if len(mcl):
            last = float(mcl.iloc[-1])
            as_of = mcl.index[-1].isoformat()
            # Previous close = the last daily close from BEFORE the minute
            # tape's session day, so d1 means "today vs yesterday" even while
            # today's daily bar is still forming.
            session_day = mcl.index[-1].date()
            before = dcl[[d.date() < session_day for d in dcl.index]]
            if len(before):
                prev_close = float(before.iloc[-1])
        elif len(dcl):
            last = float(dcl.iloc[-1])
            as_of = str(dcl.index[-1].date())
            if len(dcl) > 1:
                prev_close = float(dcl.iloc[-2])

        d1 = ((last / prev_close - 1) * 100) if (last and prev_close) else None
        out[sym] = {"last": _round2(last), "prev_close": _round2(prev_close),
                    "d1_pct": _round2(d1), "as_of": as_of}
    return out


async def live_market() -> dict:
    """The /journal/digest/live payload. 60s cache — repeated Refresh taps
    reuse the last pull instead of re-downloading."""
    hit = _cache.get("live")
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return hit[1]

    quotes = await asyncio.to_thread(_fetch_quotes_blocking)
    now = datetime.now(timezone.utc)
    payload = {
        "as_of": now.isoformat(timespec="seconds"),
        "session": market_session(now),
        "quotes": quotes,
        "sentiment": sentiment_read(quotes),
    }
    _cache["live"] = (time.monotonic(), payload)
    return payload
