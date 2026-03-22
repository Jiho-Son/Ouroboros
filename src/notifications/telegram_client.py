"""Telegram notification client for real-time trading alerts."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

import aiohttp

logger = logging.getLogger(__name__)


class NotificationPriority(Enum):
    """Priority levels for notifications with emoji indicators."""

    LOW = ("ℹ️", "info")
    MEDIUM = ("📊", "medium")
    HIGH = ("⚠️", "warning")
    CRITICAL = ("🚨", "critical")

    def __init__(self, emoji: str, label: str) -> None:
        self.emoji = emoji
        self.label = label


class LeakyBucket:
    """Rate limiter using leaky bucket algorithm."""

    def __init__(self, rate: float, capacity: int = 1) -> None:
        """
        Initialize rate limiter.

        Args:
            rate: Maximum requests per second
            capacity: Bucket capacity (burst size)
        """
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_update = now

            if self._tokens < 1.0:
                wait_time = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait_time)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


@dataclass
class NotificationFilter:
    """Granular on/off flags for each notification type.

    circuit_breaker is intentionally omitted — it is always sent regardless.
    """

    # Maps user-facing command keys to dataclass field names
    KEYS: ClassVar[dict[str, str]] = {
        "trades": "trades",
        "market": "market_open_close",
        "fatfinger": "fat_finger",
        "system": "system_events",
        "playbook": "playbook",
        "scenario": "scenario_match",
        "errors": "errors",
    }

    trades: bool = True
    market_open_close: bool = True
    fat_finger: bool = True
    system_events: bool = True
    playbook: bool = True
    scenario_match: bool = True
    errors: bool = True

    def set_flag(self, key: str, value: bool) -> bool:
        """Set a filter flag by user-facing key. Returns False if key is unknown."""
        field = self.KEYS.get(key.lower())
        if field is None:
            return False
        setattr(self, field, value)
        return True

    def as_dict(self) -> dict[str, bool]:
        """Return {user_key: current_value} for display."""
        return {k: getattr(self, field) for k, field in self.KEYS.items()}


@dataclass
class NotificationMessage:
    """Internal notification message structure."""

    priority: NotificationPriority
    message: str


class TelegramClient:
    """Telegram Bot API client for sending trading notifications."""

    API_BASE = "https://api.telegram.org/bot{token}"
    DEFAULT_TIMEOUT = 5.0  # seconds
    DEFAULT_RATE = 1.0  # messages per second

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        enabled: bool = True,
        rate_limit: float = DEFAULT_RATE,
        notification_filter: NotificationFilter | None = None,
    ) -> None:
        """
        Initialize Telegram client.

        Args:
            bot_token: Telegram bot token from @BotFather
            chat_id: Target chat ID (user or group)
            enabled: Enable/disable notifications globally
            rate_limit: Maximum messages per second
            notification_filter: Granular per-type on/off flags
        """
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = enabled
        self._rate_limiter = LeakyBucket(rate=rate_limit)
        self._session: aiohttp.ClientSession | None = None
        self._filter = (
            notification_filter if notification_filter is not None else NotificationFilter()
        )

        if not enabled:
            logger.info("Telegram notifications disabled via configuration")
        elif bot_token is None or chat_id is None:
            logger.warning("Telegram notifications disabled (missing bot_token or chat_id)")
            self._enabled = False
        else:
            logger.info("Telegram notifications enabled for chat_id=%s", chat_id)

    def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.DEFAULT_TIMEOUT)
            )
        return self._session

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def set_notification(self, key: str, value: bool) -> bool:
        """Toggle a notification type by user-facing key at runtime.

        Args:
            key: User-facing key (e.g. "scenario", "market", "all")
            value: True to enable, False to disable

        Returns:
            True if key was valid, False if unknown.
        """
        if key == "all":
            for k in NotificationFilter.KEYS:
                self._filter.set_flag(k, value)
            return True
        return self._filter.set_flag(key, value)

    def filter_status(self) -> dict[str, bool]:
        """Return current per-type filter state keyed by user-facing names."""
        return self._filter.as_dict()

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Send a generic text message to Telegram.

        Args:
            text: Message text to send
            parse_mode: Parse mode for formatting (HTML or Markdown)

        Returns:
            True if message was sent successfully, False otherwise
        """
        if not self._enabled:
            return False

        try:
            await self._rate_limiter.acquire()

            url = f"{self.API_BASE.format(token=self._bot_token)}/sendMessage"
            payload = {
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }

            session = self._get_session()
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error("Telegram API error (status=%d): %s", resp.status, error_text)
                    return False
                logger.debug("Telegram message sent: %s", text[:50])
                return True

        except TimeoutError:
            logger.error("Telegram message timeout")
            return False
        except aiohttp.ClientError as exc:
            logger.error("Telegram message failed: %s", exc)
            return False
        except Exception as exc:
            logger.error("Unexpected error sending message: %s", exc)
            return False

    async def _send_notification(self, msg: NotificationMessage) -> None:
        """
        Send notification to Telegram with graceful degradation.

        Args:
            msg: Notification message to send
        """
        formatted_message = f"{msg.priority.emoji} {msg.message}"
        await self.send_message(formatted_message)

    @staticmethod
    def _format_trade_symbol(stock_code: str, stock_name: str | None = None) -> str:
        """Return the user-facing trade symbol label."""
        normalized_name = (stock_name or "").strip()
        if not normalized_name:
            return stock_code
        return f"{normalized_name}({stock_code})"

    async def notify_trade_execution(
        self,
        stock_code: str,
        market: str,
        action: str,
        quantity: int,
        price: float,
        confidence: float,
        stock_name: str | None = None,
    ) -> None:
        """
        Notify trade execution.

        Args:
            stock_code: Stock ticker symbol
            stock_name: Human-readable stock name
            market: Market name (e.g., "Korea", "United States")
            action: "BUY" or "SELL"
            quantity: Number of shares
            price: Execution price
            confidence: AI confidence level (0-100)
        """
        if not self._filter.trades:
            return
        emoji = "🟢" if action == "BUY" else "🔴"
        symbol_label = self._format_trade_symbol(stock_code, stock_name)
        message = (
            f"<b>{emoji} {action}</b>\n"
            f"Symbol: <code>{symbol_label}</code> ({market})\n"
            f"Quantity: {quantity:,} shares\n"
            f"Price: {price:,.2f}\n"
            f"Confidence: {confidence:.0f}%"
        )
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.MEDIUM, message=message)
        )

    async def notify_market_open(self, market_name: str) -> None:
        """
        Notify market opening.

        Args:
            market_name: Name of the market (e.g., "Korea", "United States")
        """
        if not self._filter.market_open_close:
            return
        message = f"<b>Market Open</b>\n{market_name} trading session started"
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.LOW, message=message)
        )

    async def notify_market_close(self, market_name: str, pnl_pct: float) -> None:
        """
        Notify market closing.

        Args:
            market_name: Name of the market
            pnl_pct: Final P&L percentage for the session
        """
        if not self._filter.market_open_close:
            return
        pnl_sign = "+" if pnl_pct >= 0 else ""
        pnl_emoji = "📈" if pnl_pct >= 0 else "📉"
        message = (
            f"<b>Market Close</b>\n"
            f"{market_name} trading session ended\n"
            f"{pnl_emoji} P&L: {pnl_sign}{pnl_pct:.2f}%"
        )
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.LOW, message=message)
        )

    async def notify_circuit_breaker(self, pnl_pct: float, threshold: float) -> None:
        """
        Notify circuit breaker activation.

        Args:
            pnl_pct: Current P&L percentage
            threshold: Circuit breaker threshold
        """
        message = (
            f"<b>CIRCUIT BREAKER TRIPPED</b>\n"
            f"P&L: {pnl_pct:.2f}% (threshold: {threshold:.1f}%)\n"
            f"Trading halted for safety"
        )
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.CRITICAL, message=message)
        )

    async def notify_fat_finger(
        self,
        stock_code: str,
        order_amount: float,
        total_cash: float,
        max_pct: float,
    ) -> None:
        """
        Notify fat-finger protection rejection.

        Args:
            stock_code: Stock ticker symbol
            order_amount: Attempted order amount
            total_cash: Total available cash
            max_pct: Maximum allowed percentage
        """
        if not self._filter.fat_finger:
            return
        attempted_pct = (order_amount / total_cash) * 100 if total_cash > 0 else 0
        message = (
            f"<b>Fat-Finger Protection</b>\n"
            f"Order rejected: <code>{stock_code}</code>\n"
            f"Attempted: {attempted_pct:.1f}% of cash\n"
            f"Max allowed: {max_pct:.0f}%\n"
            f"Amount: {order_amount:,.0f} / {total_cash:,.0f}"
        )
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.HIGH, message=message)
        )

    async def notify_system_start(self, mode: str, enabled_markets: list[str]) -> None:
        """
        Notify system startup.

        Args:
            mode: Trading mode ("paper" or "live")
            enabled_markets: List of enabled market codes
        """
        if not self._filter.system_events:
            return
        mode_emoji = "📝" if mode == "paper" else "💰"
        markets_str = ", ".join(enabled_markets)
        message = (
            f"<b>{mode_emoji} System Started</b>\nMode: {mode.upper()}\nMarkets: {markets_str}"
        )
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.MEDIUM, message=message)
        )

    async def notify_playbook_generated(
        self,
        market: str,
        stock_count: int,
        scenario_count: int,
        token_count: int,
        slot: str = "open",
    ) -> None:
        """
        Notify that a daily playbook was generated.

        Args:
            market: Market code (e.g., "KR", "US")
            stock_count: Number of stocks in the playbook
            scenario_count: Total number of scenarios
            token_count: Gemini token usage for the playbook
            slot: Playbook slot; "mid" shows a mid-session refresh label
        """
        if not self._filter.playbook:
            return
        label = (
            "Playbook Refreshed (mid-session)" if slot == "mid" else "Playbook Generated"
        )
        message = (
            f"<b>{label}</b>\n"
            f"Market: {market}\n"
            f"Stocks: {stock_count}\n"
            f"Scenarios: {scenario_count}\n"
            f"Tokens: {token_count}"
        )
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.MEDIUM, message=message)
        )

    async def notify_scenario_matched(
        self,
        stock_code: str,
        action: str,
        condition_summary: str,
        confidence: float,
    ) -> None:
        """
        Notify that a scenario matched for a stock.

        Args:
            stock_code: Stock ticker symbol
            action: Scenario action (BUY/SELL/HOLD/REDUCE_ALL)
            condition_summary: Short summary of the matched condition
            confidence: Scenario confidence (0-100)
        """
        if not self._filter.scenario_match:
            return
        message = (
            f"<b>Scenario Matched</b>\n"
            f"Symbol: <code>{stock_code}</code>\n"
            f"Action: {action}\n"
            f"Condition: {condition_summary}\n"
            f"Confidence: {confidence:.0f}%"
        )
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.HIGH, message=message)
        )

    async def notify_playbook_failed(self, market: str, reason: str) -> None:
        """
        Notify that playbook generation failed.

        Args:
            market: Market code (e.g., "KR", "US")
            reason: Failure reason summary
        """
        if not self._filter.playbook:
            return
        message = f"<b>Playbook Failed</b>\nMarket: {market}\nReason: {reason[:200]}"
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.HIGH, message=message)
        )

    async def notify_system_shutdown(self, reason: str) -> None:
        """
        Notify system shutdown.

        Args:
            reason: Reason for shutdown (e.g., "Normal shutdown", "Circuit breaker")
        """
        if not self._filter.system_events:
            return
        message = f"<b>System Shutdown</b>\n{reason}"
        priority = (
            NotificationPriority.CRITICAL
            if "circuit breaker" in reason.lower()
            else NotificationPriority.MEDIUM
        )
        await self._send_notification(NotificationMessage(priority=priority, message=message))

    async def notify_unfilled_order(
        self,
        stock_code: str,
        market: str,
        action: str,
        quantity: int,
        outcome: str,
        new_price: float | None = None,
    ) -> None:
        """Notify about an unfilled overseas order that was cancelled or resubmitted.

        Args:
            stock_code: Stock ticker symbol.
            market: Exchange/market code (e.g., "NASD", "SEHK").
            action: "BUY" or "SELL".
            quantity: Unfilled quantity.
            outcome: "cancelled" or "resubmitted".
            new_price: New order price if resubmitted (None if only cancelled).
        """
        if not self._filter.trades:
            return
        # SELL resubmit is high priority — position liquidation at risk.
        # BUY cancel is medium priority — only cash is freed.
        priority = NotificationPriority.HIGH if action == "SELL" else NotificationPriority.MEDIUM
        outcome_emoji = "🔄" if outcome == "resubmitted" else "❌"
        outcome_label = "재주문" if outcome == "resubmitted" else "취소됨"
        action_emoji = "🔴" if action == "SELL" else "🟢"
        lines = [
            f"<b>{outcome_emoji} 미체결 주문 {outcome_label}</b>",
            f"Symbol: <code>{stock_code}</code> ({market})",
            f"Action: {action_emoji} {action}",
            f"Quantity: {quantity:,} shares",
        ]
        if new_price is not None:
            lines.append(f"New Price: {new_price:.4f}")
        message = "\n".join(lines)
        await self._send_notification(NotificationMessage(priority=priority, message=message))

    async def notify_error(self, error_type: str, error_msg: str, context: str) -> None:
        """
        Notify system error.

        Args:
            error_type: Type of error (e.g., "Connection Error")
            error_msg: Error message
            context: Error context (e.g., stock code, market)
        """
        if not self._filter.errors:
            return
        message = (
            f"<b>Error: {error_type}</b>\n"
            f"Context: {context}\n"
            f"Message: {error_msg[:200]}"  # Truncate long errors
        )
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.HIGH, message=message)
        )


