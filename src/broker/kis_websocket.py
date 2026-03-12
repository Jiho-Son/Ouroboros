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


def _is_us_market_code(market_code: str) -> bool:
    return market_code.startswith("US_")


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
    market_code = _resolve_overseas_market_code_from_rsym(rsym)
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


def classify_price_event_parse_failure(raw: str) -> str | None:
    """Return a coarse reason when a websocket payload cannot be parsed as a price event."""
    if not raw:
        return "empty payload"
    if raw[0] not in {"0", "1"}:
        return "unsupported prefix"

    parts = raw.split("|", 3)
    if len(parts) != 4:
        return "unexpected frame shape"

    _, tr_id, _, payload = parts
    fields = payload.split("^")
    if tr_id == _DOMESTIC_PRICE_TR_ID:
        if len(fields) < 3:
            return "domestic payload too short"
        if not fields[0].strip():
            return "missing domestic stock code"
        try:
            float(fields[2])
        except ValueError:
            return "invalid domestic price"
        return None

    if tr_id != _OVERSEAS_PRICE_TR_ID:
        return f"unsupported tr_id={tr_id}"
    if len(fields) < 12:
        return "overseas payload too short"

    rsym = fields[0].strip().upper()
    if _resolve_overseas_market_code_from_rsym(rsym) is None:
        return f"unknown overseas prefix={rsym[:4] or 'missing'}"
    if not fields[1].strip().upper():
        return "missing overseas stock code"

    last_price_raw = fields[11].strip()
    if not last_price_raw:
        return "missing overseas last price"
    try:
        int(last_price_raw)
    except ValueError:
        return "invalid overseas last price"
    return None


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
        tr_id, tr_key = resolve_realtime_price_subscription(
            market_code=market_code,
            stock_code=stock_code,
        )
        subscription = (market_code, stock_code.strip().upper())
        already_subscribed = subscription in self._subscriptions
        self._subscriptions.add(subscription)
        if not already_subscribed:
            logger.info(
                "Registering realtime websocket subscription market=%s stock=%s tr_id=%s tr_key=%s",
                subscription[0],
                subscription[1],
                tr_id,
                tr_key,
            )
        if self._ws is not None and not already_subscribed:
            await self._send_subscription(
                self._ws,
                market_code=market_code,
                stock_code=stock_code,
                tr_type="1",
            )

    async def unsubscribe(self, market_code: str, stock_code: str) -> None:
        try:
            resolve_realtime_price_subscription(
                market_code=market_code,
                stock_code=stock_code,
            )
        except ValueError:
            return
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
                    logger.info("Realtime websocket action=connect url=%s", self._ws_url)
                    await self._resubscribe_all(ws)
                    async for raw in ws:
                        if self._stop_requested:
                            break
                        text = _extract_ws_text(raw)
                        if text is None:
                            continue
                        event = parse_price_event(text)
                        if event is None:
                            reason = classify_price_event_parse_failure(text)
                            if reason is not None:
                                if _extract_tr_id(text) == _OVERSEAS_PRICE_TR_ID:
                                    logger.info(
                                        "Realtime websocket action=ignore_us_parse_failure "
                                        "reason=%s",
                                        reason,
                                    )
                                else:
                                    logger.debug("Ignoring websocket payload with %s", reason)
                            continue
                        if _is_us_market_code(event.market_code):
                            logger.info(
                                "Realtime websocket action=parsed_us_event "
                                "market=%s stock=%s price=%.4f tr_id=%s",
                                event.market_code,
                                event.stock_code,
                                float(event.price),
                                event.tr_id,
                            )
                        else:
                            logger.debug(
                                "Parsed realtime websocket event market=%s "
                                "stock=%s price=%s tr_id=%s",
                                event.market_code,
                                event.stock_code,
                                event.price,
                                event.tr_id,
                            )
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
        if self._subscriptions:
            logger.info("Resubscribing %d realtime websocket symbols", len(self._subscriptions))
        for market_code, stock_code in sorted(self._subscriptions):
            if _is_us_market_code(market_code):
                logger.info(
                    "Realtime websocket action=resubscribe market=%s stock=%s",
                    market_code,
                    stock_code,
                )
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
        if _is_us_market_code(market_code):
            action = "subscribe" if tr_type == "1" else "unsubscribe"
            logger.info(
                "Realtime websocket action=%s market=%s stock=%s tr_id=%s tr_key=%s",
                action,
                market_code,
                stock_code.strip().upper(),
                tr_id,
                tr_key,
            )


def _parse_int(value: str | int | None, *, default: int) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _resolve_overseas_market_code_from_rsym(rsym: str) -> str | None:
    for prefix in sorted(_OVERSEAS_EVENT_PREFIXES, key=len, reverse=True):
        if rsym.startswith(prefix):
            return _OVERSEAS_EVENT_PREFIXES[prefix]
    return None


def _extract_tr_id(raw: str) -> str | None:
    parts = raw.split("|", 3)
    if len(parts) != 4:
        return None
    return parts[1]


def _extract_ws_text(message: object) -> str | None:
    if isinstance(message, str):
        return message

    msg_type = getattr(message, "type", None)
    if msg_type == aiohttp.WSMsgType.TEXT:
        data = getattr(message, "data", None)
        return data if isinstance(data, str) else None
    return None
