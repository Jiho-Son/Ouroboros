"""Minimal KIS websocket helpers for realtime hard-stop price monitoring."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

from src.broker.kis_api import KISBroker

logger = logging.getLogger(__name__)

_DEFAULT_CUSTOMER_TYPE = "P"
_DOMESTIC_PRICE_TR_ID = "H0STCNT0"
_OVERSEAS_PRICE_TR_ID = "HDFSCNT0"
_SUPPORTED_MARKETS = {"KR", "US_NASDAQ", "US_NYSE", "US_AMEX"}
_OVERSEAS_TR_KEY_PREFIXES = {
    "US_NASDAQ": "DNAS",
    "US_NYSE": "DNYS",
    "US_AMEX": "DAMS",
}
_OVERSEAS_EVENT_PREFIXES = {
    "DNAS": "US_NASDAQ",
    "DNYS": "US_NYSE",
    "DAMS": "US_AMEX",
    "RBAQ": "US_NASDAQ",
    "BAY": "US_NYSE",
    "BAA": "US_AMEX",
}


@dataclass(frozen=True, slots=True)
class KISWebSocketPriceEvent:
    market_code: str
    stock_code: str
    price: float
    tr_id: str


def supports_realtime_price_market(market_code: str) -> bool:
    return market_code in _SUPPORTED_MARKETS


def resolve_realtime_price_subscription(*, market_code: str, stock_code: str) -> tuple[str, str]:
    """Return websocket TR metadata for a supported hard-stop market."""
    symbol = stock_code.strip().upper()
    if not symbol:
        raise ValueError("stock_code is required for websocket subscriptions")
    if market_code == "KR":
        return _DOMESTIC_PRICE_TR_ID, symbol

    prefix = _OVERSEAS_TR_KEY_PREFIXES.get(market_code)
    if prefix is None:
        raise ValueError(f"unsupported realtime websocket market: {market_code}")
    return _OVERSEAS_PRICE_TR_ID, f"{prefix}{symbol}"


def build_subscription_message(
    *,
    approval_key: str,
    tr_id: str,
    tr_key: str,
    tr_type: str,
    customer_type: str = _DEFAULT_CUSTOMER_TYPE,
) -> dict[str, object]:
    """Return the request payload expected by KIS websocket subscriptions."""
    return {
        "header": {
            "approval_key": approval_key,
            "custtype": customer_type,
            "tr_type": tr_type,
            "content-type": "utf-8",
        },
        "body": {
            "input": {
                "tr_id": tr_id,
                "tr_key": tr_key,
            }
        },
    }


def parse_price_event(raw: str) -> KISWebSocketPriceEvent | None:
    """Parse a KIS realtime trade message into a simple price event."""
    if not raw or raw[0] not in {"0", "1"}:
        return None
    parts = raw.split("|", 3)
    if len(parts) != 4:
        return None

    _, tr_id, _, payload = parts
    fields = payload.split("^")
    if tr_id == _DOMESTIC_PRICE_TR_ID:
        if len(fields) < 3:
            return None
        stock_code = fields[0].strip().upper()
        try:
            price = float(fields[2])
        except ValueError:
            return None
        if not stock_code:
            return None
        return KISWebSocketPriceEvent(
            market_code="KR",
            stock_code=stock_code,
            price=price,
            tr_id=tr_id,
        )

    if tr_id != _OVERSEAS_PRICE_TR_ID or len(fields) < 12:
        return None

    rsym = fields[0].strip().upper()
    market_code = _OVERSEAS_EVENT_PREFIXES.get(rsym[:4])
    stock_code = fields[1].strip().upper()
    if market_code is None or not stock_code:
        return None

    decimals = _parse_int(fields[2], default=0)
    last_price_raw = fields[11].strip()
    if not last_price_raw:
        return None
    try:
        price = int(last_price_raw) / (10**decimals)
    except ValueError:
        return None

    return KISWebSocketPriceEvent(
        market_code=market_code,
        stock_code=stock_code,
        price=price,
        tr_id=tr_id,
    )


class KISWebSocketClient:
    """Lightweight websocket client for KIS realtime price subscriptions."""

    def __init__(
        self,
        *,
        broker: KISBroker | Any,
        connect: Callable[[str], Any] | None = None,
        ws_url: str,
        on_price: Callable[[KISWebSocketPriceEvent], Awaitable[None]] | None = None,
        retry_delay_seconds: float = 1.0,
        max_retries: int = 3,
    ) -> None:
        self._broker = broker
        self._connect = connect or self._default_connect
        self._ws_url = ws_url
        self._on_price = on_price
        self._retry_delay_seconds = retry_delay_seconds
        self._max_retries = max_retries
        self._session: aiohttp.ClientSession | None = None
        self._ws: Any | None = None
        self._subscriptions: set[tuple[str, str]] = set()
        self._stop_requested = False

    async def subscribe(self, market_code: str, stock_code: str) -> None:
        subscription = (market_code, stock_code.strip().upper())
        already_subscribed = subscription in self._subscriptions
        self._subscriptions.add(subscription)
        if self._ws is not None and not already_subscribed:
            await self._send_subscription(
                self._ws,
                market_code=market_code,
                stock_code=stock_code,
                tr_type="1",
            )

    async def unsubscribe(self, market_code: str, stock_code: str) -> None:
        subscription = (market_code, stock_code.strip().upper())
        if subscription not in self._subscriptions:
            return
        self._subscriptions.discard(subscription)
        if self._ws is not None:
            await self._send_subscription(
                self._ws,
                market_code=market_code,
                stock_code=stock_code,
                tr_type="0",
            )

    def request_stop(self) -> None:
        self._stop_requested = True

    async def stop(self) -> None:
        self.request_stop()
        if self._ws is not None and hasattr(self._ws, "close"):
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def run(self) -> None:
        self._stop_requested = False
        retries = 0
        while not self._stop_requested:
            try:
                async with self._connect(self._ws_url) as ws:
                    self._ws = ws
                    await self._resubscribe_all(ws)
                    async for raw in ws:
                        if self._stop_requested:
                            break
                        text = _extract_ws_text(raw)
                        if text is None:
                            continue
                        event = parse_price_event(text)
                        if event is not None and self._on_price is not None:
                            await self._on_price(event)
                if self._stop_requested:
                    break
                retries += 1
            except Exception as exc:
                retries += 1
                logger.warning("KIS websocket loop failed (attempt=%d): %s", retries, exc)
            finally:
                self._ws = None

            if self._stop_requested or retries >= self._max_retries:
                break
            await asyncio.sleep(self._retry_delay_seconds)

        if self._ws is not None and hasattr(self._ws, "close"):
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._ws = None
        self._session = None

    def _default_connect(self, url: str) -> Any:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session.ws_connect(url)

    async def _resubscribe_all(self, ws: Any) -> None:
        for market_code, stock_code in sorted(self._subscriptions):
            await self._send_subscription(
                ws,
                market_code=market_code,
                stock_code=stock_code,
                tr_type="1",
            )

    async def _send_subscription(
        self,
        ws: Any,
        *,
        market_code: str,
        stock_code: str,
        tr_type: str,
    ) -> None:
        approval_key = await self._broker.get_websocket_approval_key()
        tr_id, tr_key = resolve_realtime_price_subscription(
            market_code=market_code,
            stock_code=stock_code,
        )
        payload = build_subscription_message(
            approval_key=approval_key,
            tr_id=tr_id,
            tr_key=tr_key,
            tr_type=tr_type,
        )
        await ws.send_json(payload)


def _parse_int(value: str | int | None, *, default: int) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _extract_ws_text(message: object) -> str | None:
    if isinstance(message, str):
        return message

    msg_type = getattr(message, "type", None)
    if msg_type == aiohttp.WSMsgType.TEXT:
        data = getattr(message, "data", None)
        return data if isinstance(data, str) else None
    return None
