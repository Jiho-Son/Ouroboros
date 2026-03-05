"""Async wrapper for the Korea Investment Securities (KIS) Open API.

Handles token refresh, rate limiting (leaky bucket), and hash key generation.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Any, cast

import aiohttp

from src.broker.kr_exchange_router import KRExchangeRouter
from src.config import Settings
from src.core.order_policy import classify_session_id
from src.markets.schedule import MARKETS

# KIS virtual trading server has a known SSL certificate hostname mismatch.
_KIS_VTS_HOST = "openapivts.koreainvestment.com"

logger = logging.getLogger(__name__)


def _normalize_domestic_exchange_code(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw in {"NX", "NXT"}:
        return "NXT"
    if raw in {"J", "KRX"}:
        return "KRX"
    return "KRX"


def _extract_domestic_pending_order_exchange(order: dict[str, Any]) -> str:
    for key in (
        "order_exchange",
        "excg_id_dvsn_cd",
        "EXCG_ID_DVSN_CD",
        "excg_dvsn_cd",
        "EXCG_DVSN_CD",
        "ord_excg_cd",
        "ORD_EXCG_CD",
    ):
        if key in order:
            return _normalize_domestic_exchange_code(order.get(key))
    return "KRX"


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
        self._kr_router = KRExchangeRouter()

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
                timeout=timeout,
                connector=connector,
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
            data = cast(dict[str, Any], await resp.json())

        hash_value = data.get("HASH")
        if not isinstance(hash_value, str):
            raise ConnectionError("Hash key response missing HASH")
        return hash_value

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
        return await self.get_orderbook_by_market(stock_code, market_div_code="J")

    async def get_orderbook_by_market(
        self,
        stock_code: str,
        *,
        market_div_code: str,
    ) -> dict[str, Any]:
        """Fetch orderbook for a specific domestic market division code."""
        await self._rate_limiter.acquire()
        session = self._get_session()

        headers = await self._auth_headers("FHKST01010200")
        params = {
            "FID_COND_MRKT_DIV_CODE": market_div_code,
            "FID_INPUT_ISCD": stock_code,
        }
        url = f"{self._base_url}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(f"get_orderbook failed ({resp.status}): {text}")
                return cast(dict[str, Any], await resp.json())
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching orderbook: {exc}") from exc

    @staticmethod
    def _extract_orderbook_metrics(payload: dict[str, Any]) -> tuple[float | None, float | None]:
        output = payload.get("output1") or payload.get("output") or {}
        if not isinstance(output, dict):
            return None, None

        def _float(*keys: str) -> float | None:
            for key in keys:
                raw = output.get(key)
                if raw in (None, ""):
                    continue
                try:
                    return float(cast(str | int | float, raw))
                except (ValueError, TypeError):
                    continue
            return None

        ask = _float("askp1", "stck_askp1")
        bid = _float("bidp1", "stck_bidp1")
        if ask is not None and bid is not None and ask > 0 and bid > 0 and ask >= bid:
            mid = (ask + bid) / 2
            if mid > 0:
                spread = (ask - bid) / mid
            else:
                spread = None
        else:
            spread = None

        ask_qty = _float("askp_rsqn1", "ask_qty1")
        bid_qty = _float("bidp_rsqn1", "bid_qty1")
        if ask_qty is not None and bid_qty is not None and ask_qty >= 0 and bid_qty >= 0:
            liquidity = ask_qty + bid_qty
        else:
            liquidity = None

        return spread, liquidity

    async def _load_dual_listing_metrics(
        self,
        stock_code: str,
    ) -> tuple[bool, float | None, float | None, float | None, float | None]:
        """Try KRX/NXT orderbooks and derive spread/liquidity metrics."""
        spread_krx: float | None = None
        spread_nxt: float | None = None
        liquidity_krx: float | None = None
        liquidity_nxt: float | None = None

        for market_div_code, exchange in (("J", "KRX"), ("NX", "NXT")):
            try:
                payload = await self.get_orderbook_by_market(
                    stock_code,
                    market_div_code=market_div_code,
                )
            except ConnectionError:
                continue

            spread, liquidity = self._extract_orderbook_metrics(payload)
            if exchange == "KRX":
                spread_krx = spread
                liquidity_krx = liquidity
            else:
                spread_nxt = spread
                liquidity_nxt = liquidity

        is_dual_listed = (
            (spread_krx is not None and spread_nxt is not None)
            or (liquidity_krx is not None and liquidity_nxt is not None)
        )
        return is_dual_listed, spread_krx, spread_nxt, liquidity_krx, liquidity_nxt

    async def get_current_price(
        self,
        stock_code: str,
        *,
        market_div_code: str = "J",
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
        params = {"FID_COND_MRKT_DIV_CODE": market_div_code, "FID_INPUT_ISCD": stock_code}
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
                    raise ConnectionError(f"get_current_price failed ({resp.status}): {text}")
                data = await resp.json()
                out = data.get("output", {})
                return (
                    _f(out.get("stck_prpr")),
                    _f(out.get("prdy_ctrt")),
                    _f(out.get("frgn_ntby_qty")),
                )
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching current price: {exc}") from exc

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
                    raise ConnectionError(f"get_balance failed ({resp.status}): {text}")
                return cast(dict[str, Any], await resp.json())
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching balance: {exc}") from exc

    async def send_order(
        self,
        stock_code: str,
        order_type: str,  # "BUY" or "SELL"
        quantity: int,
        price: float = 0,
        session_id: str | None = None,
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

        resolved_session = session_id or classify_session_id(MARKETS["KR"])
        if session_id is not None:
            is_dual_listed, spread_krx, spread_nxt, liquidity_krx, liquidity_nxt = (
                await self._load_dual_listing_metrics(stock_code)
            )
        else:
            is_dual_listed = False
            spread_krx = None
            spread_nxt = None
            liquidity_krx = None
            liquidity_nxt = None
        resolution = self._kr_router.resolve_for_order(
            stock_code=stock_code,
            session_id=resolved_session,
            is_dual_listed=is_dual_listed,
            spread_krx=spread_krx,
            spread_nxt=spread_nxt,
            liquidity_krx=liquidity_krx,
            liquidity_nxt=liquidity_nxt,
        )

        body = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_cd,
            "PDNO": stock_code,
            "EXCG_ID_DVSN_CD": resolution.exchange_code,
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
                    raise ConnectionError(f"send_order failed ({resp.status}): {text}")
                data = cast(dict[str, Any], await resp.json())
                logger.info(
                    "Order submitted",
                    extra={
                        "stock_code": stock_code,
                        "action": order_type,
                        "session_id": resolved_session,
                        "exchange": resolution.exchange_code,
                        "routing_reason": resolution.reason,
                    },
                )
                return data
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error sending order: {exc}") from exc

    async def fetch_market_rankings(
        self,
        ranking_type: str = "volume",
        limit: int = 30,
        session_id: str | None = None,
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

        resolved_session = session_id or classify_session_id(MARKETS["KR"])
        ranking_market_code = self._kr_router.resolve_for_ranking(resolved_session)

        if ranking_type == "volume":
            # 거래량순위: FHPST01710000 / /quotations/volume-rank
            tr_id = "FHPST01710000"
            url = f"{self._base_url}/uapi/domestic-stock/v1/quotations/volume-rank"
            params: dict[str, str] = {
                "FID_COND_MRKT_DIV_CODE": ranking_market_code,
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
                "fid_cond_mrkt_div_code": ranking_market_code,
                "fid_cond_scr_div_code": "20170",
                "fid_input_iscd": "0000",
                "fid_rank_sort_cls_code": "0",
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
                    raise ConnectionError(f"fetch_market_rankings failed ({resp.status}): {text}")
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
                rankings.append(
                    {
                        "stock_code": item.get("stck_shrn_iscd") or item.get("mksc_shrn_iscd", ""),
                        "name": item.get("hts_kor_isnm", ""),
                        "price": _safe_float(item.get("stck_prpr", "0")),
                        "volume": _safe_float(item.get("acml_vol", "0")),
                        "change_rate": _safe_float(item.get("prdy_ctrt", "0")),
                        "volume_increase_rate": _safe_float(item.get("vol_inrt", "0")),
                    }
                )
            return rankings

        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching rankings: {exc}") from exc

    async def get_domestic_pending_orders(self) -> list[dict[str, Any]]:
        """Fetch unfilled (pending) domestic limit orders.

        The KIS pending-orders API (TTTC0084R) is unsupported in paper (VTS)
        mode, so this method returns an empty list immediately when MODE is
        not "live".

        Returns:
            List of pending order dicts from the KIS ``output`` field.
            Each dict includes keys such as ``odno``, ``orgn_odno``,
            ``ord_gno_brno``, ``psbl_qty``, ``sll_buy_dvsn_cd``, ``pdno``.
        """
        if self._settings.MODE != "live":
            logger.debug(
                "get_domestic_pending_orders: paper mode — TTTC0084R unsupported, returning []"
            )
            return []

        await self._rate_limiter.acquire()
        session = self._get_session()

        # TR_ID: 실전 TTTC0084R (모의 미지원)
        # Source: 한국투자증권 오픈API 전체문서 (20260221) — '주식 미체결조회' 시트
        headers = await self._auth_headers("TTTC0084R")
        params = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_cd,
            "INQR_DVSN_1": "0",
            "INQR_DVSN_2": "0",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        url = f"{self._base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"get_domestic_pending_orders failed ({resp.status}): {text}"
                    )
                data = await resp.json()
                output = data.get("output", []) or []
                normalized_orders: list[dict[str, Any]] = []
                for raw_order in output:
                    if not isinstance(raw_order, dict):
                        continue
                    normalized = dict(raw_order)
                    normalized["order_exchange"] = _extract_domestic_pending_order_exchange(
                        raw_order
                    )
                    normalized_orders.append(normalized)
                return normalized_orders
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching domestic pending orders: {exc}") from exc

    async def cancel_domestic_order(
        self,
        stock_code: str,
        orgn_odno: str,
        krx_fwdg_ord_orgno: str,
        qty: int,
        order_exchange: str = "KRX",
    ) -> dict[str, Any]:
        """Cancel an unfilled domestic limit order.

        Args:
            stock_code: 6-digit domestic stock code (``pdno``).
            orgn_odno: Original order number from pending-orders response
                (``orgn_odno`` field).
            krx_fwdg_ord_orgno: KRX forwarding order branch number from
                pending-orders response (``ord_gno_brno`` field).
            qty: Quantity to cancel (use ``psbl_qty`` from pending order).

        Returns:
            Raw KIS API response dict (check ``rt_cd == "0"`` for success).
        """
        await self._rate_limiter.acquire()
        session = self._get_session()

        # TR_ID: 실전 TTTC0013U, 모의 VTTC0013U
        # Source: 한국투자증권 오픈API 전체문서 (20260221) — '주식주문(정정취소)' 시트
        tr_id = "TTTC0013U" if self._settings.MODE == "live" else "VTTC0013U"

        body = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._product_cd,
            "KRX_FWDG_ORD_ORGNO": krx_fwdg_ord_orgno,
            "ORGN_ODNO": orgn_odno,
            "EXCG_ID_DVSN_CD": _normalize_domestic_exchange_code(order_exchange),
            "ORD_DVSN": "00",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
            "RVSE_CNCL_DVSN_CD": "02",
            "QTY_ALL_ORD_YN": "Y",
        }

        hash_key = await self._get_hash_key(body)
        headers = await self._auth_headers(tr_id)
        headers["hashkey"] = hash_key

        url = f"{self._base_url}/uapi/domestic-stock/v1/trading/order-rvsecncl"

        try:
            async with session.post(url, headers=headers, json=body) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(f"cancel_domestic_order failed ({resp.status}): {text}")
                return cast(dict[str, Any], await resp.json())
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error cancelling domestic order: {exc}") from exc

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
                    raise ConnectionError(f"get_daily_prices failed ({resp.status}): {text}")
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
                prices.append(
                    {
                        "date": item.get("stck_bsop_date", ""),
                        "open": _safe_float(item.get("stck_oprc", "0")),
                        "high": _safe_float(item.get("stck_hgpr", "0")),
                        "low": _safe_float(item.get("stck_lwpr", "0")),
                        "close": _safe_float(item.get("stck_clpr", "0")),
                        "volume": _safe_float(item.get("acml_vol", "0")),
                    }
                )

            # Sort oldest to newest (KIS returns newest first)
            prices.reverse()

            return prices[:days]  # Return only requested number of days

        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching daily prices: {exc}") from exc
