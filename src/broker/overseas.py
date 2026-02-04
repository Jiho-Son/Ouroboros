"""KIS Overseas Stock API client."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from src.broker.kis_api import KISBroker

logger = logging.getLogger(__name__)


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
        params = {
            "AUTH": "",
            "EXCD": exchange_code,
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
                logger.info(
                    "Overseas order submitted",
                    extra={
                        "exchange": exchange_code,
                        "stock_code": stock_code,
                        "action": order_type,
                    },
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
