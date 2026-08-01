"""Deep-dive (DD) panel — full single-name breakdown behind a signal card.

Reuses the watchtower machinery (level detection, RSI, ATM IV, earnings) from
the quant platform source tree, read-only. yfinance is allowed here: this is
the discretionary sleeve, not the Strategy #1 live path.

Split on purpose: `build_dd` is pure (testable, no network); `fetch_dd` does
the yfinance IO in a thread. The AI brief hands the LLM ONLY code-computed
numbers — it adds catalyst/context/chart-reading prose, never arithmetic and
never a buy/sell call (the watchtower brief contract).
"""
import asyncio
import math
import sys
import time

from app.config import get_settings

# Short TTL cache for DD payloads and candidate-put tables so a multi-turn
# consult converses instead of re-sweeping yfinance on every message. 10 min:
# fresh enough intraday, and a conversation comfortably fits inside it.
_CACHE_TTL_S = 600
_cache: dict[tuple[str, str], tuple[float, object]] = {}


def _cached(kind: str, symbol: str):
    hit = _cache.get((kind, symbol))
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return hit[1]
    return None


def _store(kind: str, symbol: str, value) -> None:
    _cache[(kind, symbol)] = (time.monotonic(), value)


def _quant_src_on_path() -> bool:
    d = get_settings().quant_src_dir
    if not d:
        return False
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        import watchtower.levels  # noqa: F401
        return True
    except ImportError:
        return False


def dd_available() -> bool:
    return _quant_src_on_path()


def _round2(x, nd: int = 2):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)


def _zone_dict(z, price: float) -> dict:
    return {
        "kind": z.kind,
        "lo": z.lo,
        "hi": z.hi,
        "touches": z.touches,
        "strength": z.strength,
        "last_touch": str(z.last_touch.date()),
        "volume_node": z.volume_node,
        "round_number": z.round_number,
        "distance_pct": _round2(z.distance_pct(price)),
    }


def build_dd(symbol: str, df, atm_iv=None, iv_dte=None, earnings=None) -> dict:
    """Pure DD payload from a daily OHLCV frame (columns Open/High/Low/Close/
    Volume, DatetimeIndex ascending). Everything numeric computed here."""
    import numpy as np
    from watchtower.levels import atr, find_zones, nearest_zones
    from watchtower.scan import rsi

    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    close = df["Close"]
    price = float(close.iloc[-1])

    zones = find_zones(df)
    supports = nearest_zones(zones, price, "support")
    resistances = nearest_zones(zones, price, "resistance", n=2)

    lo52 = float(df["Low"].tail(252).min())
    hi52 = float(df["High"].tail(252).max())
    logret = np.log(close / close.shift(1)).dropna()
    rv20 = (float(logret.tail(20).std() * np.sqrt(252))
            if len(logret) >= 20 else None)
    a = float(atr(df).iloc[-1])

    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()

    vol = df["Volume"]
    vol_avg20 = float(vol.tail(20).mean()) if len(vol) >= 20 else None
    vol_today = float(vol.iloc[-1]) if len(vol) else None
    vol_ratio = (round(vol_today / vol_avg20, 2)
                 if vol_today and vol_avg20 else None)

    # Code-computed regime label (no LLM): price vs the 200d says which side
    # of the mountain; the 50-vs-200 relation says which way the slope leans.
    regime = None
    if not math.isnan(sma200.iloc[-1]) and not math.isnan(sma50.iloc[-1]):
        above200 = price > sma200.iloc[-1]
        golden = sma50.iloc[-1] > sma200.iloc[-1]
        regime = ("uptrend" if above200 and golden
                  else "pullback-in-uptrend" if not above200 and golden
                  else "recovery" if above200 else "downtrend")
    # Full history (2y fetch) so the UI can offer timeframes that include the
    # whole zone-detection window — a 250d chart under 400d zones misleads.
    series = [
        {"d": str(idx.date()), "c": _round2(row),
         "sma50": _round2(sma50.loc[idx]), "sma200": _round2(sma200.loc[idx])}
        for idx, row in close.items()
    ]

    def _ret(n: int):
        return (_round2((price / float(close.iloc[-n - 1]) - 1) * 100)
                if len(close) > n else None)

    iv_rv = (_round2(atm_iv / rv20) if (atm_iv and rv20) else None)

    return {
        "symbol": symbol,
        "price": _round2(price),
        "as_of": str(df.index[-1].date()),
        "stats": {
            "rsi14": _round2(rsi(close)),
            "ret_5d": _ret(5),
            "ret_20d": _ret(20),
            "lo52": _round2(lo52),
            "hi52": _round2(hi52),
            "pct_from_52w_low": _round2((price - lo52) / lo52 * 100),
            "pct_from_52w_high": _round2((price - hi52) / hi52 * 100),
            "atr14": _round2(a),
            "atr_pct": _round2(a / price * 100),
            "rv20": _round2(rv20, 4),
            "atm_iv": _round2(atm_iv, 4),
            "iv_dte": iv_dte,
            "iv_rv_ratio": iv_rv,
            "sma50": _round2(sma50.iloc[-1]),
            "sma200": _round2(sma200.iloc[-1]),
            "above_sma200": (bool(price > sma200.iloc[-1])
                             if not math.isnan(sma200.iloc[-1]) else None),
            "regime": regime,
            "volume_today": vol_today,
            "volume_avg20": vol_avg20,
            "volume_ratio": vol_ratio,
            "earnings": earnings,
        },
        "supports": [_zone_dict(z, price) for z in supports],
        "resistances": [_zone_dict(z, price) for z in resistances],
        # find_zones looks back this many trading days — the UI labels it so
        # the chart timeframe is never mistaken for the zone-detection window.
        "zone_lookback_days": 400,
        "series": series,
    }


