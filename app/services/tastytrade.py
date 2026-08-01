"""
Tastytrade API client using OAuth2 authentication.

Auth:
  POST /oauth/token with client_id + client_secret + refresh_token → 15-min access token

Market data (REST, no WebSocket needed):
  GET /market-data/by-type?equity[]=SPY&equity-option[]=SPY...
  Max 100 symbols per call; all values returned as strings.

Option chains:
  GET /option-chains/{symbol}/nested  → expiration + strike structure
  (TT symbols in strikes, URL-encode when passing to market-data)
"""
import asyncio
import re
from typing import Optional
from datetime import datetime, timezone, timedelta
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_USER_AGENT = "optiontracker/1.0"
_MARKET_DATA_BATCH = 95  # Stay under the 100-symbol limit


# ---------------------------------------------------------------------------
# Symbol conversion helpers
# ---------------------------------------------------------------------------

def occ_to_tastytrade(occ: str) -> str:
    """Convert OCC symbol to Tastytrade format (ticker padded to 6 chars).

    AAPL240119C00150000 → 'AAPL  240119C00150000'
    """
    m = re.match(r'^([A-Z\.]{1,6})(\d{6}[CP]\d{8})$', occ)
    if not m:
        return occ
    return m.group(1).ljust(6) + m.group(2)


def tastytrade_to_occ(tt_symbol: str) -> str:
    """Convert Tastytrade symbol to OCC format.

    'AAPL  240119C00150000' → 'AAPL240119C00150000'
    """
    if len(tt_symbol) < 6:
        return tt_symbol
    return tt_symbol[:6].rstrip() + tt_symbol[6:]


