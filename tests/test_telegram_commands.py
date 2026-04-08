"""Tests for Telegram command handler."""

from unittest.mock import AsyncMock, patch

import pytest

from src.notifications.telegram_client import TelegramClient, TelegramCommandHandler


class TestCommandHandlerInit:
    """Test command handler initialization."""

    def test_init_with_client(self) -> None:
        """Handler initializes with TelegramClient."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        assert handler._client is client
        assert handler._polling_interval == 1.0
        assert handler._commands == {}
        assert handler._running is False

    def test_custom_polling_interval(self) -> None:
        """Handler accepts custom polling interval."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client, polling_interval=2.5)

        assert handler._polling_interval == 2.5


class TestCommandRegistration:
    """Test command registration."""

    @pytest.mark.asyncio
    async def test_register_command(self) -> None:
        """Commands can be registered."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        async def test_handler() -> None:
            pass

        handler.register_command("test", test_handler)

        assert "test" in handler._commands
        assert handler._commands["test"] is test_handler

    @pytest.mark.asyncio
    async def test_register_multiple_commands(self) -> None:
        """Multiple commands can be registered."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        async def handler1() -> None:
            pass

        async def handler2() -> None:
            pass

        handler.register_command("start", handler1)
        handler.register_command("help", handler2)

        assert len(handler._commands) == 2
        assert handler._commands["start"] is handler1
        assert handler._commands["help"] is handler2