def _fetch_blocking(symbol: str) -> dict:
    import yfinance as yf
    from watchtower.scan import atm_iv_30_45, next_earnings

    tk = yf.Ticker(symbol)
    df = tk.history(period="2y", interval="1d", auto_adjust=False)
    if df is None or len(df) < 40:
        raise ValueError(f"not enough price history for {symbol}")
    price = float(df["Close"].dropna().iloc[-1])
    iv = atm_iv_30_45(tk, price)
    return build_dd(symbol, df,
                    atm_iv=iv[0] if iv else None,
                    iv_dte=iv[1] if iv else None,
                    earnings=next_earnings(tk))


def _next_earnings_blocking(symbol: str):
    import yfinance as yf
    from watchtower.scan import next_earnings
    return next_earnings(yf.Ticker(symbol))


async def fetch_next_earnings(symbols: list[str]) -> dict:
    """Next earnings date (iso string or None) per symbol, 10-min cached.
    A symbol that errors maps to None — this never raises; the forward
    calendar simply shows no earnings marker for it."""
    if not _quant_src_on_path():
        return {s.upper(): None for s in symbols}
    out = {}
    for symbol in symbols:
        sym = symbol.upper()
        hit = _cached("earn", sym)
        if hit is not None:
            out[sym] = hit or None  # "" caches a known-None result
            continue
        try:
            nxt = await asyncio.to_thread(_next_earnings_blocking, sym)
        except Exception:
            nxt = None
        _store("earn", sym, nxt or "")
        out[sym] = nxt
    return out


async def fetch_dd(symbol: str) -> dict:
    """The DD payload for one symbol (10-min cached). Raises ValueError on
    unknown/thin names."""
    if not _quant_src_on_path():
        raise RuntimeError("quant src tree not available on this host")
    sym = symbol.upper()
    hit = _cached("dd", sym)
    if hit is not None:
        return hit
    dd = await asyncio.to_thread(_fetch_blocking, sym)
    _store("dd", sym, dd)
    return dd


