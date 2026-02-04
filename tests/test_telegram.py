"""Tests for Telegram notification client."""

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from src.notifications.telegram_client import NotificationPriority, TelegramClient


class TestTelegramClientInit:
    """Test client initialization scenarios."""

    def test_disabled_via_flag(self) -> None:
        """Client disabled via enabled=False flag."""
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=False
        )
        assert client._enabled is False

    def test_disabled_missing_token(self) -> None:
        """Client disabled when bot_token is None."""
        client = TelegramClient(bot_token=None, chat_id="456", enabled=True)
        assert client._enabled is False

    def test_disabled_missing_chat_id(self) -> None:
        """Client disabled when chat_id is None."""
        client = TelegramClient(bot_token="123:abc", chat_id=None, enabled=True)
        assert client._enabled is False

    def test_enabled_with_credentials(self) -> None:
        """Client enabled when credentials provided."""
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True
        )
        assert client._enabled is True


class TestNotificationSending:
    """Test notification sending behavior."""

    @pytest.mark.asyncio
    async def test_no_send_when_disabled(self) -> None:
        """Notifications not sent when client disabled."""
        client = TelegramClient(enabled=False)

        with patch("aiohttp.ClientSession.post") as mock_post:
            await client.notify_trade_execution(
                stock_code="AAPL",
                market="United States",
                action="BUY",
                quantity=10,
                price=150.0,
                confidence=85.0,
            )
            mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_trade_execution_format(self) -> None:
        """Trade notification has correct format."""
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_trade_execution(
                stock_code="TSLA",
                market="United States",
                action="SELL",
                quantity=5,
                price=250.50,
                confidence=92.0,
            )

            # Verify API call was made
            assert mock_post.call_count == 1
            call_args = mock_post.call_args

            # Check payload structure
            payload = call_args.kwargs["json"]
            assert payload["chat_id"] == "456"
            assert "TSLA" in payload["text"]
            assert "SELL" in payload["text"]
            assert "5" in payload["text"]
            assert "250.50" in payload["text"]
            assert "92%" in payload["text"]

    @pytest.mark.asyncio
    async def test_circuit_breaker_priority(self) -> None:
        """Circuit breaker uses CRITICAL priority."""
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_circuit_breaker(pnl_pct=-3.15, threshold=-3.0)

            payload = mock_post.call_args.kwargs["json"]
            # CRITICAL priority has 🚨 emoji
            assert NotificationPriority.CRITICAL.emoji in payload["text"]
            assert "-3.15%" in payload["text"]

    @pytest.mark.asyncio
    async def test_api_error_handling(self) -> None:
        """API errors logged but don't crash."""
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True
        )

        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value="Bad Request")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            # Should not raise exception
            await client.notify_system_start(mode="paper", enabled_markets=["KR"])

    @pytest.mark.asyncio
    async def test_timeout_handling(self) -> None:
        """Timeouts logged but don't crash."""
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True
        )

        with patch(
            "aiohttp.ClientSession.post",
            side_effect=aiohttp.ClientError("Connection timeout"),
        ):
            # Should not raise exception
            await client.notify_error(
                error_type="Test Error", error_msg="Test", context="test"
            )

    @pytest.mark.asyncio
    async def test_session_management(self) -> None:
        """Session created and reused correctly."""
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True
        )

        # Session should be None initially
        assert client._session is None

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            await client.notify_market_open("Korea")
            # Session should be created
            assert client._session is not None

            session1 = client._session
            await client.notify_market_close("Korea", 1.5)
            # Same session should be reused
            assert client._session is session1


class TestRateLimiting:
    """Test rate limiter behavior."""

    @pytest.mark.asyncio
    async def test_rate_limiter_enforced(self) -> None:
        """Rate limiter delays rapid requests."""
        import time

        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True, rate_limit=2.0
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            start = time.monotonic()

            # Send 3 messages (rate: 2/sec = 0.5s per message)
            await client.notify_market_open("Korea")
            await client.notify_market_open("United States")
            await client.notify_market_open("Japan")

            elapsed = time.monotonic() - start

            # Should take at least 0.4 seconds (3 msgs at 2/sec with some tolerance)
            assert elapsed >= 0.4


class TestMessagePriorities:
    """Test priority-based messaging."""

    @pytest.mark.asyncio
    async def test_low_priority_uses_info_emoji(self) -> None:
        """LOW priority uses ℹ️ emoji."""
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_market_open("Korea")

            payload = mock_post.call_args.kwargs["json"]
            assert NotificationPriority.LOW.emoji in payload["text"]

    @pytest.mark.asyncio
    async def test_critical_priority_uses_alarm_emoji(self) -> None:
        """CRITICAL priority uses 🚨 emoji."""
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True
        )

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_system_shutdown("Circuit breaker tripped")

            payload = mock_post.call_args.kwargs["json"]
            assert NotificationPriority.CRITICAL.emoji in payload["text"]


class TestClientCleanup:
    """Test client cleanup behavior."""

    @pytest.mark.asyncio
    async def test_close_closes_session(self) -> None:
        """close() closes the HTTP session."""
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True
        )

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session

        await client.close()
        mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_handles_no_session(self) -> None:
        """close() handles None session gracefully."""
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True
        )

        # Should not raise exception
        await client.close()
