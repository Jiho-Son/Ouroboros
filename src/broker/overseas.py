"""KIS Overseas Stock API client."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from src.broker.kis_api import KISBroker
from src.broker.orderbook_utils import extract_orderbook_top_levels

logger = logging.getLogger(__name__)

def _format_overseas_order_price(price: float) -> str:
    """Format overseas limit prices with KIS-supported precision."""
    if price <= 0:
        return "0"
    decimals = 2 if price >= 1 else 4
    return f"{price:.{decimals}f}"


# Ranking API uses different exchange codes than order/quote APIs.
_RANKING_EXCHANGE_MAP: dict[str, str] = {
    "NASD": "NAS",
    "NYSE": "NYS",
    "AMEX": "AMS",
    "SEHK": "HKS",
    "SHAA": "SHS",
    "SZAA": "SZS",
    "HSX": "HSX",
    "HNX": "HNX",
    "TSE": "TSE",
}

# Price inquiry API (HHDFS00000300) uses the same short exchange codes as rankings.
# NASD → NAS, NYSE → NYS, AMEX → AMS (confirmed: AMEX returns empty, AMS returns price).
_PRICE_EXCHANGE_MAP: dict[str, str] = _RANKING_EXCHANGE_MAP

# Cancel order TR_IDs per exchange code — (live_tr_id, paper_tr_id).
# Source: 한국투자증권 오픈API 전체문서 (20260221) — '해외주식 주문취소' 시트
_CANCEL_TR_ID_MAP: dict[str, tuple[str, str]] = {
    "NASD": ("TTTT1004U", "VTTT1004U"),
    "NYSE": ("TTTT1004U", "VTTT1004U"),
    "AMEX": ("TTTT1004U", "VTTT1004U"),
    "SEHK": ("TTTS1003U", "VTTS1003U"),
    "TSE": ("TTTS0309U", "VTTS0309U"),
    "SHAA": ("TTTS0302U", "VTTS0302U"),
    "SZAA": ("TTTS0306U", "VTTS0306U"),
    "HNX": ("TTTS0312U", "VTTS0312U"),
    "HSX": ("TTTS0312U", "VTTS0312U"),
}


class OverseasBroker:
    """KIS Overseas Stock API wrapper that reuses KISBroker infrastructure."""

    def __init__(self, kis_broker: KISBroker) -> None:
        """
        Initialize overseas broker.

        Args:
            kis_broker: Domestic KIS broker instance to reuse session/token/rate limiter
        """
        self._broker = kis_broker

    async def get_daily_prices(
        self,
        exchange_code: str,
        stock_code: str,
        days: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch overseas daily OHLCV history for a stock."""
        await self._broker._rate_limiter.acquire()
        session = self._broker._get_session()

        headers = await self._broker._auth_headers("HHDFS76240000")
        price_excd = _PRICE_EXCHANGE_MAP.get(exchange_code, exchange_code)
        params = {
            "AUTH": "",
            "EXCD": price_excd,
            "SYMB": stock_code,
            "GUBN": "0",
            "BYMD": "",
            "MODP": "1",
        }
        url = f"{self._broker._base_url}/uapi/overseas-price/v1/quotations/dailyprice"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(f"get_daily_prices failed ({resp.status}): {text}")
                data = await resp.json()

            def _safe_float(value: str | float | None, default: float = 0.0) -> float:
                if value is None or value == "":
                    return default
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return default

            prices: list[dict[str, Any]] = []
            for item in data.get("output2", []):
                prices.append(
                    {
                        "date": item.get("xymd", ""),
                        "open": _safe_float(item.get("open"), 0.0),
                        "high": _safe_float(item.get("high"), 0.0),
                        "low": _safe_float(item.get("low"), 0.0),
                        "close": _safe_float(item.get("clos"), 0.0),
                        "volume": _safe_float(item.get("tvol"), 0.0),
                    }
                )

            prices.reverse()
            return prices[-days:]
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching overseas daily prices: {exc}") from exc

    async def get_overseas_price(self, exchange_code: str, stock_code: str) -> dict[str, Any]:
        """
        Fetch overseas stock price.

        Args:
            exchange_code: Exchange code (e.g., "NASD", "NYSE", "TSE")
            stock_code: Stock ticker symbol

        Returns:
            API response with price data

        Raises:
            ConnectionError: On network or API errors
        """
        await self._broker._rate_limiter.acquire()
        session = self._broker._get_session()

        headers = await self._broker._auth_headers("HHDFS00000300")
        # Map internal exchange codes to the short form expected by the price API.
        price_excd = _PRICE_EXCHANGE_MAP.get(exchange_code, exchange_code)
        params = {
            "AUTH": "",
            "EXCD": price_excd,
            "SYMB": stock_code,
        }
        url = f"{self._broker._base_url}/uapi/overseas-price/v1/quotations/price"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(f"get_overseas_price failed ({resp.status}): {text}")
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching overseas price: {exc}") from exc

    @staticmethod
    def _extract_orderbook_top_levels(payload: dict[str, Any]) -> tuple[float | None, float | None]:
        """Extract top ask/bid from shared pending-order orderbook payload variants."""
        # KIS overseas payloads are expected to expose a single authoritative orderbook
        # container in production, so the shared helper's alias precedence is sufficient.
        return extract_orderbook_top_levels(payload)

    async def get_overseas_orderbook(self, exchange_code: str, stock_code: str) -> dict[str, Any]:
        """Fetch overseas best bid/ask quote snapshot."""
        await self._broker._rate_limiter.acquire()
        session = self._broker._get_session()

        headers = await self._broker._auth_headers("HHDFS76200100")
        price_excd = _PRICE_EXCHANGE_MAP.get(exchange_code, exchange_code)
        params = {
            "AUTH": "",
            "EXCD": price_excd,
            "SYMB": stock_code,
        }
        url = f"{self._broker._base_url}/uapi/overseas-price/v1/quotations/inquire-asking-price"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"get_overseas_orderbook failed ({resp.status}): {text}"
                    )
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching overseas orderbook: {exc}") from exc

    async def fetch_overseas_rankings(
        self,
        exchange_code: str,
        ranking_type: str = "fluctuation",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Fetch overseas rankings (price change or volume surge).

        Ranking API specs may differ by account/product. Endpoint paths and
        TR_IDs are configurable via settings and can be overridden in .env.
        """
        if not self._broker._settings.OVERSEAS_RANKING_ENABLED:
            return []

        await self._broker._rate_limiter.acquire()
        session = self._broker._get_session()

        ranking_excd = _RANKING_EXCHANGE_MAP.get(exchange_code, exchange_code)

        if ranking_type == "volume":
            tr_id = self._broker._settings.OVERSEAS_RANKING_VOLUME_TR_ID
            path = self._broker._settings.OVERSEAS_RANKING_VOLUME_PATH
            params: dict[str, str] = {
                "KEYB": "",  # NEXT KEY BUFF — Required, 공백
                "AUTH": "",
                "EXCD": ranking_excd,
                "MIXN": "0",
                "VOL_RANG": "0",
            }
        else:
            tr_id = self._broker._settings.OVERSEAS_RANKING_FLUCT_TR_ID
            path = self._broker._settings.OVERSEAS_RANKING_FLUCT_PATH
            params = {
                "KEYB": "",  # NEXT KEY BUFF — Required, 공백
                "AUTH": "",
                "EXCD": ranking_excd,
                "NDAY": "0",
                "GUBN": "1",  # 0=하락율, 1=상승율 — 변동성 스캐너는 급등 종목 우선
                "VOL_RANG": "0",
            }

        headers = await self._broker._auth_headers(tr_id)
        url = f"{self._broker._base_url}{path}"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    if resp.status == 404:
                        logger.warning(
                            "Overseas ranking endpoint unavailable (404) for %s/%s; "
                            "using symbol fallback scan",
                            exchange_code,
                            ranking_type,
                        )
                        return []
                    raise ConnectionError(f"fetch_overseas_rankings failed ({resp.status}): {text}")

                data = await resp.json()
                rows = self._extract_ranking_rows(data)
                if rows:
                    return rows[:limit]

                logger.debug(
                    "Overseas ranking returned empty for %s/%s (keys=%s)",
                    exchange_code,
                    ranking_type,
                    list(data.keys()),
                )
                return []
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching overseas rankings: {exc}") from exc

    async def get_overseas_balance(self, exchange_code: str) -> dict[str, Any]:
        """
        Fetch overseas account balance.

        Args:
            exchange_code: Exchange code (e.g., "NASD", "NYSE")

        Returns:
            API response with balance data

        Raises:
            ConnectionError: On network or API errors
        """
        await self._broker._rate_limiter.acquire()
        session = self._broker._get_session()

        # TR_ID: 실전 TTTS3012R, 모의 VTTS3012R
        # Source: 한국투자증권 오픈API 전체문서 (20260221) — '해외주식 잔고조회' 시트
        balance_tr_id = "TTTS3012R" if self._broker._settings.MODE == "live" else "VTTS3012R"
        headers = await self._broker._auth_headers(balance_tr_id)
        params = {
            "CANO": self._broker._account_no,
            "ACNT_PRDT_CD": self._broker._product_cd,
            "OVRS_EXCG_CD": exchange_code,
            "TR_CRCY_CD": self._get_currency_code(exchange_code),
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        url = f"{self._broker._base_url}/uapi/overseas-stock/v1/trading/inquire-balance"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(f"get_overseas_balance failed ({resp.status}): {text}")
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching overseas balance: {exc}") from exc

    async def get_overseas_buying_power(
        self,
        exchange_code: str,
        stock_code: str,
        price: float,
    ) -> dict[str, Any]:
        """
        Fetch overseas buying power for a specific stock and price.

        Args:
            exchange_code: Exchange code (e.g., "NASD", "NYSE")
            stock_code: Stock ticker symbol
            price: Current stock price (used for quantity calculation)

        Returns:
            API response; key field: output.ord_psbl_frcr_amt (주문가능외화금액)

        Raises:
            ConnectionError: On network or API errors
        """
        await self._broker._rate_limiter.acquire()
        session = self._broker._get_session()

        # TR_ID: 실전 TTTS3007R, 모의 VTTS3007R
        # Source: 한국투자증권 오픈API 전체문서 (20260221) — '해외주식 매수가능금액조회' 시트
        ps_tr_id = "TTTS3007R" if self._broker._settings.MODE == "live" else "VTTS3007R"
        headers = await self._broker._auth_headers(ps_tr_id)
        params = {
            "CANO": self._broker._account_no,
            "ACNT_PRDT_CD": self._broker._product_cd,
            "OVRS_EXCG_CD": exchange_code,
            "OVRS_ORD_UNPR": f"{price:.2f}",
            "ITEM_CD": stock_code,
        }
        url = f"{self._broker._base_url}/uapi/overseas-stock/v1/trading/inquire-psamount"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"get_overseas_buying_power failed ({resp.status}): {text}"
                    )
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching overseas buying power: {exc}") from exc

    async def send_overseas_order(
        self,
        exchange_code: str,
        stock_code: str,
        order_type: str,  # "BUY" or "SELL"
        quantity: int,
        price: float = 0.0,
    ) -> dict[str, Any]:
        """
        Submit overseas stock order.

        Args:
            exchange_code: Exchange code (e.g., "NASD", "NYSE")
            stock_code: Stock ticker symbol
            order_type: "BUY" or "SELL"
            quantity: Number of shares
            price: Order price (0 for market order)

        Returns:
            API response with order result

        Raises:
            ConnectionError: On network or API errors
        """
        await self._broker._rate_limiter.acquire()
        session = self._broker._get_session()

        # TR_ID: 실전 BUY=TTTT1002U SELL=TTTT1006U, 모의 BUY=VTTT1002U SELL=VTTT1001U
        # Source: 한국투자증권 오픈API 전체문서 (20260221) — '해외주식 주문' 시트
        if self._broker._settings.MODE == "live":
            tr_id = "TTTT1002U" if order_type == "BUY" else "TTTT1006U"
        else:
            tr_id = "VTTT1002U" if order_type == "BUY" else "VTTT1001U"

        body = {
            "CANO": self._broker._account_no,
            "ACNT_PRDT_CD": self._broker._product_cd,
            "OVRS_EXCG_CD": exchange_code,
            "PDNO": stock_code,
            "ORD_DVSN": "00" if price > 0 else "01",  # 00=지정가, 01=시장가
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": _format_overseas_order_price(price),
            "ORD_SVR_DVSN_CD": "0",  # 0=해외주문
        }

        hash_key = await self._broker._get_hash_key(body)
        headers = await self._broker._auth_headers(tr_id)
        headers["hashkey"] = hash_key

        url = f"{self._broker._base_url}/uapi/overseas-stock/v1/trading/order"

        try:
            async with session.post(url, headers=headers, json=body) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(f"send_overseas_order failed ({resp.status}): {text}")
                data = await resp.json()
                rt_cd = data.get("rt_cd", "")
                msg1 = data.get("msg1", "")
                if rt_cd == "0":
                    logger.info(
                        "Overseas order submitted",
                        extra={
                            "exchange": exchange_code,
                            "stock_code": stock_code,
                            "action": order_type,
                        },
                    )
                else:
                    logger.warning(
                        "Overseas order rejected (rt_cd=%s): %s [%s %s %s qty=%d]",
                        rt_cd,
                        msg1,
                        order_type,
                        stock_code,
                        exchange_code,
                        quantity,
                    )
                return data
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error sending overseas order: {exc}") from exc

    async def get_overseas_pending_orders(self, exchange_code: str) -> list[dict[str, Any]]:
        """Fetch unfilled (pending) overseas orders for a given exchange.

        Args:
            exchange_code: Exchange code (e.g., "NASD", "SEHK").
                For US markets, NASD returns all US pending orders (NASD/NYSE/AMEX).

        Returns:
            List of pending order dicts with fields: odno, pdno, sll_buy_dvsn_cd,
            ft_ord_qty, nccs_qty, ft_ord_unpr3, ovrs_excg_cd.
            Always returns [] in paper mode (TTTS3018R is live-only).

        Raises:
            ConnectionError: On network or API errors (live mode only).
        """
        if self._broker._settings.MODE != "live":
            logger.debug("Pending orders API (TTTS3018R) not supported in paper mode; returning []")
            return []

        await self._broker._rate_limiter.acquire()
        session = self._broker._get_session()

        # TTTS3018R: 해외주식 미체결내역조회 (실전 전용)
        # Source: 한국투자증권 오픈API 전체문서 (20260221) — '해외주식 미체결조회' 시트
        headers = await self._broker._auth_headers("TTTS3018R")
        params = {
            "CANO": self._broker._account_no,
            "ACNT_PRDT_CD": self._broker._product_cd,
            "OVRS_EXCG_CD": exchange_code,
            "SORT_SQN": "DS",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        url = f"{self._broker._base_url}/uapi/overseas-stock/v1/trading/inquire-nccs"

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"get_overseas_pending_orders failed ({resp.status}): {text}"
                    )
                data = await resp.json()
                output = data.get("output", [])
                if isinstance(output, list):
                    return output
                return []
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error fetching pending orders: {exc}") from exc

    async def cancel_overseas_order(
        self,
        exchange_code: str,
        stock_code: str,
        odno: str,
        qty: int,
    ) -> dict[str, Any]:
        """Cancel an overseas limit order.

        Args:
            exchange_code: Exchange code (e.g., "NASD", "SEHK").
            stock_code: Stock ticker symbol.
            odno: Original order number to cancel.
            qty: Unfilled quantity to cancel.

        Returns:
            API response dict containing rt_cd and msg1.

        Raises:
            ValueError: If exchange_code has no cancel TR_ID mapping.
            ConnectionError: On network or API errors.
        """
        tr_ids = _CANCEL_TR_ID_MAP.get(exchange_code)
        if tr_ids is None:
            raise ValueError(f"No cancel TR_ID mapping for exchange: {exchange_code}")
        live_tr_id, paper_tr_id = tr_ids
        tr_id = live_tr_id if self._broker._settings.MODE == "live" else paper_tr_id

        await self._broker._rate_limiter.acquire()
        session = self._broker._get_session()

        # RVSE_CNCL_DVSN_CD="02" means cancel (not revision).
        # OVRS_ORD_UNPR must be "0" for cancellations.
        # Source: 한국투자증권 오픈API 전체문서 (20260221) — '해외주식 정정취소주문' 시트
        body = {
            "CANO": self._broker._account_no,
            "ACNT_PRDT_CD": self._broker._product_cd,
            "OVRS_EXCG_CD": exchange_code,
            "PDNO": stock_code,
            "ORGN_ODNO": odno,
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": "0",
            "ORD_SVR_DVSN_CD": "0",
        }

        hash_key = await self._broker._get_hash_key(body)
        headers = await self._broker._auth_headers(tr_id)
        headers["hashkey"] = hash_key

        url = f"{self._broker._base_url}/uapi/overseas-stock/v1/trading/order-rvsecncl"

        try:
            async with session.post(url, headers=headers, json=body) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(f"cancel_overseas_order failed ({resp.status}): {text}")
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(f"Network error cancelling overseas order: {exc}") from exc

    def _get_currency_code(self, exchange_code: str) -> str:
        """
        Map exchange code to currency code.

        Args:
            exchange_code: Exchange code

        Returns:
            Currency code (e.g., "USD", "JPY")
        """
        currency_map = {
            "NASD": "USD",
            "NYSE": "USD",
            "AMEX": "USD",
            "TSE": "JPY",
            "SEHK": "HKD",
            "SHAA": "CNY",
            "SZAA": "CNY",
            "HNX": "VND",
            "HSX": "VND",
        }
        return currency_map.get(exchange_code, "USD")

    def _extract_ranking_rows(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract list rows from ranking response across schema variants."""
        candidates = [data.get("output"), data.get("output1"), data.get("output2")]
        for value in candidates:
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return []
