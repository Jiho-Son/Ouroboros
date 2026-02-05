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
                "<b>▶️ Trading Resumed</b>\n\n"
                "Trading operations have been restarted."
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
            await client.send_message(
                "<b>⚠️ Error</b>\n\nFailed to retrieve trading status."
            )

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
        """Positions command displays current holdings."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_positions() -> None:
            """Mock /positions handler."""
            message = (
                "<b>💼 Current Holdings</b>\n"
                "\n🇰🇷 <b>Korea</b>\n"
                "• 005930: 10 shares @ 70,000\n"
                "\n🇺🇸 <b>Overseas</b>\n"
                "• AAPL: 15 shares @ 175\n"
                "\n<b>Cash:</b> ₩5,000,000"
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
            assert "Current Holdings" in payload["text"]
            assert "shares" in payload["text"]

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
            message = (
                "<b>💼 Current Holdings</b>\n\n"
                "No positions currently held."
            )
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
            assert "No positions" in payload["text"]

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
            await client.send_message(
                "<b>⚠️ Error</b>\n\nFailed to retrieve positions."
            )

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
    async def test_start_command_content(self) -> None:
        """Start command contains welcome message and command list."""
        client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
        handler = TelegramCommandHandler(client)

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        async def mock_start() -> None:
            """Mock /start handler."""
            message = (
                "<b>🤖 The Ouroboros Trading Bot</b>\n\n"
                "AI-powered global stock trading agent with real-time notifications.\n\n"
                "<b>Available commands:</b>\n"
                "/help - Show this help message\n"
                "/status - Current trading status\n"
                "/positions - View holdings\n"
                "/stop - Pause trading\n"
                "/resume - Resume trading"
            )
            await client.send_message(message)

        handler.register_command("start", mock_start)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            update = {
                "update_id": 1,
                "message": {
                    "chat": {"id": 456},
                    "text": "/start",
                },
            }

            await handler._handle_update(update)

            # Verify message was sent
            assert mock_post.call_count == 1
            payload = mock_post.call_args.kwargs["json"]
            assert "Ouroboros Trading Bot" in payload["text"]
            assert "/help" in payload["text"]
            assert "/status" in payload["text"]

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
                "/start - Welcome message\n"
                "/help - Show available commands\n"
                "/status - Trading status (mode, markets, P&L)\n"
                "/positions - Current holdings\n"
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
            assert "/start" in payload["text"]
            assert "/help" in payload["text"]
            assert "/status" in payload["text"]
            assert "/positions" in payload["text"]
            assert "/stop" in payload["text"]
            assert "/resume" in payload["text"]


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
