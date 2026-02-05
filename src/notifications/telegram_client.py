"""Telegram notification client for real-time trading alerts."""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum

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
    ) -> None:
        """
        Initialize Telegram client.

        Args:
            bot_token: Telegram bot token from @BotFather
            chat_id: Target chat ID (user or group)
            enabled: Enable/disable notifications globally
            rate_limit: Maximum messages per second
        """
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = enabled
        self._rate_limiter = LeakyBucket(rate=rate_limit)
        self._session: aiohttp.ClientSession | None = None

        if not enabled:
            logger.info("Telegram notifications disabled via configuration")
        elif bot_token is None or chat_id is None:
            logger.warning(
                "Telegram notifications disabled (missing bot_token or chat_id)"
            )
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
                    logger.error(
                        "Telegram API error (status=%d): %s", resp.status, error_text
                    )
                    return False
                logger.debug("Telegram message sent: %s", text[:50])
                return True

        except asyncio.TimeoutError:
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

    async def notify_trade_execution(
        self,
        stock_code: str,
        market: str,
        action: str,
        quantity: int,
        price: float,
        confidence: float,
    ) -> None:
        """
        Notify trade execution.

        Args:
            stock_code: Stock ticker symbol
            market: Market name (e.g., "Korea", "United States")
            action: "BUY" or "SELL"
            quantity: Number of shares
            price: Execution price
            confidence: AI confidence level (0-100)
        """
        emoji = "🟢" if action == "BUY" else "🔴"
        message = (
            f"<b>{emoji} {action}</b>\n"
            f"Symbol: <code>{stock_code}</code> ({market})\n"
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

    async def notify_circuit_breaker(
        self, pnl_pct: float, threshold: float
    ) -> None:
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

    async def notify_system_start(
        self, mode: str, enabled_markets: list[str]
    ) -> None:
        """
        Notify system startup.

        Args:
            mode: Trading mode ("paper" or "live")
            enabled_markets: List of enabled market codes
        """
        mode_emoji = "📝" if mode == "paper" else "💰"
        markets_str = ", ".join(enabled_markets)
        message = (
            f"<b>{mode_emoji} System Started</b>\n"
            f"Mode: {mode.upper()}\n"
            f"Markets: {markets_str}"
        )
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.MEDIUM, message=message)
        )

    async def notify_system_shutdown(self, reason: str) -> None:
        """
        Notify system shutdown.

        Args:
            reason: Reason for shutdown (e.g., "Normal shutdown", "Circuit breaker")
        """
        message = f"<b>System Shutdown</b>\n{reason}"
        priority = (
            NotificationPriority.CRITICAL
            if "circuit breaker" in reason.lower()
            else NotificationPriority.MEDIUM
        )
        await self._send_notification(
            NotificationMessage(priority=priority, message=message)
        )

    async def notify_error(
        self, error_type: str, error_msg: str, context: str
    ) -> None:
        """
        Notify system error.

        Args:
            error_type: Type of error (e.g., "Connection Error")
            error_msg: Error message
            context: Error context (e.g., stock code, market)
        """
        message = (
            f"<b>Error: {error_type}</b>\n"
            f"Context: {context}\n"
            f"Message: {error_msg[:200]}"  # Truncate long errors
        )
        await self._send_notification(
            NotificationMessage(priority=NotificationPriority.HIGH, message=message)
        )