class TestPollingLifecycle:
    """Test polling start/stop."""

    @pytest.mark.asyncio
    async def test_start_polling(self) -> None:
        """Polling can be started."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        with patch.object(handler, "_poll_loop", new_callable=AsyncMock):
            await handler.start_polling()

            assert handler._running is True
            assert handler._polling_task is not None

        await handler.stop_polling()

    @pytest.mark.asyncio
    async def test_start_polling_disabled_client(self) -> None:
        """Polling not started when client disabled."""
        client = TelegramClient(enabled=False)
        handler = TelegramCommandHandler(client)

        await handler.start_polling()

        assert handler._running is False
        assert handler._polling_task is None

    @pytest.mark.asyncio
    async def test_stop_polling(self) -> None:
        """Polling can be stopped."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        with patch.object(handler, "_poll_loop", new_callable=AsyncMock):
            await handler.start_polling()
            await handler.stop_polling()

            assert handler._running is False

    @pytest.mark.asyncio
    async def test_double_start_ignored(self) -> None:
        """Starting already running handler is ignored."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        with patch.object(handler, "_poll_loop", new_callable=AsyncMock):
            await handler.start_polling()
            task1 = handler._polling_task

            await handler.start_polling()  # Second start
            task2 = handler._polling_task

            # Should be the same task
            assert task1 is task2

        await handler.stop_polling()


class TestUpdateHandling:
    """Test update parsing and handling."""

    @pytest.mark.asyncio
    async def test_handle_valid_command(self) -> None:
        """Valid commands are executed."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        executed = False

        async def test_command() -> None:
            nonlocal executed
            executed = True

        handler.register_command("test", test_command)

        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 456},
                "text": "/test",
            },
        }

        await handler._handle_update(update)
        assert executed is True

    @pytest.mark.asyncio
    async def test_handle_unknown_command(self) -> None:
        """Unknown commands send help message."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/unknown",
                },
            }

            await handler._handle_update(update)

            # Should send error message
            assert mock_post.call_count == 1
            payload = mock_post.call_args.kwargs["json"]
            assert "Unknown command" in payload["text"]
            assert "/unknown" in payload["text"]

    @pytest.mark.asyncio
    async def test_ignore_unauthorized_chat(self) -> None:
        """Commands from unauthorized chats are ignored."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        executed = False

        async def test_command() -> None:
            nonlocal executed
            executed = True

        handler.register_command("test", test_command)

        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 999},  # Wrong chat_id
                "text": "/test",
            },
        }

        await handler._handle_update(update)
        assert executed is False

    @pytest.mark.asyncio
    async def test_ignore_non_command_text(self) -> None:
        """Non-command text is ignored."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        executed = False

        async def test_command() -> None:
            nonlocal executed
            executed = True

        handler.register_command("test", test_command)

        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 456},
                "text": "Hello, not a command",
            },
        }

        await handler._handle_update(update)
        assert executed is False

    @pytest.mark.asyncio
    async def test_handle_command_with_botname(self) -> None:
        """Commands with @botname suffix are handled correctly."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        executed = False

        async def test_command() -> None:
            nonlocal executed
            executed = True

        handler.register_command("start", test_command)

        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 456},
                "text": "/start@mybot",
            },
        }

        await handler._handle_update(update)
        assert executed is True

    @pytest.mark.asyncio
    async def test_handle_update_error_isolation(self) -> None:
        """Errors in handlers don't crash the system."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        async def failing_command() -> None:
            raise ValueError("Test error")

        handler.register_command("fail", failing_command)

        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 456},
                "text": "/fail",
            },
        }

        # Should not raise exception
        await handler._handle_update(update)


class TestTradingControlCommands:
    """Test trading control commands."""

    @pytest.mark.asyncio
    async def test_stop_command_pauses_trading(self) -> None:
        """Stop command clears pause event."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        # Create mock pause event
        import asyncio

        pause_event = asyncio.Event()
        pause_event.set()  # Initially active

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_stop() -> None:
            """Mock /stop handler."""
            if not pause_event.is_set():
                await client.send_message("⏸️ Trading is already paused")
                return

            pause_event.clear()
            await client.send_message(
                "<b>⏸️ Trading Paused</b>\n\n"
                "All trading operations have been suspended.\n"
                "Use /resume to restart trading."
            )

        handler.register_command("stop", mock_stop)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/stop",
                },
            }

            await handler._handle_update(update)

            # Verify pause event was cleared
            assert not pause_event.is_set()

            # Verify message was sent
            assert mock_post.call_count == 1
            payload = mock_post.call_args.kwargs["json"]
            assert "Trading Paused" in payload["text"]

    @pytest.mark.asyncio
    async def test_resume_command_resumes_trading(self) -> None:
        """Resume command sets pause event."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        # Create mock pause event (initially paused)
        import asyncio

        pause_event = asyncio.Event()
        pause_event.clear()  # Initially paused

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_resume() -> None:
            """Mock /resume handler."""
            if pause_event.is_set():
                await client.send_message("▶️ Trading is already active")
                return

            pause_event.set()
            await client.send_message(
                "<b>▶️ Trading Resumed</b>\n\nTrading operations have been restarted."
            )

        handler.register_command("resume", mock_resume)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/resume",
                },
            }

            await handler._handle_update(update)

            # Verify pause event was set
            assert pause_event.is_set()

            # Verify message was sent
            assert mock_post.call_count == 1
            payload = mock_post.call_args.kwargs["json"]
            assert "Trading Resumed" in payload["text"]

    @pytest.mark.asyncio
    async def test_stop_when_already_paused(self) -> None:
        """Stop command when already paused sends appropriate message."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        # Create mock pause event (already paused)
        import asyncio

        pause_event = asyncio.Event()
        pause_event.clear()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_stop() -> None:
            """Mock /stop handler."""
            if not pause_event.is_set():
                await client.send_message("⏸️ Trading is already paused")
                return

            pause_event.clear()

        handler.register_command("stop", mock_stop)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/stop",
                },
            }

            await handler._handle_update(update)

            # Verify message was sent
            payload = mock_post.call_args.kwargs["json"]
            assert "already paused" in payload["text"]

    @pytest.mark.asyncio
    async def test_resume_when_already_active(self) -> None:
        """Resume command when already active sends appropriate message."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        # Create mock pause event (already active)
        import asyncio

        pause_event = asyncio.Event()
        pause_event.set()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_resume() -> None:
            """Mock /resume handler."""
            if pause_event.is_set():
                await client.send_message("▶️ Trading is already active")
                return

            pause_event.set()

        handler.register_command("resume", mock_resume)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/resume",
                },
            }

            await handler._handle_update(update)

            # Verify message was sent
            payload = mock_post.call_args.kwargs["json"]
            assert "already active" in payload["text"]


class TestStatusCommands:
    """Test status query commands."""

    @pytest.mark.asyncio
    async def test_status_command_shows_trading_info(self) -> None:
        """Status command displays mode, markets, and P&L."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_status() -> None:
            """Mock /status handler."""
            message = (
                "<b>📊 Trading Status</b>\n\n"
                "<b>Mode:</b> PAPER\n"
                "<b>Markets:</b> Korea, United States\n"
                "<b>Trading:</b> Active\n\n"
                "<b>Current P&L:</b> +2.50%\n"
                "<b>Circuit Breaker:</b> -3.0%"
            )
            await client.send_message(message)

        handler.register_command("status", mock_status)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/status",
                },
            }

            await handler._handle_update(update)

            # Verify message was sent
            assert mock_post.call_count == 1
            payload = mock_post.call_args.kwargs["json"]
            assert "Trading Status" in payload["text"]
            assert "PAPER" in payload["text"]
            assert "P&L" in payload["text"]

    @pytest.mark.asyncio
    async def test_status_command_error_handling(self) -> None:
        """Status command handles errors gracefully."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_status_error() -> None:
            """Mock /status handler with error."""
            await client.send_message("<b>⚠️ Error</b>\n\nFailed to retrieve trading status.")

        handler.register_command("status", mock_status_error)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/status",
                },
            }

            await handler._handle_update(update)

            # Should send error message
            payload = mock_post.call_args.kwargs["json"]
            assert "Error" in payload["text"]

    @pytest.mark.asyncio
    async def test_positions_command_shows_holdings(self) -> None:
        """Positions command displays account summary."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_positions() -> None:
            """Mock /positions handler."""
            message = (
                "<b>💼 Account Summary</b>\n\n"
                "<b>Total Evaluation:</b> ₩10,500,000\n"
                "<b>Available Cash:</b> ₩5,000,000\n"
                "<b>Purchase Total:</b> ₩10,000,000\n"
                "<b>P&L:</b> +5.00%\n\n"
                "<i>Note: Individual position details require API enhancement</i>"
            )
            await client.send_message(message)

        handler.register_command("positions", mock_positions)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/positions",
                },
            }

            await handler._handle_update(update)

            # Verify message was sent
            assert mock_post.call_count == 1
            payload = mock_post.call_args.kwargs["json"]
            assert "Account Summary" in payload["text"]
            assert "Total Evaluation" in payload["text"]
            assert "P&L" in payload["text"]

    @pytest.mark.asyncio
    async def test_positions_command_empty_holdings(self) -> None:
        """Positions command handles empty portfolio."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_positions_empty() -> None:
            """Mock /positions handler with no positions."""
            message = "<b>💼 Account Summary</b>\n\nNo balance information available."
            await client.send_message(message)

        handler.register_command("positions", mock_positions_empty)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/positions",
                },
            }

            await handler._handle_update(update)

            # Verify message was sent
            payload = mock_post.call_args.kwargs["json"]
            assert "No balance information available" in payload["text"]

    @pytest.mark.asyncio
    async def test_positions_command_error_handling(self) -> None:
        """Positions command handles errors gracefully."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_positions_error() -> None:
            """Mock /positions handler with error."""
            await client.send_message("<b>⚠️ Error</b>\n\nFailed to retrieve positions.")

        handler.register_command("positions", mock_positions_error)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/positions",
                },
            }

            await handler._handle_update(update)

            # Should send error message
            payload = mock_post.call_args.kwargs["json"]
            assert "Error" in payload["text"]


