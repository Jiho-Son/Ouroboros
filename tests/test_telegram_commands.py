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
