"""Tests for Telegram notification client."""

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from src.notifications.telegram_client import (
    NotificationFilter,
    NotificationPriority,
    TelegramClient,
)


class TestTelegramClientInit:
    """Test client initialization scenarios."""

    def test_disabled_via_flag(self) -> None:
        """Client disabled via enabled=False flag."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=False)
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
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        assert client._enabled is True


class TestNotificationSending:
    """Test notification sending behavior."""

    @pytest.mark.asyncio
    async def test_send_message_success(self) -> None:
        """send_message returns True on successful send."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            result = await client.send_message("Test message")

            assert result is True
            assert mock_post.call_count == 1

            payload = mock_post.call_args.kwargs["json"]
            assert payload["chat_id"] == "456"
            assert payload["text"] == "Test message"
            assert payload["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_send_message_disabled_client(self) -> None:
        """send_message returns False when client disabled."""
        client = TelegramClient(enabled=False)

        with patch("aiohttp.ClientSession.post") as mock_post:
            result = await client.send_message("Test message")

            assert result is False
            mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_message_api_error(self) -> None:
        """send_message returns False on API error."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value="Bad Request")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            result = await client.send_message("Test message")
            assert result is False

    @pytest.mark.asyncio
    async def test_send_message_with_markdown(self) -> None:
        """send_message supports different parse modes."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            result = await client.send_message("*bold*", parse_mode="Markdown")

            assert result is True
            payload = mock_post.call_args.kwargs["json"]
            assert payload["parse_mode"] == "Markdown"

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
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

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
    async def test_playbook_generated_format(self) -> None:
        """Playbook generated notification has expected fields."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_playbook_generated(
                market="KR",
                stock_count=4,
                scenario_count=12,
                token_count=980,
            )

            payload = mock_post.call_args.kwargs["json"]
            assert "Playbook Generated" in payload["text"]
            assert "Market: KR" in payload["text"]
            assert "Stocks: 4" in payload["text"]
            assert "Scenarios: 12" in payload["text"]
            assert "Tokens: 980" in payload["text"]

    @pytest.mark.asyncio
    async def test_scenario_matched_format(self) -> None:
        """Scenario matched notification has expected fields."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_scenario_matched(
                stock_code="AAPL",
                action="BUY",
                condition_summary="RSI < 30, volume_ratio > 2.0",
                confidence=88.2,
            )

            payload = mock_post.call_args.kwargs["json"]
            assert "Scenario Matched" in payload["text"]
            assert "AAPL" in payload["text"]
            assert "Action: BUY" in payload["text"]
            assert "RSI < 30" in payload["text"]
            assert "88%" in payload["text"]

    @pytest.mark.asyncio
    async def test_playbook_failed_format(self) -> None:
        """Playbook failed notification has expected fields."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_playbook_failed(
                market="US",
                reason="Gemini timeout",
            )

            payload = mock_post.call_args.kwargs["json"]
            assert "Playbook Failed" in payload["text"]
            assert "Market: US" in payload["text"]
            assert "Gemini timeout" in payload["text"]

    @pytest.mark.asyncio
    async def test_circuit_breaker_priority(self) -> None:
        """Circuit breaker uses CRITICAL priority."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

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
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

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
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        with patch(
            "aiohttp.ClientSession.post",
            side_effect=aiohttp.ClientError("Connection timeout"),
        ):
            # Should not raise exception
            await client.notify_error(error_type="Test Error", error_msg="Test", context="test")

    @pytest.mark.asyncio
    async def test_session_management(self) -> None:
        """Session created and reused correctly."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

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

        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True, rate_limit=2.0)

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
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

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
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_system_shutdown("Circuit breaker tripped")

            payload = mock_post.call_args.kwargs["json"]
            assert NotificationPriority.CRITICAL.emoji in payload["text"]

    @pytest.mark.asyncio
    async def test_playbook_generated_priority(self) -> None:
        """Playbook generated uses MEDIUM priority emoji."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_playbook_generated(
                market="KR",
                stock_count=2,
                scenario_count=4,
                token_count=123,
            )

            payload = mock_post.call_args.kwargs["json"]
            assert NotificationPriority.MEDIUM.emoji in payload["text"]

    @pytest.mark.asyncio
    async def test_playbook_failed_priority(self) -> None:
        """Playbook failed uses HIGH priority emoji."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_playbook_failed(
                market="KR",
                reason="Invalid JSON",
            )

            payload = mock_post.call_args.kwargs["json"]
            assert NotificationPriority.HIGH.emoji in payload["text"]

    @pytest.mark.asyncio
    async def test_scenario_matched_priority(self) -> None:
        """Scenario matched uses HIGH priority emoji."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_scenario_matched(
                stock_code="AAPL",
                action="BUY",
                condition_summary="RSI < 30",
                confidence=80.0,
            )

            payload = mock_post.call_args.kwargs["json"]
            assert NotificationPriority.HIGH.emoji in payload["text"]


class TestClientCleanup:
    """Test client cleanup behavior."""

    @pytest.mark.asyncio
    async def test_close_closes_session(self) -> None:
        """close() closes the HTTP session."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        mock_session = AsyncMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        client._session = mock_session

        await client.close()
        mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_handles_no_session(self) -> None:
        """close() handles None session gracefully."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)

        # Should not raise exception
        await client.close()