class TelegramCommandHandler:
    """Handles incoming Telegram commands via long polling."""

    def __init__(self, client: TelegramClient, polling_interval: float = 1.0) -> None:
        """
        Initialize command handler.

        Args:
            client: TelegramClient instance for sending responses
            polling_interval: Polling interval in seconds
        """
        self._client = client
        self._polling_interval = polling_interval
        self._commands: dict[str, Callable[[], Awaitable[None]]] = {}
        self._commands_with_args: dict[str, Callable[[list[str]], Awaitable[None]]] = {}
        self._last_update_id = 0
        self._polling_task: asyncio.Task[None] | None = None
        self._running = False

    def register_command(self, command: str, handler: Callable[[], Awaitable[None]]) -> None:
        """
        Register a command handler (no arguments).

        Args:
            command: Command name (without leading slash, e.g., "start")
            handler: Async function to handle the command
        """
        self._commands[command] = handler
        logger.debug("Registered command handler: /%s", command)

    def register_command_with_args(
        self, command: str, handler: Callable[[list[str]], Awaitable[None]]
    ) -> None:
        """
        Register a command handler that receives trailing arguments.

        Args:
            command: Command name (without leading slash, e.g., "notify")
            handler: Async function receiving list of argument tokens
        """
        self._commands_with_args[command] = handler
        logger.debug("Registered command handler (with args): /%s", command)

    async def start_polling(self) -> None:
        """Start long polling for commands."""
        if self._running:
            logger.warning("Command handler already running")
            return

        if not self._client._enabled:
            logger.info("Command handler disabled (TelegramClient disabled)")
            return

        self._running = True
        self._polling_task = asyncio.create_task(self._poll_loop())
        logger.info("Started Telegram command polling")

    async def stop_polling(self) -> None:
        """Stop polling and cancel pending tasks."""
        if not self._running:
            return

        self._running = False
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        logger.info("Stopped Telegram command polling")

    async def _poll_loop(self) -> None:
        """Main polling loop that fetches updates."""
        while self._running:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._handle_update(update)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in polling loop: %s", exc)

            await asyncio.sleep(self._polling_interval)

    async def _get_updates(self) -> list[dict]:
        """
        Fetch updates from Telegram API.

        Returns:
            List of update objects
        """
        try:
            url = f"{self._client.API_BASE.format(token=self._client._bot_token)}/getUpdates"
            payload = {
                "offset": self._last_update_id + 1,
                "timeout": int(self._polling_interval),
                "allowed_updates": ["message"],
            }

            session = self._client._get_session()
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    if resp.status == 409:
                        # Another bot instance is already polling — stop this poller entirely.
                        # Retrying would keep conflicting with the other instance.
                        self._running = False
                        logger.warning(
                            "Telegram conflict (409): another instance is already polling. "
                            "Disabling Telegram commands for this process. "
                            "Ensure only one instance of The Ouroboros is running at a time.",
                        )
                    else:
                        logger.error(
                            "getUpdates API error (status=%d): %s", resp.status, error_text
                        )
                    return []

                data = await resp.json()
                if not data.get("ok"):
                    logger.error("getUpdates returned ok=false: %s", data)
                    return []

                updates = data.get("result", [])
                if updates:
                    self._last_update_id = updates[-1]["update_id"]

                return updates

        except TimeoutError:
            logger.debug("getUpdates timeout (normal)")
            return []
        except aiohttp.ClientError as exc:
            logger.error("getUpdates failed: %s", exc)
            return []
        except Exception as exc:
            logger.error("Unexpected error in _get_updates: %s", exc)
            return []

    async def _handle_update(self, update: dict) -> None:
        """
        Parse and handle a single update.

        Args:
            update: Update object from Telegram API
        """
        try:
            message = update.get("message")
            if not message:
                return

            # Verify chat_id matches configured chat
            chat_id = str(message.get("chat", {}).get("id", ""))
            if chat_id != self._client._chat_id:
                logger.warning("Ignoring command from unauthorized chat_id: %s", chat_id)
                return

            # Extract command text
            text = message.get("text", "").strip()
            if not text.startswith("/"):
                return

            # Parse command (remove leading slash and extract command name)
            command_parts = text[1:].split()
            if not command_parts:
                return

            # Remove @botname suffix if present (for group chats)
            command_name = command_parts[0].split("@")[0]

            # Execute handler (args-aware handlers take priority)
            args_handler = self._commands_with_args.get(command_name)
            if args_handler:
                logger.info("Executing command: /%s %s", command_name, command_parts[1:])
                await args_handler(command_parts[1:])
            elif command_name in self._commands:
                logger.info("Executing command: /%s", command_name)
                await self._commands[command_name]()
            else:
                logger.debug("Unknown command: /%s", command_name)
                await self._client.send_message(
                    f"Unknown command: /{command_name}\nUse /help to see available commands."
                )

        except Exception as exc:
            logger.error("Error handling update: %s", exc)
            # Don't crash the polling loop on handler errors
