"""Strangle screener — decision support for short strangles on large-cap ETFs.

HONESTY CONTRACT (from the quant platform's ledger, 2026-07-20): the VRP-z
strangle candidate FAILED its one holdout shot (train +$104/trade → holdout
−$77/trade; e_strat2_strangle_holdout_capstone_20260719) and the 2026 period
is epistemically burned for that program. So this screener claims NO validated
entry edge. What it shows is CONDITIONS — how rich implied vol is versus
recent realized (the VRP-z framing that looked good in train and did not
survive out-of-sample) — plus live construction math. The UI must carry that
label; this module just computes.

Data: yfinance (discretionary decision-support path — the yfinance ban is
live-Strategy-#1 only). IV proxy = each ETF's CBOE vol index (VIX/VXN/VXD/RVX)
so a 252-day VRP history exists without an options-IV archive; RV20 =
close-close annualized. Both label their method. 10-min cache."""
import asyncio
import logging
import math
import time
from datetime import date

logger = logging.getLogger(__name__)

UNIVERSE = [
    {"symbol": "SPY", "vol_index": "^VIX"},
    {"symbol": "QQQ", "vol_index": "^VXN"},
    {"symbol": "DIA", "vol_index": "^VXD"},
    {"symbol": "IWM", "vol_index": "^RVX"},  # delisted on yahoo — falls back to chain IV, no z
]
Z_WINDOW = 252          # trailing window for the VRP z-score
Z_MIN = 60              # minimum history before a z is quoted
RICH_Z = 0.5            # the train-period threshold — UNVALIDATED, label it
DTE_LO, DTE_HI = 30, 45

_CACHE_TTL_S = 600
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return hit[1]
    return None


def _store(key: str, value) -> None:
    _cache[key] = (time.monotonic(), value)


def vrp_series(closes, vol_index_closes):
    """Ex-ante VRP series: vol-index/100 minus trailing 20d close-close RV,
    both known at t. Returns (vrp, rv20) aligned pandas Series."""
    import numpy as np

    logret = np.log(closes / closes.shift(1))
    rv20 = logret.rolling(20).std() * math.sqrt(252)
    iv = vol_index_closes / 100.0
    vrp = (iv - rv20).dropna()
    return vrp, rv20


def zscore_last(series, window: int = Z_WINDOW, min_n: int = Z_MIN):
    s = series.dropna()
    if len(s) < min_n:
        return None
    tail = s.tail(window)
    sd = tail.std()
    if not sd or math.isnan(sd) or sd == 0:
        return None
    return round(float((s.iloc[-1] - tail.mean()) / sd), 2)


def _nearest_by_delta(chain, target: float):
    """Row whose |delta| is nearest target. yfinance chains lack greeks, so
    delta here is Black-Scholes from the row's own IV — approximate, labeled."""
    best, best_err = None, 1e9
    for row in chain:
        d = row.get("delta_est")
        if d is None:
            continue
        err = abs(abs(d) - target)
        if err < best_err:
            best, best_err = row, err
    return best


def _bs_delta(spot: float, strike: float, t_years: float, iv: float, kind: str):
    """Plain BS delta — screener-grade only (European, r=q=0). The pricing
    engine's American greeks are the research standard; this is a UI estimate
    on live yfinance rows and is labeled as such."""
    if not (spot and strike and t_years and iv) or iv <= 0 or t_years <= 0:
        return None
    try:
        d1 = (math.log(spot / strike) + 0.5 * iv * iv * t_years) / (iv * math.sqrt(t_years))
        nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
        return nd1 if kind == "call" else nd1 - 1
    except (ValueError, ZeroDivisionError):
        return None


