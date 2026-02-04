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

            logger.info("Refreshing KIS access token")
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

    async def get_balance(self) -> dict[str, Any]:
        """Fetch current account balance and holdings."""
        await self._rate_limiter.acquire()
        session = self._get_session()

        headers = await self._auth_headers("VTTC8434R")  # 모의투자 잔고조회
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

        tr_id = "VTTC0802U" if order_type == "BUY" else "VTTC0801U"
        body = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_cd,
            "PDNO": stock_code,
            "ORD_DVSN": "01" if price > 0 else "06",  # 01=지정가, 06=시장가
            "ORD_QTY": str(quantity),
            "ORD_UNPR": str(price),
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