def _f(value) -> Optional[float]:
    """Safely convert a string (or None) to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class TastytradeClient:
    """Tastytrade API client using OAuth2 refresh-token flow."""

    def __init__(self):
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._auth_lock = asyncio.Lock()

    # -----------------------------------------------------------------------
    # Auth
    # -----------------------------------------------------------------------

    def _is_configured(self) -> bool:
        if not settings.tastytrade_configured:
            logger.warning(
                "Tastytrade not configured — set TASTYTRADE_CLIENT_ID, "
                "TASTYTRADE_CLIENT_SECRET, TASTYTRADE_REFRESH_TOKEN"
            )
            return False
        return True

    def _token_needs_refresh(self) -> bool:
        if not self._access_token or not self._token_expiry:
            return True
        return datetime.now(timezone.utc) >= self._token_expiry - timedelta(minutes=2)

    async def _authenticate(self) -> bool:
        async with self._auth_lock:
            if not self._token_needs_refresh():
                return True
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{settings.tastytrade_base_url}/oauth/token",
                        json={
                            "grant_type": "refresh_token",
                            "client_id": settings.tastytrade_client_id,
                            "client_secret": settings.tastytrade_client_secret,
                            "refresh_token": settings.tastytrade_refresh_token,
                        },
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": _USER_AGENT,
                        },
                        timeout=30.0,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    self._access_token = data["access_token"]
                    expires_in = data.get("expires_in", 900)
                    self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                    logger.info("Tastytrade OAuth2 token obtained (expires in %ds)", expires_in)
                    return True
            except Exception as e:
                logger.error("Tastytrade OAuth2 auth failed: %s", e)
                self._access_token = None
                return False

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }

    async def _get(self, path: str, params=None) -> Optional[dict]:
        """Authenticated GET with automatic token refresh on 401."""
        if not self._is_configured():
            return None
        if self._token_needs_refresh():
            if not await self._authenticate():
                return None
        base = settings.tastytrade_base_url
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{base}{path}", headers=self._headers(), params=params, timeout=30.0)
                if resp.status_code == 401:
                    self._access_token = None
                    if not await self._authenticate():
                        return None
                    resp = await client.get(f"{base}{path}", headers=self._headers(), params=params, timeout=30.0)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Tastytrade HTTP %s for %s: %s", e.response.status_code, path, e.response.text[:200])
            return None
        except Exception as e:
            logger.error("Tastytrade request error for %s: %s", path, e)
            return None

    # -----------------------------------------------------------------------
    # Market data (batched REST, ≤95 symbols per call)
    # -----------------------------------------------------------------------

    async def _market_data(self, equities: list[str] = (), equity_options: list[str] = ()) -> dict[str, dict]:
        """
        Fetch market data via GET /market-data/by-type.
        Batches automatically to stay under the 100-symbol limit.
        Returns dict keyed by symbol (TT format for options, plain for equities).
        """
        results: dict[str, dict] = {}

        def batch(lst, size):
            for i in range(0, len(lst), size):
                yield lst[i:i + size]

        eq_list = list(equities)
        opt_list = list(equity_options)

        # Batch such that eq + opt per call ≤ _MARKET_DATA_BATCH
        async def fetch_batch(eqs, opts):
            params = [("equity[]", s) for s in eqs] + [("equity-option[]", s) for s in opts]
            data = await self._get("/market-data/by-type", params=params)
            if data:
                for item in data.get("data", {}).get("items", []):
                    sym = item.get("symbol", "")
                    if sym:
                        results[sym] = item

        # Simple strategy: send equities and options in separate batches
        for chunk in batch(eq_list, _MARKET_DATA_BATCH):
            await fetch_batch(chunk, [])

        for chunk in batch(opt_list, _MARKET_DATA_BATCH):
            await fetch_batch([], chunk)

        return results

    # -----------------------------------------------------------------------
    # Underlying (stock/ETF) quotes
    # -----------------------------------------------------------------------

    async def get_underlying_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch quotes for multiple stock/ETF symbols."""
        if not symbols:
            return {}
        raw = await self._market_data(equities=symbols)
        quotes = {}
        for sym, item in raw.items():
            last = _f(item.get("last"))
            prev = _f(item.get("prev-close"))
            change = None
            change_pct = None
            if last is not None and prev is not None and prev != 0:
                change = round(last - prev, 4)
                change_pct = round((change / prev) * 100, 4)

            quotes[sym] = {
                "symbol": sym,
                "description": item.get("description"),
                "last": last,
                "bid": _f(item.get("bid")),
                "ask": _f(item.get("ask")),
                "open": _f(item.get("open")),
                "high": _f(item.get("day-high-price")),
                "low": _f(item.get("day-low-price")),
                "close": _f(item.get("close")),
                "prevclose": prev,
                "change": change,
                "change_percentage": change_pct,
                "volume": _f(item.get("volume")),
                "exch": item.get("listed-market"),
            }
        return quotes

    async def get_underlying_quote(self, symbol: str) -> Optional[dict]:
        """Fetch quote for a single stock/ETF symbol."""
        quotes = await self.get_underlying_quotes([symbol])
        return quotes.get(symbol)

    # -----------------------------------------------------------------------
    # Option quotes
    # -----------------------------------------------------------------------

    async def get_option_quotes(self, symbols: list[str], include_greeks: bool = True) -> dict[str, dict]:
        """Fetch quotes for multiple OCC option symbols."""
        if not symbols:
            return {}

        tt_symbols = [occ_to_tastytrade(s) for s in symbols]
        occ_by_tt = {tt: occ for tt, occ in zip(tt_symbols, symbols)}

        raw = await self._market_data(equity_options=tt_symbols)

        quotes = {}
        for tt_sym, item in raw.items():
            occ_sym = occ_by_tt.get(tt_sym) or tastytrade_to_occ(tt_sym)

            entry: dict = {
                "symbol": occ_sym,
                "bid": _f(item.get("bid")),
                "ask": _f(item.get("ask")),
                "last": _f(item.get("last")),
                "volume": _f(item.get("volume")),
                "open_interest": item.get("open-interest"),
                "underlying": None,
                "strike": None,
                "expiration_date": None,
                "option_type": None,
            }

            if include_greeks:
                iv = _f(item.get("volatility"))
                delta = _f(item.get("delta"))
                gamma = _f(item.get("gamma"))
                theta = _f(item.get("theta"))
                vega = _f(item.get("vega"))
                rho = _f(item.get("rho"))

                if any(v is not None for v in [iv, delta, gamma, theta, vega, rho]):
                    entry["greeks"] = {
                        "delta": delta,
                        "gamma": gamma,
                        "theta": theta,
                        "vega": vega,
                        "rho": rho,
                        "mid_iv": iv,
                        "bid_iv": None,
                        "ask_iv": None,
                    }

            quotes[occ_sym] = entry

        return quotes

    # -----------------------------------------------------------------------
    # Option expirations
    # -----------------------------------------------------------------------

    async def get_option_expirations(self, symbol: str) -> list[str]:
        """Return sorted YYYY-MM-DD expiration dates for a symbol."""
        data = await self._get(f"/option-chains/{symbol}/nested")
        if not data:
            return []
        items = data.get("data", {}).get("items", [])
        dates = sorted({
            exp["expiration-date"]
            for chain in items
            for exp in chain.get("expirations", [])
            if exp.get("expiration-date")
        })
        logger.info("Found %d expirations for %s", len(dates), symbol)
        return dates

    # -----------------------------------------------------------------------
    # Option chain
    # -----------------------------------------------------------------------

    async def get_option_chain(self, symbol: str, expiration: str, include_greeks: bool = True) -> dict:
        """
        Full option chain for symbol + expiration.
        Returns {'calls': [...], 'puts': [...]} with strike, bid, ask, greeks.
        """
        data = await self._get(f"/option-chains/{symbol}/nested")
        if not data:
            return {"calls": [], "puts": []}

        call_tt: list[str] = []
        put_tt: list[str] = []
        strike_by_tt: dict[str, float] = {}

        for chain in data.get("data", {}).get("items", []):
            for exp in chain.get("expirations", []):
                if exp.get("expiration-date") != expiration:
                    continue
                for strike in exp.get("strikes", []):
                    sp = _f(strike.get("strike-price")) or 0.0
                    c = strike.get("call")
                    p = strike.get("put")
                    if c:
                        call_tt.append(c)
                        strike_by_tt[c] = sp
                    if p:
                        put_tt.append(p)
                        strike_by_tt[p] = sp

        if not call_tt and not put_tt:
            logger.warning("No options found for %s expiring %s", symbol, expiration)
            return {"calls": [], "puts": []}

        raw = await self._market_data(equity_options=call_tt + put_tt)

        def build(tt_sym: str, opt_type: str) -> dict:
            item = raw.get(tt_sym, {})
            entry: dict = {
                "symbol": tastytrade_to_occ(tt_sym),
                "strike": strike_by_tt.get(tt_sym, 0),
                "bid": _f(item.get("bid")),
                "ask": _f(item.get("ask")),
                "last": _f(item.get("last")),
                "volume": _f(item.get("volume")),
                "open_interest": item.get("open-interest"),
                "option_type": opt_type,
                "expiration_date": expiration,
            }
            if include_greeks and item:
                iv = _f(item.get("volatility"))
                delta = _f(item.get("delta"))
                gamma = _f(item.get("gamma"))
                theta = _f(item.get("theta"))
                vega = _f(item.get("vega"))
                rho = _f(item.get("rho"))
                if any(v is not None for v in [iv, delta, gamma, theta, vega, rho]):
                    entry["greeks"] = {
                        "delta": delta,
                        "gamma": gamma,
                        "theta": theta,
                        "vega": vega,
                        "rho": rho,
                        "mid_iv": iv,
                        "bid_iv": None,
                        "ask_iv": None,
                    }
            return entry

        calls = sorted([build(s, "call") for s in call_tt], key=lambda x: x["strike"])
        puts = sorted([build(s, "put") for s in put_tt], key=lambda x: x["strike"])
        return {"calls": calls, "puts": puts}

    # -----------------------------------------------------------------------
    # Account data (for trade sync)
    # -----------------------------------------------------------------------

    async def get_accounts(self) -> list[dict]:
        """Return list of Tastytrade accounts for the authenticated user."""
        data = await self._get("/customers/me/accounts")
        if not data:
            return []
        items = data.get("data", {}).get("items", [])
        return [item.get("account", item) for item in items]

    async def get_transactions(
        self,
        account_number: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        page_offset: int = 0,
    ) -> dict:
        """
        Fetch transaction history for an account.

        Returns dict with 'items' (list) and 'pagination' keys.
        Caller is responsible for paginating if pagination['total-items'] > len(items).
        """
        params: list[tuple] = [("per-page", "250"), ("page-offset", str(page_offset))]
        if start_date:
            params.append(("start-date", start_date))
        if end_date:
            params.append(("end-date", end_date))

        data = await self._get(f"/accounts/{account_number}/transactions", params=params)
        if not data:
            return {"items": [], "pagination": {}}
        return {
            "items": data.get("data", {}).get("items", []),
            "pagination": data.get("pagination", {}),
        }

    async def get_all_transactions(
        self,
        account_number: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """Fetch ALL transactions, paginating automatically."""
        all_items: list[dict] = []
        offset = 0
        while True:
            result = await self.get_transactions(account_number, start_date, end_date, offset)
            items = result["items"]
            all_items.extend(items)
            pagination = result.get("pagination", {})
            total = pagination.get("total-items", 0)
            if len(all_items) >= total or not items:
                break
            offset += len(items)
        return all_items

    async def get_positions(self, account_number: str) -> list[dict]:
        """Fetch current open positions for an account."""
        data = await self._get(f"/accounts/{account_number}/positions")
        if not data:
            return []
        return data.get("data", {}).get("items", [])

    async def get_balances(self, account_number: str) -> Optional[dict]:
        """Fetch cash and margin balances for an account."""
        data = await self._get(f"/accounts/{account_number}/balances")
        if not data:
            return None
        return data.get("data")

    # -----------------------------------------------------------------------
    # Symbol search
    # -----------------------------------------------------------------------

    async def search_symbols(self, query: str) -> list[dict]:
        """Search for stock/ETF symbols. Returns up to 15 results."""
        if not query:
            return []

        results: list[dict] = []
        seen: set[str] = set()

        def add(items: list) -> None:
            for item in items:
                sym = item.get("symbol")
                if not sym or sym in seen:
                    continue
                inst_type = item.get("instrument-type", "")
                if inst_type not in ("Equity", "ETF"):
                    continue
                seen.add(sym)
                results.append({
                    "symbol": sym,
                    "name": item.get("description", ""),
                    "type": "etf" if item.get("is-etf") else "stock",
                    "exchange": item.get("listed-market", ""),
                })

        # Exact symbol lookup
        d1 = await self._get("/instruments/equities", params=[("symbol[]", query)])
        if d1:
            add(d1.get("data", {}).get("items", []))

        # Fuzzy/name search
        d2 = await self._get("/instruments/equities/active", params=[("lfe-symbol", query), ("per-page", "15")])
        if d2:
            add(d2.get("data", {}).get("items", []))

        return results[:15]


    async def get_market_metrics(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch IV rank and related metrics for a list of underlying symbols.
        Returns dict keyed by symbol."""
        if not symbols:
            return {}
        data = await self._get("/market-metrics", params={"symbols": ",".join(symbols)})
        if not data:
            return {}
        return {
            item["symbol"]: item
            for item in data.get("data", {}).get("items", [])
            if item.get("symbol")
        }

    async def get_live_orders(self, account_number: str) -> list[dict]:
        """Return all working (Live) orders for an account."""
        data = await self._get(f"/accounts/{account_number}/orders/live")
        if not data:
            return []
        return data.get("data", {}).get("items", [])


# Singleton
tastytrade_client = TastytradeClient()
