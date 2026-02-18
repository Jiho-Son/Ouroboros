"""KIS Overseas Stock API client."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from src.broker.kis_api import KISBroker

logger = logging.getLogger(__name__)


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


class OverseasBroker:
    """KIS Overseas Stock API wrapper that reuses KISBroker infrastructure."""

    def __init__(self, kis_broker: KISBroker) -> None:
        """
        Initialize overseas broker.

        Args:
            kis_broker: Domestic KIS broker instance to reuse session/token/rate limiter
        """
        self._broker = kis_broker

    async def get_overseas_price(
        self, exchange_code: str, stock_code: str
    ) -> dict[str, Any]:
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
                    raise ConnectionError(
                        f"get_overseas_price failed ({resp.status}): {text}"
                    )
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(
                f"Network error fetching overseas price: {exc}"
            ) from exc

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
                "AUTH": "",
                "EXCD": ranking_excd,
                "MIXN": "0",
                "VOL_RANG": "0",
            }
        else:
            tr_id = self._broker._settings.OVERSEAS_RANKING_FLUCT_TR_ID
            path = self._broker._settings.OVERSEAS_RANKING_FLUCT_PATH
            params = {
                "AUTH": "",
                "EXCD": ranking_excd,
                "NDAY": "0",
                "GUBN": "1",
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
                    raise ConnectionError(
                        f"fetch_overseas_rankings failed ({resp.status}): {text}"
                    )

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
            raise ConnectionError(
                f"Network error fetching overseas rankings: {exc}"
            ) from exc

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

        # Virtual trading TR_ID for overseas balance inquiry
        headers = await self._broker._auth_headers("VTTS3012R")
        params = {
            "CANO": self._broker._account_no,
            "ACNT_PRDT_CD": self._broker._product_cd,
            "OVRS_EXCG_CD": exchange_code,
            "TR_CRCY_CD": self._get_currency_code(exchange_code),
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        url = (
            f"{self._broker._base_url}/uapi/overseas-stock/v1/trading/inquire-balance"
        )

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ConnectionError(
                        f"get_overseas_balance failed ({resp.status}): {text}"
                    )
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise ConnectionError(
                f"Network error fetching overseas balance: {exc}"
            ) from exc

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

        # Virtual trading TR_IDs for overseas orders
        tr_id = "VTTT1002U" if order_type == "BUY" else "VTTT1006U"

        body = {
            "CANO": self._broker._account_no,
            "ACNT_PRDT_CD": self._broker._product_cd,
            "OVRS_EXCG_CD": exchange_code,
            "PDNO": stock_code,
            "ORD_DVSN": "00" if price > 0 else "01",  # 00=지정가, 01=시장가
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price) if price > 0 else "0",
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
                    raise ConnectionError(
                        f"send_overseas_order failed ({resp.status}): {text}"
                    )
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
            raise ConnectionError(
                f"Network error sending overseas order: {exc}"
            ) from exc

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