def _screen_one_blocking(symbol: str, vol_index: str) -> dict:
    import yfinance as yf

    def _daily(t):
        h = yf.Ticker(t).history(period="2y", interval="1d", auto_adjust=False)
        if h is not None and len(h):
            # yahoo returns tz-aware exchange-local stamps (NY for the ETF,
            # Chicago for CBOE indexes) — align on the DATE or reindex matches 0 rows
            h.index = h.index.tz_localize(None).normalize()
        return h

    tk = yf.Ticker(symbol)
    hist = _daily(symbol)
    if hist is None or len(hist) < Z_MIN + 21:
        raise ValueError(f"not enough history for {symbol}")
    spot = float(hist["Close"].dropna().iloc[-1])
    closes = hist["Close"]

    vixh = _daily(vol_index)
    have_index = vixh is not None and len(vixh) > 0
    iv_now = float(vixh["Close"].dropna().iloc[-1]) / 100.0 if have_index else None
    vrp = vrp_z = rv_now = None
    iv_source = vol_index if have_index else "chain_atm"
    ivr = None
    if have_index:
        vix_aligned = vixh["Close"].reindex(closes.index).ffill()
        vrp_s, rv_s = vrp_series(closes, vix_aligned)
        rv_now = round(float(rv_s.dropna().iloc[-1]), 4) if len(rv_s.dropna()) else None
        vrp = round(float(vrp_s.iloc[-1]), 4) if len(vrp_s) else None
        vrp_z = zscore_last(vrp_s)
        s252 = vixh["Close"].dropna().tail(252)
        if len(s252) >= Z_MIN and float(s252.max()) > float(s252.min()):
            ivr = round((float(s252.iloc[-1]) - float(s252.min()))
                        / (float(s252.max()) - float(s252.min())) * 100, 1)
    else:
        import numpy as np
        logret = np.log(closes / closes.shift(1))
        rv = logret.rolling(20).std() * math.sqrt(252)
        rv_now = round(float(rv.dropna().iloc[-1]), 4) if len(rv.dropna()) else None

    # Live chain: nearest expiry inside the 30-45 DTE window (else nearest to 38).
    expiry, dte = None, None
    put_rows, call_rows = [], []
    try:
        today = date.today()
        cands = []
        for e in tk.options or []:
            d = (date.fromisoformat(e) - today).days
            if d > 7:
                cands.append((abs(d - 38), d, e))
        if cands:
            in_win = [c for c in cands if DTE_LO <= c[1] <= DTE_HI]
            _, dte, expiry = min(in_win or cands)
        if expiry:
            ch = tk.option_chain(expiry)
            t_years = dte / 365.0
            for df, kind, out in ((ch.puts, "put", put_rows), (ch.calls, "call", call_rows)):
                for _, r in df.iterrows():
                    strike = float(r["strike"])
                    bid, ask = float(r.get("bid") or 0), float(r.get("ask") or 0)
                    mid = round((bid + ask) / 2, 3) if bid and ask else (float(r.get("lastPrice") or 0) or None)
                    iv_row = float(r.get("impliedVolatility") or 0) or None
                    out.append({
                        "strike": strike, "mid": mid, "iv": iv_row,
                        "delta_est": _bs_delta(spot, strike, t_years, iv_row, kind),
                        "oi": int(r.get("openInterest") or 0),
                    })
    except Exception:
        logger.warning("chain fetch failed for %s", symbol, exc_info=True)

    def construction(put_d: float, call_d: float, label: str):
        p = _nearest_by_delta(put_rows, put_d)
        c = _nearest_by_delta(call_rows, call_d)
        if not p or not c:
            return None
        credit = round(((p["mid"] or 0) + (c["mid"] or 0)) * 100, 2)
        cps = credit / 100.0
        return {
            "label": label,
            "put_strike": p["strike"], "put_delta": round(abs(p["delta_est"]), 2),
            "put_mid": p["mid"], "call_strike": c["strike"],
            "call_delta": round(abs(c["delta_est"]), 2), "call_mid": c["mid"],
            "credit": credit,
            "be_low": round(p["strike"] - cps, 2),
            "be_high": round(c["strike"] + cps, 2),
        }

    if iv_now is None:
        # no vol index (e.g. IWM) — chain ATM IV for the display row; no z history
        atm = [r["iv"] for r in put_rows
               if r["iv"] and 0.97 <= (r["strike"] / spot) <= 1.03]
        if atm:
            atm.sort()
            iv_now = round(atm[len(atm) // 2], 4)
            if rv_now:
                vrp = round(iv_now - rv_now, 4)

    expected_move = (round(spot * iv_now * math.sqrt(dte / 365.0), 2)
                     if iv_now and dte else None)
    return {
        "symbol": symbol, "spot": round(spot, 2),
        "vol_index": iv_source, "iv": round(iv_now, 4) if iv_now else None,
        "ivr": ivr, "rv20": rv_now, "vrp": vrp, "vrp_z": vrp_z,
        "gap_ratio": round(iv_now / rv_now, 2) if iv_now and rv_now else None,
        "rich": bool(vrp_z is not None and vrp_z >= RICH_Z),
        "expiry": expiry, "dte": dte, "expected_move": expected_move,
        "constructions": [c for c in (
            construction(0.16, 0.16, "TT classic 16Δ/16Δ"),
            construction(0.25, 0.06, "asymmetric 25Δp/6Δc (train candidate)"),
        ) if c],
    }


# ---------------------------------------------------------------------------
# Vol watch — marked symbols tracked for juicy premium + IV-crush forward tests
# ---------------------------------------------------------------------------

VOL_INDEX_BY_SYMBOL = {u["symbol"]: u["vol_index"] for u in UNIVERSE}


def _atm_iv_from_chain(tk, spot: float, expiry: str):
    """Median IV of puts within 3% of spot for one expiry; None if thin.
    Also returns the ATM straddle mid as % of spot (the market's expected
    move to that expiry — the number IV-crush trades are priced off)."""
    try:
        ch = tk.option_chain(expiry)
    except Exception:
        return None, None
    ivs, straddle = [], None
    best_gap = 1e9

    def _mid(row):
        bid, ask = float(row.get("bid") or 0), float(row.get("ask") or 0)
        return (bid + ask) / 2 if bid and ask else (float(row.get("lastPrice") or 0) or None)

    puts = {float(r["strike"]): r for _, r in ch.puts.iterrows()}
    calls = {float(r["strike"]): r for _, r in ch.calls.iterrows()}
    for k, r in puts.items():
        if abs(k / spot - 1) <= 0.03:
            iv = float(r.get("impliedVolatility") or 0)
            if iv:
                ivs.append(iv)
        gap = abs(k - spot)
        if gap < best_gap and k in calls:
            pm, cm = _mid(r), _mid(calls[k])
            if pm and cm:
                best_gap, straddle = gap, round((pm + cm) / spot * 100, 2)
    ivs.sort()
    return (round(ivs[len(ivs) // 2], 4) if ivs else None), straddle


def _vol_metrics_blocking(symbol: str) -> dict:
    """One symbol's live vol picture: spot, RV20, front + 30-45d ATM IV,
    term-structure ratio, front straddle %, next earnings. yfinance-sourced,
    screener-grade — the nightly snapshot of these fields builds the history
    that IVR and crush review read."""
    import numpy as np
    import yfinance as yf

    tk = yf.Ticker(symbol)
    hist = tk.history(period="1y", interval="1d", auto_adjust=False)
    if hist is None or len(hist) < 30:
        raise ValueError(f"not enough history for {symbol}")
    closes = hist["Close"].dropna()
    spot = float(closes.iloc[-1])
    logret = np.log(closes / closes.shift(1))
    rv_s = logret.rolling(20).std() * math.sqrt(252)
    rv20 = round(float(rv_s.dropna().iloc[-1]), 4) if len(rv_s.dropna()) else None

    today = date.today()
    expiries = [(e, (date.fromisoformat(e) - today).days) for e in (tk.options or [])]
    front = next(((e, d) for e, d in expiries if d >= 5), None)
    mid = min(((e, d) for e, d in expiries if DTE_LO <= d <= DTE_HI),
              key=lambda x: abs(x[1] - 38), default=None)
    back = next(((e, d) for e, d in expiries if d > (front[1] if front else 0) + 20), None)

    iv_front = straddle_pct = iv_mid = iv_back = None
    if front:
        iv_front, straddle_pct = _atm_iv_from_chain(tk, spot, front[0])
    if mid and (not front or mid[0] != front[0]):
        iv_mid, _ = _atm_iv_from_chain(tk, spot, mid[0])
    elif front:
        iv_mid = iv_front
    if back and back[0] not in {x[0] for x in (front, mid) if x}:
        iv_back, _ = _atm_iv_from_chain(tk, spot, back[0])
    elif mid and front and mid[0] != front[0]:
        iv_back = iv_mid

    iv = iv_mid or iv_front  # headline IV: the 30-45d band when available
    term_ratio = (round(iv_front / iv_back, 2)
                  if iv_front and iv_back else None)

    earnings = None
    try:
        cal = tk.calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if dates:
            nxt = min(d for d in dates if d >= today)
            earnings = str(nxt)
    except Exception:
        pass

    # IVR for vol-index names comes from a full 252d index history right away.
    ivr = None
    vol_index = VOL_INDEX_BY_SYMBOL.get(symbol)
    if vol_index:
        vixh = yf.Ticker(vol_index).history(period="1y", interval="1d")
        s = vixh["Close"].dropna() / 100.0 if vixh is not None and len(vixh) else None
        if s is not None and len(s) >= Z_MIN:
            lo, hi = float(s.min()), float(s.max())
            cur = float(s.iloc[-1])
            if hi > lo:
                ivr = round((cur - lo) / (hi - lo) * 100, 1)

    return {
        "symbol": symbol, "spot": round(spot, 2), "rv20": rv20,
        "iv": iv, "iv_front": iv_front, "iv_back": iv_back,
        "front_expiry": front[0] if front else None,
        "front_dte": front[1] if front else None,
        "iv_rv": round(iv / rv20, 2) if iv and rv20 else None,
        "term_ratio": term_ratio,
        "straddle_pct": straddle_pct,
        "earnings": earnings,
        "ivr": ivr,  # None for non-index names until snapshots accumulate
        "ivr_source": vol_index or "snapshots",
    }


async def vol_metrics(symbol: str) -> dict:
    key = f"volwatch:{symbol}"
    hit = _cached(key)
    if hit is not None:
        return hit
    row = await asyncio.to_thread(_vol_metrics_blocking, symbol)
    _store(key, row)
    return row


def ivr_from_snapshots(iv_now, snap_ivs: list) -> tuple:
    """(ivr, n) from our own accumulated snapshot history — the honest path
    for names without a CBOE index. Quoted only once >= 60 days exist."""
    ivs = [v for v in snap_ivs if v is not None]
    if iv_now is None or len(ivs) < Z_MIN:
        return None, len(ivs)
    lo, hi = min(ivs), max(ivs)
    if hi <= lo:
        return None, len(ivs)
    return round((iv_now - lo) / (hi - lo) * 100, 1), len(ivs)


async def snapshot_symbol(account_id: int, symbol: str) -> dict:
    """Fetch live vol metrics and upsert today's row into vol_snapshots —
    the accumulating record that IVR-without-an-index and IV-crush review run on."""
    from app.database import get_db

    m = await vol_metrics(symbol)
    async with get_db() as db:
        await db.execute(
            """INSERT INTO vol_snapshots
               (account_id, symbol, date, spot, iv, rv20, iv_rv, term_ratio,
                straddle_pct, earnings)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, symbol, date) DO UPDATE SET
                 spot=excluded.spot, iv=excluded.iv, rv20=excluded.rv20,
                 iv_rv=excluded.iv_rv, term_ratio=excluded.term_ratio,
                 straddle_pct=excluded.straddle_pct, earnings=excluded.earnings""",
            (account_id, symbol, date.today().isoformat(), m.get("spot"),
             m.get("iv"), m.get("rv20"), m.get("iv_rv"), m.get("term_ratio"),
             m.get("straddle_pct"), m.get("earnings")),
        )
        await db.commit()
    return m


async def snapshot_all(account_id: int) -> dict:
    """Nightly sweep of the vol watchlist; one bad name never kills the rest."""
    from app.database import fetch_all

    rows = await fetch_all(
        "SELECT symbol FROM vol_watchlist WHERE account_id = ?", (account_id,))
    ok, failed = 0, []
    for r in rows:
        try:
            await snapshot_symbol(account_id, r["symbol"])
            ok += 1
        except Exception as e:
            logger.warning("vol snapshot %s failed: %s", r["symbol"], e)
            failed.append(r["symbol"])
    return {"snapshotted": ok, "failed": failed}


async def screener() -> dict:
    """All universe names, concurrently, each 10-min cached; a bad name
    reports its error instead of killing the sweep."""
    async def one(u):
        key = f"strangle:{u['symbol']}"
        hit = _cached(key)
        if hit is not None:
            return hit
        try:
            row = await asyncio.to_thread(_screen_one_blocking, u["symbol"], u["vol_index"])
        except Exception as e:
            logger.warning("strangle screen %s failed: %s", u["symbol"], e)
            row = {"symbol": u["symbol"], "error": str(e)}
        _store(key, row)
        return row

    rows = await asyncio.gather(*(one(u) for u in UNIVERSE))
    return {
        "as_of": date.today().isoformat(),
        "rich_threshold": RICH_Z,
        "rows": list(rows),
    }