class TestNotificationFilter:
    """Test granular notification filter behavior."""

    def test_default_filter_allows_all(self) -> None:
        """Default NotificationFilter has all flags enabled."""
        f = NotificationFilter()
        assert f.trades is True
        assert f.market_open_close is True
        assert f.fat_finger is True
        assert f.system_events is True
        assert f.playbook is True
        assert f.scenario_match is True
        assert f.errors is True

    def test_client_uses_default_filter_when_none_given(self) -> None:
        """TelegramClient creates a default NotificationFilter when none provided."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        assert isinstance(client._filter, NotificationFilter)
        assert client._filter.scenario_match is True

    def test_client_stores_provided_filter(self) -> None:
        """TelegramClient stores a custom NotificationFilter."""
        nf = NotificationFilter(scenario_match=False, trades=False)
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True, notification_filter=nf
        )
        assert client._filter.scenario_match is False
        assert client._filter.trades is False
        assert client._filter.market_open_close is True  # default still True

    @pytest.mark.asyncio
    async def test_scenario_match_filtered_does_not_send(self) -> None:
        """notify_scenario_matched skips send when scenario_match=False."""
        nf = NotificationFilter(scenario_match=False)
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True, notification_filter=nf
        )
        with patch("aiohttp.ClientSession.post") as mock_post:
            await client.notify_scenario_matched(
                stock_code="005930", action="BUY", condition_summary="rsi<30", confidence=85.0
            )
            mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_trades_filtered_does_not_send(self) -> None:
        """notify_trade_execution skips send when trades=False."""
        nf = NotificationFilter(trades=False)
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True, notification_filter=nf
        )
        with patch("aiohttp.ClientSession.post") as mock_post:
            await client.notify_trade_execution(
                stock_code="005930",
                market="KR",
                action="BUY",
                quantity=10,
                price=70000.0,
                confidence=85.0,
            )
            mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_market_open_close_filtered_does_not_send(self) -> None:
        """notify_market_open/close skip send when market_open_close=False."""
        nf = NotificationFilter(market_open_close=False)
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True, notification_filter=nf
        )
        with patch("aiohttp.ClientSession.post") as mock_post:
            await client.notify_market_open("Korea")
            await client.notify_market_close("Korea", pnl_pct=1.5)
            mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_circuit_breaker_always_sends_regardless_of_filter(self) -> None:
        """notify_circuit_breaker always sends (no filter flag)."""
        nf = NotificationFilter(
            trades=False,
            market_open_close=False,
            fat_finger=False,
            system_events=False,
            playbook=False,
            scenario_match=False,
            errors=False,
        )
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True, notification_filter=nf
        )
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await client.notify_circuit_breaker(pnl_pct=-3.5, threshold=-3.0)
            assert mock_post.call_count == 1

    @pytest.mark.asyncio
    async def test_errors_filtered_does_not_send(self) -> None:
        """notify_error skips send when errors=False."""
        nf = NotificationFilter(errors=False)
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True, notification_filter=nf
        )
        with patch("aiohttp.ClientSession.post") as mock_post:
            await client.notify_error("TestError", "something went wrong", "KR")
            mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_playbook_filtered_does_not_send(self) -> None:
        """notify_playbook_generated/failed skip send when playbook=False."""
        nf = NotificationFilter(playbook=False)
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True, notification_filter=nf
        )
        with patch("aiohttp.ClientSession.post") as mock_post:
            await client.notify_playbook_generated("KR", 3, 10, 1200)
            await client.notify_playbook_failed("KR", "timeout")
            mock_post.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_events_filtered_does_not_send(self) -> None:
        """notify_system_start/shutdown skip send when system_events=False."""
        nf = NotificationFilter(system_events=False)
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True, notification_filter=nf
        )
        with patch("aiohttp.ClientSession.post") as mock_post:
            await client.notify_system_start("paper", ["KR"])
            await client.notify_system_shutdown("Normal shutdown")
            mock_post.assert_not_called()

    def test_set_flag_valid_key(self) -> None:
        """set_flag returns True and updates field for a known key."""
        nf = NotificationFilter()
        assert nf.set_flag("scenario", False) is True
        assert nf.scenario_match is False

    def test_set_flag_invalid_key(self) -> None:
        """set_flag returns False for an unknown key."""
        nf = NotificationFilter()
        assert nf.set_flag("unknown_key", False) is False

    def test_as_dict_keys_match_keys(self) -> None:
        """as_dict() returns every key defined in KEYS."""
        nf = NotificationFilter()
        d = nf.as_dict()
        assert set(d.keys()) == set(NotificationFilter.KEYS.keys())

    def test_set_notification_valid_key(self) -> None:
        """TelegramClient.set_notification toggles filter at runtime."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        assert client._filter.scenario_match is True
        assert client.set_notification("scenario", False) is True
        assert client._filter.scenario_match is False

    def test_set_notification_all_off(self) -> None:
        """set_notification('all', False) disables every filter flag."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        assert client.set_notification("all", False) is True
        for v in client.filter_status().values():
            assert v is False

    def test_set_notification_all_on(self) -> None:
        """set_notification('all', True) enables every filter flag."""
        client = TelegramClient(
            bot_token="123:abc",
            chat_id="456",
            enabled=True,
            notification_filter=NotificationFilter(
                trades=False,
                market_open_close=False,
                scenario_match=False,
                fat_finger=False,
                system_events=False,
                playbook=False,
                errors=False,
            ),
        )
        assert client.set_notification("all", True) is True
        for v in client.filter_status().values():
            assert v is True

    def test_set_notification_unknown_key(self) -> None:
        """set_notification returns False for an unknown key."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        assert client.set_notification("unknown", False) is False

    def test_filter_status_reflects_current_state(self) -> None:
        """filter_status() matches the current NotificationFilter state."""
        nf = NotificationFilter(trades=False, scenario_match=False)
        client = TelegramClient(
            bot_token="123:abc", chat_id="456", enabled=True, notification_filter=nf
        )
        status = client.filter_status()
        assert status["trades"] is False
        assert status["scenario"] is False
        assert status["market"] is True
