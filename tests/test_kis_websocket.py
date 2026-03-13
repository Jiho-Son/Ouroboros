from __future__ import annotations

import asyncio
import logging
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
        market_code="KR",
        stock_code="005930",
        price=61500,
        tr_id="H0STCNT0",
    )


def test_parse_price_event_reads_overseas_trade_price() -> None:
    raw = (
        "0|HDFSCNT0|001|"
        "DNASAAPL^AAPL^4^20260309^20260309^093000^20260309^223000^"
        "001500000^001510000^001490000^001480100^5^000019900^00136^"
        "001480000^001481000^10^12^100^200^100000^30^70^120.0^1"
    )

    event = parse_price_event(raw)

    assert event == KISWebSocketPriceEvent(
        market_code="US_NASDAQ",
        stock_code="AAPL",
        price=148.01,
        tr_id="HDFSCNT0",
    )


def test_parse_price_event_reads_three_char_overseas_session_prefix() -> None:
    raw = (
        "0|HDFSCNT0|001|"
        "BAYIBM^IBM^2^20260309^20260309^093000^20260309^223000^"
        "0015000^0015100^0014900^0014801^5^0000199^00136^"
        "0014800^0014810^10^12^100^200^100000^30^70^120.0^1"
    )

    event = parse_price_event(raw)

    assert event == KISWebSocketPriceEvent(
        market_code="US_NYSE",
        stock_code="IBM",
        price=148.01,
        tr_id="HDFSCNT0",
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

    await client.subscribe("KR", "005930")

    assert ws.sent_json[0]["body"]["input"]["tr_key"] == "005930"


@pytest.mark.asyncio
async def test_subscribe_sends_overseas_market_prefix_in_key() -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    ws = _FakeWebSocket(messages=[])
    client = KISWebSocketClient(
        broker=broker,
        connect=lambda _url: _FakeConnect(ws),
        ws_url="ws://example.test/tryitout",
        retry_delay_seconds=0.0,
    )
    client._ws = ws

    await client.subscribe("US_NASDAQ", "AAPL")

    assert ws.sent_json[0]["body"]["input"]["tr_id"] == "HDFSCNT0"
    assert ws.sent_json[0]["body"]["input"]["tr_key"] == "DNASAAPL"


@pytest.mark.asyncio
async def test_subscribe_logs_us_subscription_action(caplog: pytest.LogCaptureFixture) -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    ws = _FakeWebSocket(messages=[])
    client = KISWebSocketClient(
        broker=broker,
        connect=lambda _url: _FakeConnect(ws),
        ws_url="ws://example.test/tryitout",
        retry_delay_seconds=0.0,
    )
    client._ws = ws
    caplog.set_level(logging.INFO)

    await client.subscribe("US_NASDAQ", "AAPL")

    assert "action=subscribe" in caplog.text
    assert "market=US_NASDAQ" in caplog.text
    assert "stock=AAPL" in caplog.text


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

    await client.subscribe("KR", "005930")
    await client.subscribe("KR", "005930")

    assert len(ws.sent_json) == 1


@pytest.mark.asyncio
async def test_subscribe_rejects_invalid_market_without_poisoning_state() -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    client = KISWebSocketClient(
        broker=broker,
        connect=lambda _url: _FakeConnect(_FakeWebSocket(messages=[])),
        ws_url="ws://example.test/tryitout",
        retry_delay_seconds=0.0,
    )

    with pytest.raises(ValueError, match="unsupported realtime websocket market: US_OTC"):
        await client.subscribe("US_OTC", "TQQQ")

    assert client._subscriptions == set()


@pytest.mark.asyncio
async def test_unsubscribe_removes_overseas_subscription_and_sends_unsubscribe() -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    ws = _FakeWebSocket(messages=[])
    client = KISWebSocketClient(
        broker=broker,
        connect=lambda _url: _FakeConnect(ws),
        ws_url="ws://example.test/tryitout",
        retry_delay_seconds=0.0,
    )
    client._ws = ws

    await client.subscribe("US_NASDAQ", "AAPL")
    await client.unsubscribe("US_NASDAQ", "AAPL")

    assert client._subscriptions == set()
    assert ws.sent_json[-1]["body"]["input"]["tr_key"] == "DNASAAPL"
    assert ws.sent_json[-1]["header"]["tr_type"] == "0"


@pytest.mark.asyncio
async def test_unsubscribe_logs_us_unsubscribe_action(caplog: pytest.LogCaptureFixture) -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    ws = _FakeWebSocket(messages=[])
    client = KISWebSocketClient(
        broker=broker,
        connect=lambda _url: _FakeConnect(ws),
        ws_url="ws://example.test/tryitout",
        retry_delay_seconds=0.0,
    )
    client._ws = ws
    await client.subscribe("US_NASDAQ", "AAPL")
    caplog.set_level(logging.INFO)

    await client.unsubscribe("US_NASDAQ", "AAPL")

    assert "action=unsubscribe" in caplog.text
    assert "market=US_NASDAQ" in caplog.text
    assert "stock=AAPL" in caplog.text


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
    await client.subscribe("KR", "005930")

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
async def test_run_reconnects_and_resubscribes_overseas_symbols_without_double_prefix() -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    first_ws = _FakeWebSocket(messages=[RuntimeError("boom")])
    second_ws = _FakeWebSocket(messages=[])
    queue = [_FakeConnect(first_ws), _FakeConnect(second_ws)]

    def connect(_url: str) -> _FakeConnect:
        return queue.pop(0)

    client = KISWebSocketClient(
        broker=broker,
        connect=connect,
        ws_url="ws://example.test/tryitout",
        retry_delay_seconds=0.0,
        max_retries=2,
    )
    await client.subscribe("US_NASDAQ", "AAPL")

    task = asyncio.create_task(client.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    client.request_stop()
    await task

    assert client._subscriptions == {("US_NASDAQ", "AAPL")}
    assert first_ws.sent_json[0]["body"]["input"]["tr_key"] == "DNASAAPL"
    assert second_ws.sent_json[0]["body"]["input"]["tr_key"] == "DNASAAPL"


@pytest.mark.asyncio
async def test_run_logs_connect_action(caplog: pytest.LogCaptureFixture) -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    ws = _FakeWebSocket(messages=[])
    client = KISWebSocketClient(
        broker=broker,
        connect=lambda _url: _FakeConnect(ws),
        ws_url="ws://example.test/custom-path",
        retry_delay_seconds=0.0,
        max_retries=1,
    )
    caplog.set_level(logging.INFO)

    await client.run()

    assert "action=connect" in caplog.text
    assert "ws://example.test/custom-path" in caplog.text


@pytest.mark.asyncio
async def test_run_logs_us_resubscribe_action_per_symbol(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    first_ws = _FakeWebSocket(messages=[RuntimeError("boom")])
    second_ws = _FakeWebSocket(messages=[])
    queue = [_FakeConnect(first_ws), _FakeConnect(second_ws)]

    def connect(_url: str) -> _FakeConnect:
        return queue.pop(0)

    client = KISWebSocketClient(
        broker=broker,
        connect=connect,
        ws_url="ws://example.test/tryitout",
        retry_delay_seconds=0.0,
        max_retries=2,
    )
    await client.subscribe("US_NASDAQ", "AAPL")
    caplog.set_level(logging.INFO)

    task = asyncio.create_task(client.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    client.request_stop()
    await task

    assert "action=resubscribe" in caplog.text
    assert "market=US_NASDAQ" in caplog.text
    assert "stock=AAPL" in caplog.text


@pytest.mark.asyncio
async def test_run_reconnect_logs_resubscription_market_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    first_ws = _FakeWebSocket(messages=[RuntimeError("boom")])
    second_ws = _FakeWebSocket(messages=[])
    queue = [_FakeConnect(first_ws), _FakeConnect(second_ws)]

    def connect(_url: str) -> _FakeConnect:
        return queue.pop(0)

    client = KISWebSocketClient(
        broker=broker,
        connect=connect,
        ws_url="ws://example.test/tryitout",
        retry_delay_seconds=0.0,
        max_retries=2,
    )
    await client.subscribe("US_NASDAQ", "AAPL")

    with caplog.at_level(logging.INFO):
        task = asyncio.create_task(client.run())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        client.request_stop()
        await task

    assert (
        "Resubscribing realtime websocket symbols count=1 "
        "subscriptions=US_NASDAQ:AAPL"
    ) in caplog.text


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
async def test_run_logs_ignored_us_parse_failure_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    malformed_us_payload = (
        "0|HDFSCNT0|001|"
        "XXXXAAPL^AAPL^4^20260309^20260309^093000^20260309^223000^"
        "001500000^001510000^001490000^001480100"
    )
    ws = _FakeWebSocket(messages=[malformed_us_payload])
    callback = AsyncMock()
    client = KISWebSocketClient(
        broker=broker,
        connect=lambda _url: _FakeConnect(ws),
        ws_url="ws://example.test/custom-path",
        retry_delay_seconds=0.0,
        max_retries=1,
        on_price=callback,
    )
    caplog.set_level(logging.INFO)

    await client.run()

    callback.assert_not_called()
    assert "action=ignore_us_parse_failure" in caplog.text
    assert "unknown overseas prefix=XXXX" in caplog.text


@pytest.mark.asyncio
async def test_run_logs_parsed_us_event_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = SimpleNamespace(get_websocket_approval_key=AsyncMock(return_value="approval-1"))
    raw = (
        "0|HDFSCNT0|001|"
        "DNASAAPL^AAPL^4^20260309^20260309^093000^20260309^223000^"
        "001500000^001510000^001490000^001480100^5^000019900^00136^"
        "001480000^001481000^10^12^100^200^100000^30^70^120.0^1"
    )
    ws = _FakeWebSocket(messages=[raw])
    callback = AsyncMock()
    client = KISWebSocketClient(
        broker=broker,
        connect=lambda _url: _FakeConnect(ws),
        ws_url="ws://example.test/custom-path",
        retry_delay_seconds=0.0,
        max_retries=1,
        on_price=callback,
    )
    caplog.set_level(logging.INFO)

    await client.run()

    callback.assert_awaited_once()
    assert "action=parsed_us_event" in caplog.text
    assert "market=US_NASDAQ" in caplog.text
    assert "stock=AAPL" in caplog.text


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