def _candidate_puts_blocking(symbol: str, max_rows: int = 8) -> list[dict]:
    """Put candidates for the consult agent: the 25-50 DTE expiry nearest 35,
    strikes from just under spot down to ~25% below, each with real chain
    numbers (mid, % of strike, OTM%, IV, OI). Code-computed only — the agent
    may pick FROM this table, never invent premium."""
    import datetime as _dt

    import yfinance as yf

    tk = yf.Ticker(symbol)
    hist = tk.history(period="5d", interval="1d")
    if hist is None or hist.empty:
        return []
    spot = float(hist["Close"].dropna().iloc[-1])
    try:
        expiries = tk.options
    except Exception:
        return []
    today = _dt.date.today()
    live = {e: (_dt.date.fromisoformat(e) - today).days for e in expiries
            if 25 <= (_dt.date.fromisoformat(e) - today).days <= 50}
    if not live:
        return []
    exp = min(live, key=lambda e: abs(live[e] - 35))
    try:
        puts = tk.option_chain(exp).puts
    except Exception:
        return []
    band = puts[(puts["strike"] <= spot * 1.01) & (puts["strike"] >= spot * 0.75)]
    band = band.sort_values("strike", ascending=False).head(max_rows)
    out = []
    for _, row in band.iterrows():
        bid = float(row.get("bid") or 0)
        ask = float(row.get("ask") or 0)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else float(row.get("lastPrice") or 0)
        if mid <= 0:
            continue
        k = float(row["strike"])
        iv = row.get("impliedVolatility")
        out.append({
            "expiry": exp, "dte": live[exp], "strike": k,
            "bid": bid, "ask": ask, "mid": round(mid, 2),
            "prem_pct_of_strike": round(mid / k * 100, 2),
            "otm_pct": round((spot - k) / spot * 100, 1),
            "iv": round(float(iv) * 100, 1) if iv and not math.isnan(float(iv)) else None,
            "oi": int(row.get("openInterest") or 0),
        })
    return out


async def fetch_candidate_puts(symbol: str) -> list[dict]:
    sym = symbol.upper()
    hit = _cached("puts", sym)
    if hit is not None:
        return hit
    puts = await asyncio.to_thread(_candidate_puts_blocking, sym)
    if puts:  # never cache a failed/empty chain read
        _store("puts", sym, puts)
    return puts


DD_BRIEF_PROMPT = """You are the research analyst inside a private options trade
journal. The trader sells put premium at defended support levels (cash-secured
puts, 25-50 DTE) and wants a full due-diligence read on ONE name.

Every number below was computed by code and is authoritative — never recompute,
never invent figures. Use WebSearch for catalysts, news, and sentiment; attribute
what you find. You are decision SUPPORT: never say buy/sell/enter/avoid — give
both sides and let the trader decide.

Write markdown (<= 450 words):
1. **Chart read** — trend and where price sits versus the SMAs, the 52-week
   range, and the detected support/resistance zones (strength = recency-weighted
   touches). Say plainly whether support below is defended or thin.
2. **What's driving it** — recent news/catalysts from search; earnings timing
   and its risk to a 25-50 DTE short put.
3. **Vol context** — what the RV/IV numbers say about how the market is pricing
   risk here (IV/RV ratio above ~1.3 = rich premium, if IV is present).
4. **Bull case / bear case** — two tight paragraphs, both sides argued honestly.
5. **The watchtower lens** — one paragraph: given the zone stats, RSI, and
   earnings timing, what would the desk's own framework flag as the strong and
   weak points of selling a put here? (Framework: strike below a >=2-touch
   unbroken zone, RSI<=35 washout, no earnings before expiry, premium >= 4% of
   strike near support / 8% without a defended level.)

DATA (code-computed):
{context}

JOURNAL HISTORY ON THIS NAME (this trader's own record):
{journal}"""
