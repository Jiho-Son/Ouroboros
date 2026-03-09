"""Minimal KIS websocket helpers for realtime domestic price monitoring."""

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


@dataclass(frozen=True, slots=True)
class KISWebSocketPriceEvent:
    stock_code: str
    price: int
    tr_id: str


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
    """Parse a KIS domestic trade message into a simple price event."""
    if not raw or raw[0] not in {"0", "1"}:
        return None
    parts = raw.split("|", 3)
    if len(parts) != 4:
        return None

    _, tr_id, _, payload = parts
    if tr_id != _DOMESTIC_PRICE_TR_ID:
        return None

    fields = payload.split("^")
    if len(fields) < 3:
        return None

    stock_code = fields[0].strip()
    try:
        price = int(float(fields[2]))
    except ValueError:
        return None
    if not stock_code:
        return None
    return KISWebSocketPriceEvent(stock_code=stock_code, price=price, tr_id=tr_id)


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
        self._subscriptions: set[str] = set()
        self._stop_requested = False

    async def subscribe(self, stock_code: str) -> None:
        already_subscribed = stock_code in self._subscriptions
        self._subscriptions.add(stock_code)
        if self._ws is not None and not already_subscribed:
            await self._send_subscription(self._ws, stock_code=stock_code, tr_type="1")

    async def unsubscribe(self, stock_code: str) -> None:
        if stock_code not in self._subscriptions:
            return
        self._subscriptions.discard(stock_code)
        if self._ws is not None:
            await self._send_subscription(self._ws, stock_code=stock_code, tr_type="0")

    def request_stop(self) -> None:
        self._stop_requested = True

    async def stop(self) -> None:
        self.request_stop()
        if self._ws is not None and hasattr(self._ws, "close"):
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def run(self) -> None:
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

        await self.stop()

    def _default_connect(self, url: str) -> Any:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session.ws_connect(url)

    async def _resubscribe_all(self, ws: Any) -> None:
        for stock_code in sorted(self._subscriptions):
            await self._send_subscription(ws, stock_code=stock_code, tr_type="1")

    async def _send_subscription(self, ws: Any, *, stock_code: str, tr_type: str) -> None:
        approval_key = await self._broker.get_websocket_approval_key()
        payload = build_subscription_message(
            approval_key=approval_key,
            tr_id=_DOMESTIC_PRICE_TR_ID,
            tr_key=stock_code,
            tr_type=tr_type,
        )
        await ws.send_json(payload)


def _extract_ws_text(message: object) -> str | None:
    if isinstance(message, str):
        return message

    msg_type = getattr(message, "type", None)
    if msg_type == aiohttp.WSMsgType.TEXT:
        data = getattr(message, "data", None)
        return data if isinstance(data, str) else None
    return None
