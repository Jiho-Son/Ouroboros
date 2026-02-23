"""Async wrapper for the Korea Investment Securities (KIS) Open API.

Handles token refresh, rate limiting (leaky bucket), and hash key generation.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Any

import aiohttp

from src.config import Settings

# KIS virtual trading server has a known SSL certificate hostname mismatch.
_KIS_VTS_HOST = "openapivts.koreainvestment.com"

logger = logging.getLogger(__name__)


def kr_tick_unit(price: float) -> int:
    """Return KRX tick size for the given price level.

    KRX price tick rules (domestic stocks):
        price < 2,000        →   1원
        2,000  ≤ price < 5,000     →   5원
        5,000  ≤ price < 20,000    →  10원
        20,000 ≤ price < 50,000    →  50원
        50,000 ≤ price < 200,000   → 100원
        200,000 ≤ price < 500,000  → 500원
        500,000 ≤ price            → 1,000원
    """
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def kr_round_down(price: float) -> int:
    """Round *down* price to the nearest KRX tick unit."""
    tick = kr_tick_unit(price)
    return int(price // tick * tick)


class LeakyBucket:
    """Simple leaky-bucket rate limiter for async code."""

    def __init__(self, rate: float) -> None:
        """Args:
            rate: Maximum requests per second.
        """
        self._rate = rate
        self._interval = 1.0 / rate
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._last + self._interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = asyncio.get_event_loop().time()


class KISBroker:
    """Async client for KIS Open API with automatic token management."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.KIS_BASE_URL
        self._app_key = settings.KIS_APP_KEY
        self._app_secret = settings.KIS_APP_SECRET
        self._account_no = settings.account_number
        self._product_cd = settings.account_product_code

        self._session: aiohttp.ClientSession | None = None
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()
        self._last_refresh_attempt: float = 0.0
        self._refresh_cooldown: float = 60.0  # Seconds (matches KIS 1/minute limit)
        self._rate_limiter = LeakyBucket(settings.RATE_LIMIT_RPS)

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            connector: aiohttp.BaseConnector | None = None
            if _KIS_VTS_HOST in self._base_url:
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            self._session = aiohttp.ClientSession(
                timeout=timeout, connector=connector,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Token Management
    # ------------------------------------------------------------------

    async def _ensure_token(self) -> str:
        """Return a valid access token, refreshing if expired.

        Uses a lock to prevent concurrent token refresh attempts that would
        hit the API's 1-per-minute rate limit (EGW00133).
        """
        # Fast path: check without lock
        now = asyncio.get_event_loop().time()
        if self._access_token and now < self._token_expires_at:
            return self._access_token

        # Slow path: acquire lock and refresh
        async with self._token_lock:
            # Re-check after acquiring lock (another coroutine may have refreshed)
            now = asyncio.get_event_loop().time()
            if self._access_token and now < self._token_expires_at:
                return self._access_token

            # Check cooldown period (prevents hitting EGW00133: 1/minute limit)
            time_since_last_attempt = now - self._last_refresh_attempt
            if time_since_last_attempt < self._refresh_cooldown:
                remaining = self._refresh_cooldown - time_since_last_attempt
                # Do not fail fast here. If token is unavailable, upstream calls
                # will all fail for up to a minute and scanning returns no trades.
                logger.warning(
                    "Token refresh on cooldown. Waiting %.1fs before retry (KIS allows 1/minute)",
                    remaining,
                )
                await asyncio.sleep(remaining)
                now = asyncio.get_event_loop().time()

            logger.info("Refreshing KIS access token")
            self._last_refresh_attempt = now
            session = self._get_session()
            url = f"{self._base_url}/oauth2/tokenP"
            body = {
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            }

            async with session.post(url, json=body) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(f"Token refresh failed ({resp.status}): {text}")
                data = await resp.json()

            self._access_token = data["access_token"]
            self._token_expires_at = now + data.get("expires_in", 86400) - 60  # 1-min buffer
            logger.info("Token refreshed successfully")
            return self._access_token

    # ------------------------------------------------------------------
    # Hash Key (required for POST bodies)
    # ------------------------------------------------------------------

    async def _get_hash_key(self, body: dict[str, Any]) -> str:
        """Request a hash key from KIS for POST request body signing."""
        await self._rate_limiter.acquire()
        session = self._get_session()
        url = f"{self._base_url}/uapi/hashkey"
        headers = {
            "content-Type": "application/json",
            "appKey": self._app_key,
            "appSecret": self._app_secret,
        }

        async with session.post(url, json=body, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ConnectionError(f"Hash key request failed ({resp.status}): {text}")
            data = await resp.json()

        return data["HASH"]

    # ------------------------------------------------------------------
    # Common Headers
    # ------------------------------------------------------------------

    async def _auth_headers(self, tr_id: str) -> dict[str, str]:
        token = await self._ensure_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
        }

    # ------------------------------------------------------------------
    # API Methods
    # ------------------------------------------------------------------

    async def get_orderbook(self, stock_code: str) -> dict[str, Any]:
        """Fetch the current orderbook for a given stock code."""
        await self._rate_limiter.acquire()
        session = self._get_session()

        headers = await self._auth_headers("FHKST01010200")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }
        url = f"{self._base_url}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"get_orderbook failed ({resp.status}): {text}"
                    )
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching orderbook: {exc}") from exc

    async def get_current_price(
        self, stock_code: str
    ) -> tuple[float, float, float]:
        """Fetch current price data for a domestic stock.

        Uses the ``inquire-price`` API (FHKST01010100), which works in both
        real and VTS environments and returns the actual last-traded price.

        Returns:
            (current_price, prdy_ctrt, frgn_ntby_qty)
            - current_price: Last traded price in KRW.
            - prdy_ctrt: Day change rate (%).
            - frgn_ntby_qty: Foreigner net buy quantity.
        """
        await self._rate_limiter.acquire()
        session = self._get_session()

        headers = await self._auth_headers("FHKST01010100")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }
        url = f"{self._base_url}/uapi/domestic-stock/v1/quotations/inquire-price"

        def _f(val: str | None) -> float:
            try:
                return float(val or "0")
            except ValueError:
                return 0.0

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"get_current_price failed ({resp.status}): {text}"
                    )
                data = await resp.json()
                out = data.get("output", {})
                return (
                    _f(out.get("stck_prpr")),
                    _f(out.get("prdy_ctrt")),
                    _f(out.get("frgn_ntby_qty")),
                )
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(
                f"Network error fetching current price: {exc}"
            ) from exc

    async def get_balance(self) -> dict[str, Any]:
        """Fetch current account balance and holdings."""
        await self._rate_limiter.acquire()
        session = self._get_session()

        # TR_ID: 실전 TTTC8434R, 모의 VTTC8434R
        # Source: 한국투자증권 오픈API 전체문서 (20260221) — '국내주식 잔고조회' 시트
        tr_id = "TTTC8434R" if self._settings.MODE == "live" else "VTTC8434R"
        headers = await self._auth_headers(tr_id)
        params = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        url = f"{self._base_url}/uapi/domestic-stock/v1/trading/inquire-balance"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"get_balance failed ({resp.status}): {text}"
                    )
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching balance: {exc}") from exc

    async def send_order(
        self,
        stock_code: str,
        order_type: str,  # "BUY" or "SELL"
        quantity: int,
        price: int = 0,
    ) -> dict[str, Any]:
        """Submit a buy or sell order.

        Args:
            stock_code: 6-digit stock code.
            order_type: "BUY" or "SELL".
            quantity: Number of shares.
            price: Order price (0 for market order).
        """
        await self._rate_limiter.acquire()
        session = self._get_session()

        # TR_ID: 실전 BUY=TTTC0012U SELL=TTTC0011U, 모의 BUY=VTTC0012U SELL=VTTC0011U
        # Source: 한국투자증권 오픈API 전체문서 (20260221) — '주식주문(현금)' 시트
        # ※ TTTC0802U/VTTC0802U는 미수매수(증거금40% 계좌 전용) — 현금주문에 사용 금지
        if self._settings.MODE == "live":
            tr_id = "TTTC0012U" if order_type == "BUY" else "TTTC0011U"
        else:
            tr_id = "VTTC0012U" if order_type == "BUY" else "VTTC0011U"

        # KRX requires limit orders to be rounded down to the tick unit.
        # ORD_DVSN: "00"=지정가, "01"=시장가
        if price > 0:
            ord_dvsn = "00"  # 지정가
            ord_price = kr_round_down(price)
        else:
            ord_dvsn = "01"  # 시장가
            ord_price = 0

        body = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_cd,
            "PDNO": stock_code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(ord_price),
        }

        hash_key = await self._get_hash_key(body)
        headers = await self._auth_headers(tr_id)
        headers["hashkey"] = hash_key

        url = f"{self._base_url}/uapi/domestic-stock/v1/trading/order-cash"

        try:
            async with session.post(url, headers=headers, json=body) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"send_order failed ({resp.status}): {text}"
                    )
                data = await resp.json()
                logger.info(
                    "Order submitted",
                    extra={
                        "stock_code": stock_code,
                        "action": order_type,
                    },
                )
                return data
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error sending order: {exc}") from exc

    async def fetch_market_rankings(
        self,
        ranking_type: str = "volume",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Fetch market rankings from KIS API.

        Args:
            ranking_type: Type of ranking ("volume" or "fluctuation")
            limit: Maximum number of results to return

        Returns:
            List of stock data dicts with keys: stock_code, name, price, volume,
            change_rate, volume_increase_rate

        Raises:
            ConnectionError: If API request fails
        """
        await self._rate_limiter.acquire()
        session = self._get_session()

        if ranking_type == "volume":
            # 거래량순위: FHPST01710000 / /quotations/volume-rank
            tr_id = "FHPST01710000"
            url = f"{self._base_url}/uapi/domestic-stock/v1/quotations/volume-rank"
            params: dict[str, str] = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "0",
                "FID_INPUT_PRICE_2": "0",
                "FID_VOL_CNT": "0",
                "FID_INPUT_DATE_1": "",
            }
        else:
            # 등락률순위: FHPST01700000 / /ranking/fluctuation (소문자 파라미터)
            tr_id = "FHPST01700000"
            url = f"{self._base_url}/uapi/domestic-stock/v1/ranking/fluctuation"
            params = {
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20170",
                "fid_input_iscd": "0000",
                "fid_rank_sort_cls_code": "0000",
                "fid_input_cnt_1": str(limit),
                "fid_prc_cls_code": "0",
                "fid_input_price_1": "0",
                "fid_input_price_2": "0",
                "fid_vol_cnt": "0",
                "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": "0",
                "fid_div_cls_code": "0",
                "fid_rsfl_rate1": "0",
                "fid_rsfl_rate2": "0",
            }

        headers = await self._auth_headers(tr_id)

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"fetch_market_rankings failed ({resp.status}): {text}"
                    )
                data = await resp.json()

            # Parse response - output is a list of ranked stocks
            def _safe_float(value: str | float | None, default: float = 0.0) -> float:
                if value is None or value == "":
                    return default
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return default

            rankings = []
            for item in data.get("output", [])[:limit]:
                rankings.append({
                    "stock_code": item.get("mksc_shrn_iscd", ""),
                    "name": item.get("hts_kor_isnm", ""),
                    "price": _safe_float(item.get("stck_prpr", "0")),
                    "volume": _safe_float(item.get("acml_vol", "0")),
                    "change_rate": _safe_float(item.get("prdy_ctrt", "0")),
                    "volume_increase_rate": _safe_float(item.get("vol_inrt", "0")),
                })
            return rankings

        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching rankings: {exc}") from exc

    async def get_daily_prices(
        self,
        stock_code: str,
        days: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch daily OHLCV price history for a stock.

        Args:
            stock_code: 6-digit stock code
            days: Number of trading days to fetch (default 20 for RSI calculation)

        Returns:
            List of daily price dicts with keys: date, open, high, low, close, volume
            Sorted oldest to newest

        Raises:
            ConnectionError: If API request fails
        """
        await self._rate_limiter.acquire()
        session = self._get_session()

        headers = await self._auth_headers("FHKST03010100")

        # Calculate date range (today and N days ago)
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",  # Daily
            "FID_ORG_ADJ_PRC": "0",  # Adjusted price
        }

        url = f"{self._base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"get_daily_prices failed ({resp.status}): {text}"
                    )
                data = await resp.json()

            # Parse response
            def _safe_float(value: str | float | None, default: float = 0.0) -> float:
                if value is None or value == "":
                    return default
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return default

            prices = []
            for item in data.get("output2", []):
                prices.append({
                    "date": item.get("stck_bsop_date", ""),
                    "open": _safe_float(item.get("stck_oprc", "0")),
                    "high": _safe_float(item.get("stck_hgpr", "0")),
                    "low": _safe_float(item.get("stck_lwpr", "0")),
                    "close": _safe_float(item.get("stck_clpr", "0")),
                    "volume": _safe_float(item.get("acml_vol", "0")),
                })

            # Sort oldest to newest (KIS returns newest first)
            prices.reverse()

            return prices[:days]  # Return only requested number of days

        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching daily prices: {exc}") from exc