class TestBasicCommands:
    """Test basic command implementations."""

    @pytest.mark.asyncio
    async def test_help_command_content(self) -> None:
        """Help command lists all available commands."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_help() -> None:
            """Mock /help handler."""
            message = (
                "<b>📖 Available Commands</b>\n\n"
                "/help - Show available commands\n"
                "/status - Trading status (mode, markets, P&L)\n"
                "/positions - Current holdings\n"
                "/report - Daily summary report\n"
                "/scenarios - Today's playbook scenarios\n"
                "/review - Recent scorecards\n"
                "/dashboard - Dashboard URL/status\n"
                "/stop - Pause trading\n"
                "/resume - Resume trading"
            )
            await client.send_message(message)

        handler.register_command("help", mock_help)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/help",
                },
            }

            await handler._handle_update(update)

            # Verify message was sent
            assert mock_post.call_count == 1
            payload = mock_post.call_args.kwargs["json"]
            assert "Available Commands" in payload["text"]
            assert "/help" in payload["text"]
            assert "/status" in payload["text"]
            assert "/positions" in payload["text"]
            assert "/report" in payload["text"]
            assert "/scenarios" in payload["text"]
            assert "/review" in payload["text"]
            assert "/dashboard" in payload["text"]
            assert "/stop" in payload["text"]
            assert "/resume" in payload["text"]


class TestExtendedCommands:
    """Test additional bot commands."""

    @pytest.mark.asyncio
    async def test_report_command(self) -> None:
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_report() -> None:
            await client.send_message("<b>📈 Daily Report</b>\n\nTrades: 1")

        handler.register_command("report", mock_report)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await handler._handle_update(
                {"update_id": 1, "message": {"chat": {"id": 456}, "text": "/report"}}
            )
            payload = mock_post.call_args.kwargs["json"]
            assert "Daily Report" in payload["text"]

    @pytest.mark.asyncio
    async def test_scenarios_command(self) -> None:
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_scenarios() -> None:
            await client.send_message("<b>🧠 Today's Scenarios</b>\n\n- AAPL: BUY (85)")

        handler.register_command("scenarios", mock_scenarios)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await handler._handle_update(
                {"update_id": 1, "message": {"chat": {"id": 456}, "text": "/scenarios"}}
            )
            payload = mock_post.call_args.kwargs["json"]
            assert "Today's Scenarios" in payload["text"]

    @pytest.mark.asyncio
    async def test_review_command(self) -> None:
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_review() -> None:
            await client.send_message("<b>📝 Recent Reviews</b>\n\n- 2026-02-14 KR")

        handler.register_command("review", mock_review)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await handler._handle_update(
                {"update_id": 1, "message": {"chat": {"id": 456}, "text": "/review"}}
            )
            payload = mock_post.call_args.kwargs["json"]
            assert "Recent Reviews" in payload["text"]

    @pytest.mark.asyncio
    async def test_dashboard_command(self) -> None:
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_dashboard() -> None:
            await client.send_message("<b>🖥️ Dashboard</b>\n\nURL: http://127.0.0.1:8080")

        handler.register_command("dashboard", mock_dashboard)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            await handler._handle_update(
                {"update_id": 1, "message": {"chat": {"id": 456}, "text": "/dashboard"}}
            )
            payload = mock_post.call_args.kwargs["json"]
            assert "Dashboard" in payload["text"]


class TestGetUpdates:
    """Test getUpdates API interaction."""

    @pytest.mark.asyncio
    async def test_get_updates_success(self) -> None:
        """getUpdates fetches and parses updates."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"text": "/test"}},
                    {"update_id": 2, "message": {"text": "/help"}},
                ],
            }
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            updates = await handler._get_updates()

            assert len(updates) == 2
            assert updates[0]["update_id"] == 1
            assert updates[1]["update_id"] == 2
            assert handler._last_update_id == 2

    @pytest.mark.asyncio
    async def test_get_updates_api_error(self) -> None:
        """getUpdates handles API errors gracefully."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value="Bad Request")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            updates = await handler._get_updates()

            assert updates == []

    @pytest.mark.asyncio
    async def test_get_updates_empty_result(self) -> None:
        """getUpdates handles empty results."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"ok": True, "result": []})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            updates = await handler._get_updates()

            assert updates == []

    @pytest.mark.asyncio
    async def test_get_updates_409_stops_polling(self) -> None:
        """409 Conflict response stops the poller (_running = False) and returns empty list."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)
        handler._running = True  # simulate active poller

        mock_resp = AsyncMock()
        mock_resp.status = 409
        mock_resp.text = AsyncMock(
            return_value='{"ok":false,"error_code":409,"description":"Conflict"}'
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            updates = await handler._get_updates()

        assert updates == []
        assert handler._running is False  # poller stopped

    @pytest.mark.asyncio
    async def test_poll_loop_exits_after_409(self) -> None:
        """_poll_loop exits naturally after _running is set to False by a 409 response."""
        import asyncio as _asyncio

        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        call_count = 0

        async def mock_get_updates_409() -> list[dict]:
            nonlocal call_count
            call_count += 1
            # Simulate 409 stopping the poller
            handler._running = False
            return []

        handler._get_updates = mock_get_updates_409  # type: ignore[method-assign]

        handler._running = True
        task = _asyncio.create_task(handler._poll_loop())
        await _asyncio.wait_for(task, timeout=2.0)

        # _get_updates called exactly once, then loop exited
        assert call_count == 1
        assert handler._running is False

    @pytest.mark.asyncio
    async def test_get_updates_429_sleeps_retry_after_and_returns_empty(self) -> None:
        """429 rate-limit response honoured: sleeps for retry_after seconds, returns []."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 429
        mock_resp.text = AsyncMock(
            return_value='{"ok":false,"error_code":429,"description":"Too Many Requests: retry after 7","parameters":{"retry_after":7}}'
        )
        mock_resp.json = AsyncMock(
            return_value={
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests: retry after 7",
                "parameters": {"retry_after": 7},
            }
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        with (
            patch("aiohttp.ClientSession.post", return_value=mock_resp),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            updates = await handler._get_updates()

        assert updates == []
        assert 7 in slept, f"Expected retry_after=7 sleep, got {slept}"

    @pytest.mark.asyncio
    async def test_get_updates_502_logs_warning_not_error(self, caplog) -> None:
        """502 Bad Gateway is logged as WARNING (transient), not ERROR."""
        import logging

        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 502
        mock_resp.text = AsyncMock(return_value='{"ok":false,"error_code":502}')
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("aiohttp.ClientSession.post", return_value=mock_resp),
            caplog.at_level(logging.WARNING),
        ):
            updates = await handler._get_updates()

        assert updates == []
        assert any("502" in r.message and r.levelname == "WARNING" for r in caplog.records)
        assert not any(r.levelname == "ERROR" for r in caplog.records)


class TestCommandWithArgs:
    """Test register_command_with_args and argument dispatch."""

    def test_register_command_with_args_stored(self) -> None:
        """register_command_with_args stores handler in _commands_with_args."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        async def my_handler(args: list[str]) -> None:
            pass

        handler.register_command_with_args("notify", my_handler)
        assert "notify" in handler._commands_with_args
        assert handler._commands_with_args["notify"] is my_handler

    @pytest.mark.asyncio
    async def test_args_handler_receives_arguments(self) -> None:
        """Args handler is called with the trailing tokens."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        received: list[list[str]] = []

        async def capture(args: list[str]) -> None:
            received.append(args)

        handler.register_command_with_args("notify", capture)

        update = {
            "message": {
                "chat": {"id": "456"},
                "text": "/notify scenario off",
            }
        }
        await handler._handle_update(update)
        assert received == [["scenario", "off"]]

    @pytest.mark.asyncio
    async def test_args_handler_takes_priority_over_no_args_handler(self) -> None:
        """When both handlers exist for same command, args handler wins."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        no_args_called = []
        args_called = []

        async def no_args_handler() -> None:
            no_args_called.append(True)

        async def args_handler(args: list[str]) -> None:
            args_called.append(args)

        handler.register_command("notify", no_args_handler)
        handler.register_command_with_args("notify", args_handler)

        update = {
            "message": {
                "chat": {"id": "456"},
                "text": "/notify all off",
            }
        }
        await handler._handle_update(update)
        assert args_called == [["all", "off"]]
        assert no_args_called == []

    @pytest.mark.asyncio
    async def test_args_handler_with_no_trailing_args(self) -> None:
        """/notify with no args still dispatches to args handler with empty list."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        received: list[list[str]] = []

        async def capture(args: list[str]) -> None:
            received.append(args)

        handler.register_command_with_args("notify", capture)

        update = {
            "message": {
                "chat": {"id": "456"},
                "text": "/notify",
            }
        }
        await handler._handle_update(update)
        assert received == [[]]
