from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.broker.kis_websocket import (
    KISWebSocketClient,
    KISWebSocketPriceEvent,
    build_subscription_message,
    parse_price_event,
)


class _FakeWebSocket:
    def __init__(self, messages: list[object]) -> None:
        self._messages = list(messages)
        self.sent_json: list[dict[str, object]] = []
        self.closed = False

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent_json.append(payload)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> _FakeWebSocket:
        return self

    async def __anext__(self) -> object:
        if not self._messages:
            raise StopAsyncIteration
        msg = self._messages.pop(0)
        if isinstance(msg, Exception):
            raise msg
        return msg


class _FakeConnect:
    def __init__(self, ws: _FakeWebSocket) -> None:
        self._ws = ws

    async def __aenter__(self) -> _FakeWebSocket:
        return self._ws

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def test_build_subscription_message_uses_kis_shape() -> None:
    payload = build_subscription_message(
        approval_key="approval-1",
        tr_id="H0STCNT0",
        tr_key="005930",
        tr_type="1",
    )

    assert payload["header"]["approval_key"] == "approval-1"
    assert payload["header"]["custtype"] == "P"
    assert payload["body"]["input"]["tr_id"] == "H0STCNT0"
    assert payload["body"]["input"]["tr_key"] == "005930"


def test_parse_price_event_reads_domestic_trade_price() -> None:
    raw = "0|H0STCNT0|001|005930^093000^61500^2^100^0.16"

    event = parse_price_event(raw)

    assert event == KISWebSocketPriceEvent(
        stock_code="005930",
        price=61500,
        tr_id="H0STCNT0",
    )


@pytest.mark.asyncio
async def test_subscribe_sends_message_to_live_socket() -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    ws = _FakeWebSocket(messages=[])
    client = KISWebSocketClient(
        broker=broker,
        connect=lambda _url: _FakeConnect(ws),
        ws_url="ws://example.test/tryitout",
        retry_delay_seconds=0.0,
    )
    client._ws = ws

    await client.subscribe("005930")

    assert ws.sent_json[0]["body"]["input"]["tr_key"] == "005930"


@pytest.mark.asyncio
async def test_subscribe_does_not_resend_duplicate_registration() -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    ws = _FakeWebSocket(messages=[])
    client = KISWebSocketClient(
        broker=broker,
        connect=lambda _url: _FakeConnect(ws),
        ws_url="ws://example.test/tryitout",
        retry_delay_seconds=0.0,
    )
    client._ws = ws

    await client.subscribe("005930")
    await client.subscribe("005930")

    assert len(ws.sent_json) == 1


@pytest.mark.asyncio
async def test_run_reconnects_and_resubscribes_existing_symbols() -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    first_ws = _FakeWebSocket(messages=[RuntimeError("boom")])
    second_ws = _FakeWebSocket(messages=[])
    queue = [_FakeConnect(first_ws), _FakeConnect(second_ws)]
    seen_urls: list[str] = []
    callback = AsyncMock()

    def connect(url: str) -> _FakeConnect:
        seen_urls.append(url)
        return queue.pop(0)

    client = KISWebSocketClient(
        broker=broker,
        connect=connect,
        ws_url="ws://example.test/tryitout",
        retry_delay_seconds=0.0,
        on_price=callback,
        max_retries=2,
    )
    await client.subscribe("005930")

    task = asyncio.create_task(client.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    client.request_stop()
    await task

    assert seen_urls == ["ws://example.test/tryitout", "ws://example.test/tryitout"]
    assert first_ws.sent_json[0]["body"]["input"]["tr_key"] == "005930"
    assert second_ws.sent_json[0]["body"]["input"]["tr_key"] == "005930"
    callback.assert_not_called()


@pytest.mark.asyncio
async def test_run_uses_exact_configured_ws_url() -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    ws = _FakeWebSocket(messages=[])
    seen_urls: list[str] = []

    def connect(url: str) -> _FakeConnect:
        seen_urls.append(url)
        return _FakeConnect(ws)

    client = KISWebSocketClient(
        broker=broker,
        connect=connect,
        ws_url="ws://example.test/custom-path",
        retry_delay_seconds=0.0,
        max_retries=1,
    )

    await client.run()

    assert seen_urls == ["ws://example.test/custom-path"]


@pytest.mark.asyncio
async def test_run_can_restart_with_same_client_instance() -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    seen_urls: list[str] = []

    def connect(url: str) -> _FakeConnect:
        seen_urls.append(url)
        return _FakeConnect(_FakeWebSocket(messages=[]))

    client = KISWebSocketClient(
        broker=broker,
        connect=connect,
        ws_url="ws://example.test/custom-path",
        retry_delay_seconds=0.0,
        max_retries=1,
    )

    await client.run()
    await client.run()

    assert seen_urls == [
        "ws://example.test/custom-path",
        "ws://example.test/custom-path",
    ]
