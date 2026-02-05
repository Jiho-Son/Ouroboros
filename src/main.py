"""The Ouroboros — main trading loop.

Orchestrates the broker, brain, and risk manager into a continuous
trading cycle with configurable intervals.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from datetime import UTC, datetime
from typing import Any

from src.analysis.scanner import MarketScanner
from src.analysis.volatility import VolatilityAnalyzer
from src.brain.gemini_client import GeminiClient
from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker
from src.config import Settings
from src.context.layer import ContextLayer
from src.context.store import ContextStore
from src.core.criticality import CriticalityAssessor
from src.core.priority_queue import PriorityTaskQueue
from src.core.risk_manager import CircuitBreakerTripped, FatFingerRejected, RiskManager
from src.db import init_db, log_trade
from src.logging.decision_logger import DecisionLogger
from src.logging_config import setup_logging
from src.markets.schedule import MarketInfo, get_next_market_open, get_open_markets
from src.notifications.telegram_client import TelegramClient, TelegramCommandHandler

logger = logging.getLogger(__name__)


def safe_float(value: str | float | None, default: float = 0.0) -> float:
    """Convert to float, handling empty strings and None.

    Args:
        value: Value to convert (string, float, or None)
        default: Default value if conversion fails

    Returns:
        Converted float or default value

    Examples:
        >>> safe_float("123.45")
        123.45
        >>> safe_float("")
        0.0
        >>> safe_float(None)
        0.0
        >>> safe_float("invalid", 99.0)
        99.0
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# Target stock codes to monitor per market
WATCHLISTS = {
    "KR": ["005930", "000660", "035420"],  # Samsung, SK Hynix, NAVER
    "US_NASDAQ": ["AAPL", "MSFT", "GOOGL"],  # Example US stocks
    "US_NYSE": ["JPM", "BAC"],  # Example NYSE stocks
    "JP": ["7203", "6758"],  # Toyota, Sony
}

TRADE_INTERVAL_SECONDS = 60
SCAN_INTERVAL_SECONDS = 60  # Scan markets every 60 seconds
MAX_CONNECTION_RETRIES = 3

# Daily trading mode constants (for Free tier API efficiency)
DAILY_TRADE_SESSIONS = 4  # Number of trading sessions per day
TRADE_SESSION_INTERVAL_HOURS = 6  # Hours between sessions

# Full stock universe per market (for scanning)
# In production, this would be loaded from a database or API
STOCK_UNIVERSE = {
    "KR": ["005930", "000660", "035420", "051910", "005380", "005490"],
    "US_NASDAQ": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA"],
    "US_NYSE": ["JPM", "BAC", "XOM", "JNJ", "V"],
    "JP": ["7203", "6758", "9984", "6861"],
}


async def trading_cycle(
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    brain: GeminiClient,
    risk: RiskManager,
    db_conn: Any,
    decision_logger: DecisionLogger,
    context_store: ContextStore,
    criticality_assessor: CriticalityAssessor,
    telegram: TelegramClient,
    market: MarketInfo,
    stock_code: str,
) -> None:
    """Execute one trading cycle for a single stock."""
    cycle_start_time = asyncio.get_event_loop().time()

    # 1. Fetch market data
    if market.is_domestic:
        orderbook = await broker.get_orderbook(stock_code)
        balance_data = await broker.get_balance()

        output2 = balance_data.get("output2", [{}])
        total_eval = safe_float(output2[0].get("tot_evlu_amt", "0")) if output2 else 0
        total_cash = safe_float(
            balance_data.get("output2", [{}])[0].get("dnca_tot_amt", "0")
            if output2
            else "0"
        )
        purchase_total = safe_float(output2[0].get("pchs_amt_smtl_amt", "0")) if output2 else 0

        current_price = safe_float(orderbook.get("output1", {}).get("stck_prpr", "0"))
        foreigner_net = safe_float(orderbook.get("output1", {}).get("frgn_ntby_qty", "0"))
    else:
        # Overseas market
        price_data = await overseas_broker.get_overseas_price(
            market.exchange_code, stock_code
        )
        balance_data = await overseas_broker.get_overseas_balance(market.exchange_code)

        output2 = balance_data.get("output2", [{}])
        # Handle both list and dict response formats
        if isinstance(output2, list) and output2:
            balance_info = output2[0]
        elif isinstance(output2, dict):
            balance_info = output2
        else:
            balance_info = {}

        total_eval = safe_float(balance_info.get("frcr_evlu_tota", "0") or "0")
        total_cash = safe_float(balance_info.get("frcr_dncl_amt_2", "0") or "0")
        purchase_total = safe_float(balance_info.get("frcr_buy_amt_smtl", "0") or "0")

        current_price = safe_float(price_data.get("output", {}).get("last", "0"))
        foreigner_net = 0.0  # Not available for overseas

    # Calculate daily P&L %
    pnl_pct = (
        ((total_eval - purchase_total) / purchase_total * 100)
        if purchase_total > 0
        else 0.0
    )

    market_data = {
        "stock_code": stock_code,
        "market_name": market.name,
        "current_price": current_price,
        "foreigner_net": foreigner_net,
    }

    # 1.5. Get volatility metrics from context store (L7_REALTIME)
    latest_timeframe = context_store.get_latest_timeframe(ContextLayer.L7_REALTIME)
    volatility_score = 50.0  # Default normal volatility
    volume_surge = 1.0
    price_change_1m = 0.0

    if latest_timeframe:
        volatility_data = context_store.get_context(
            ContextLayer.L7_REALTIME,
            latest_timeframe,
            f"volatility_{stock_code}",
        )
        if volatility_data:
            volatility_score = volatility_data.get("momentum_score", 50.0)
            volume_surge = volatility_data.get("volume_surge", 1.0)
            price_change_1m = volatility_data.get("price_change_1m", 0.0)

    # 1.6. Assess criticality based on market conditions
    criticality = criticality_assessor.assess_market_conditions(
        pnl_pct=pnl_pct,
        volatility_score=volatility_score,
        volume_surge=volume_surge,
        price_change_1m=price_change_1m,
        is_market_open=True,
    )

    logger.info(
        "Criticality for %s (%s): %s (pnl=%.2f%%, volatility=%.1f, volume_surge=%.1fx)",
        stock_code,
        market.name,
        criticality.value,
        pnl_pct,
        volatility_score,
        volume_surge,
    )

    # 2. Ask the brain for a decision
    decision = await brain.decide(market_data)
    logger.info(
        "Decision for %s (%s): %s (confidence=%d)",
        stock_code,
        market.name,
        decision.action,
        decision.confidence,
    )

    # 2.5. Log decision with context snapshot
    context_snapshot = {
        "L1": {
            "current_price": current_price,
            "foreigner_net": foreigner_net,
        },
        "L2": {
            "total_eval": total_eval,
            "total_cash": total_cash,
            "purchase_total": purchase_total,
            "pnl_pct": pnl_pct,
        },
        # L3-L7 will be populated when context tree is implemented
    }
    input_data = {
        "current_price": current_price,
        "foreigner_net": foreigner_net,
        "total_eval": total_eval,
        "total_cash": total_cash,
        "pnl_pct": pnl_pct,
    }

    decision_logger.log_decision(
        stock_code=stock_code,
        market=market.code,
        exchange_code=market.exchange_code,
        action=decision.action,
        confidence=decision.confidence,
        rationale=decision.rationale,
        context_snapshot=context_snapshot,
        input_data=input_data,
    )

    # 3. Execute if actionable
    if decision.action in ("BUY", "SELL"):
        # Determine order size (simplified: 1 lot)
        quantity = 1
        order_amount = current_price * quantity

        # 4. Risk check BEFORE order
        try:
            risk.validate_order(
                current_pnl_pct=pnl_pct,
                order_amount=order_amount,
                total_cash=total_cash,
            )
        except FatFingerRejected as exc:
            try:
                await telegram.notify_fat_finger(
                    stock_code=stock_code,
                    order_amount=exc.order_amount,
                    total_cash=exc.total_cash,
                    max_pct=exc.max_pct,
                )
            except Exception as notify_exc:
                logger.warning("Fat finger notification failed: %s", notify_exc)
            raise  # Re-raise to prevent trade

        # 5. Send order
        if market.is_domestic:
            result = await broker.send_order(
                stock_code=stock_code,
                order_type=decision.action,
                quantity=quantity,
                price=0,  # market order
            )
        else:
            result = await overseas_broker.send_overseas_order(
                exchange_code=market.exchange_code,
                stock_code=stock_code,
                order_type=decision.action,
                quantity=quantity,
                price=0.0,  # market order
            )
        logger.info("Order result: %s", result.get("msg1", "OK"))

        # 5.5. Notify trade execution
        try:
            await telegram.notify_trade_execution(
                stock_code=stock_code,
                market=market.name,
                action=decision.action,
                quantity=quantity,
                price=current_price,
                confidence=decision.confidence,
            )
        except Exception as exc:
            logger.warning("Telegram notification failed: %s", exc)

    # 6. Log trade
    log_trade(
        conn=db_conn,
        stock_code=stock_code,
        action=decision.action,
        confidence=decision.confidence,
        rationale=decision.rationale,
        market=market.code,
        exchange_code=market.exchange_code,
    )

    # 7. Latency monitoring
    cycle_end_time = asyncio.get_event_loop().time()
    cycle_latency = cycle_end_time - cycle_start_time
    timeout = criticality_assessor.get_timeout(criticality)

    if timeout and cycle_latency > timeout:
        logger.warning(
            "Trading cycle exceeded timeout for %s (criticality=%s, latency=%.2fs, timeout=%.2fs)",
            stock_code,
            criticality.value,
            cycle_latency,
            timeout,
        )
    else:
        logger.debug(
            "Trading cycle completed within timeout for %s (criticality=%s, latency=%.2fs)",
            stock_code,
            criticality.value,
            cycle_latency,
        )


async def run_daily_session(
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    brain: GeminiClient,
    risk: RiskManager,
    db_conn: Any,
    decision_logger: DecisionLogger,
    context_store: ContextStore,
    criticality_assessor: CriticalityAssessor,
    telegram: TelegramClient,
    settings: Settings,
) -> None:
    """Execute one daily trading session.

    Designed for API efficiency with Gemini Free tier:
    - Batch decision making (1 API call per market)
    - Runs N times per day at fixed intervals
    - Minimizes API usage while maintaining trading capability
    """
    # Get currently open markets
    open_markets = get_open_markets(settings.enabled_market_list)

    if not open_markets:
        logger.info("No markets open for this session")
        return

    logger.info("Starting daily trading session for %d markets", len(open_markets))

    # Process each open market
    for market in open_markets:
        # Get watchlist for this market
        watchlist = WATCHLISTS.get(market.code, [])
        if not watchlist:
            logger.debug("No watchlist for market %s", market.code)
            continue

        logger.info("Processing market: %s (%d stocks)", market.name, len(watchlist))

        # Collect market data for all stocks in the watchlist
        stocks_data = []
        for stock_code in watchlist:
            try:
                if market.is_domestic:
                    orderbook = await broker.get_orderbook(stock_code)
                    current_price = safe_float(orderbook.get("output1", {}).get("stck_prpr", "0"))
                    foreigner_net = safe_float(
                        orderbook.get("output1", {}).get("frgn_ntby_qty", "0")
                    )
                else:
                    price_data = await overseas_broker.get_overseas_price(
                        market.exchange_code, stock_code
                    )
                    current_price = safe_float(price_data.get("output", {}).get("last", "0"))
                    foreigner_net = 0.0

                stocks_data.append(
                    {
                        "stock_code": stock_code,
                        "market_name": market.name,
                        "current_price": current_price,
                        "foreigner_net": foreigner_net,
                    }
                )
            except Exception as exc:
                logger.error("Failed to fetch data for %s: %s", stock_code, exc)
                continue

        if not stocks_data:
            logger.warning("No valid stock data for market %s", market.code)
            continue

        # Get batch decisions (1 API call for all stocks in this market)
        logger.info("Requesting batch decision for %d stocks in %s", len(stocks_data), market.name)
        decisions = await brain.decide_batch(stocks_data)

        # Get balance data once for the market
        if market.is_domestic:
            balance_data = await broker.get_balance()
            output2 = balance_data.get("output2", [{}])
            total_eval = safe_float(output2[0].get("tot_evlu_amt", "0")) if output2 else 0
            total_cash = safe_float(output2[0].get("dnca_tot_amt", "0")) if output2 else 0
            purchase_total = safe_float(output2[0].get("pchs_amt_smtl_amt", "0")) if output2 else 0
        else:
            balance_data = await overseas_broker.get_overseas_balance(market.exchange_code)
            output2 = balance_data.get("output2", [{}])
            if isinstance(output2, list) and output2:
                balance_info = output2[0]
            elif isinstance(output2, dict):
                balance_info = output2
            else:
                balance_info = {}

            total_eval = safe_float(balance_info.get("frcr_evlu_tota", "0") or "0")
            total_cash = safe_float(balance_info.get("frcr_dncl_amt_2", "0") or "0")
            purchase_total = safe_float(balance_info.get("frcr_buy_amt_smtl", "0") or "0")

        # Calculate daily P&L %
        pnl_pct = (
            ((total_eval - purchase_total) / purchase_total * 100) if purchase_total > 0 else 0.0
        )

        # Execute decisions for each stock
        for stock_data in stocks_data:
            stock_code = stock_data["stock_code"]
            decision = decisions.get(stock_code)

            if not decision:
                logger.warning("No decision for %s — skipping", stock_code)
                continue

            logger.info(
                "Decision for %s (%s): %s (confidence=%d)",
                stock_code,
                market.name,
                decision.action,
                decision.confidence,
            )

            # Log decision
            context_snapshot = {
                "L1": {
                    "current_price": stock_data["current_price"],
                    "foreigner_net": stock_data["foreigner_net"],
                },
                "L2": {
                    "total_eval": total_eval,
                    "total_cash": total_cash,
                    "purchase_total": purchase_total,
                    "pnl_pct": pnl_pct,
                },
            }
            input_data = {
                "current_price": stock_data["current_price"],
                "foreigner_net": stock_data["foreigner_net"],
                "total_eval": total_eval,
                "total_cash": total_cash,
                "pnl_pct": pnl_pct,
            }

            decision_logger.log_decision(
                stock_code=stock_code,
                market=market.code,
                exchange_code=market.exchange_code,
                action=decision.action,
                confidence=decision.confidence,
                rationale=decision.rationale,
                context_snapshot=context_snapshot,
                input_data=input_data,
            )

            # Execute if actionable
            if decision.action in ("BUY", "SELL"):
                quantity = 1
                order_amount = stock_data["current_price"] * quantity

                # Risk check
                try:
                    risk.validate_order(
                        current_pnl_pct=pnl_pct,
                        order_amount=order_amount,
                        total_cash=total_cash,
                    )
                except FatFingerRejected as exc:
                    try:
                        await telegram.notify_fat_finger(
                            stock_code=stock_code,
                            order_amount=exc.order_amount,
                            total_cash=exc.total_cash,
                            max_pct=exc.max_pct,
                        )
                    except Exception as notify_exc:
                        logger.warning("Fat finger notification failed: %s", notify_exc)
                    continue  # Skip this order
                except CircuitBreakerTripped as exc:
                    logger.critical("Circuit breaker tripped — stopping session")
                    try:
                        await telegram.notify_circuit_breaker(
                            pnl_pct=exc.pnl_pct,
                            threshold=exc.threshold,
                        )
                    except Exception as notify_exc:
                        logger.warning("Circuit breaker notification failed: %s", notify_exc)
                    raise

                # Send order
                try:
                    if market.is_domestic:
                        result = await broker.send_order(
                            stock_code=stock_code,
                            order_type=decision.action,
                            quantity=quantity,
                            price=0,  # market order
                        )
                    else:
                        result = await overseas_broker.send_overseas_order(
                            exchange_code=market.exchange_code,
                            stock_code=stock_code,
                            order_type=decision.action,
                            quantity=quantity,
                            price=0.0,  # market order
                        )
                    logger.info("Order result: %s", result.get("msg1", "OK"))

                    # Notify trade execution
                    try:
                        await telegram.notify_trade_execution(
                            stock_code=stock_code,
                            market=market.name,
                            action=decision.action,
                            quantity=quantity,
                            price=stock_data["current_price"],
                            confidence=decision.confidence,
                        )
                    except Exception as exc:
                        logger.warning("Telegram notification failed: %s", exc)
                except Exception as exc:
                    logger.error("Order execution failed for %s: %s", stock_code, exc)
                    continue

            # Log trade
            log_trade(
                conn=db_conn,
                stock_code=stock_code,
                action=decision.action,
                confidence=decision.confidence,
                rationale=decision.rationale,
                market=market.code,
                exchange_code=market.exchange_code,
            )

    logger.info("Daily trading session completed")


async def run(settings: Settings) -> None:
    """Main async loop — iterate over open markets on a timer."""
    broker = KISBroker(settings)
    overseas_broker = OverseasBroker(broker)
    brain = GeminiClient(settings)
    risk = RiskManager(settings)
    db_conn = init_db(settings.DB_PATH)
    decision_logger = DecisionLogger(db_conn)
    context_store = ContextStore(db_conn)

    # Initialize Telegram notifications
    telegram = TelegramClient(
        bot_token=settings.TELEGRAM_BOT_TOKEN,
        chat_id=settings.TELEGRAM_CHAT_ID,
        enabled=settings.TELEGRAM_ENABLED,
    )

    # Initialize Telegram command handler
    command_handler = TelegramCommandHandler(telegram)

    # Register basic commands
    async def handle_start() -> None:
        """Handle /start command."""
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
        await telegram.send_message(message)

    async def handle_help() -> None:
        """Handle /help command."""
        message = (
            "<b>📖 Available Commands</b>\n\n"
            "/start - Welcome message\n"
            "/help - Show available commands\n"
            "/status - Trading status (mode, markets, P&L)\n"
            "/positions - Current holdings\n"
            "/stop - Pause trading\n"
            "/resume - Resume trading"
        )
        await telegram.send_message(message)

    async def handle_stop() -> None:
        """Handle /stop command - pause trading."""
        if not pause_trading.is_set():
            await telegram.send_message("⏸️ Trading is already paused")
            return

        pause_trading.clear()
        logger.info("Trading paused via Telegram command")
        await telegram.send_message(
            "<b>⏸️ Trading Paused</b>\n\n"
            "All trading operations have been suspended.\n"
            "Use /resume to restart trading."
        )

    async def handle_resume() -> None:
        """Handle /resume command - resume trading."""
        if pause_trading.is_set():
            await telegram.send_message("▶️ Trading is already active")
            return

        pause_trading.set()
        logger.info("Trading resumed via Telegram command")
        await telegram.send_message(
            "<b>▶️ Trading Resumed</b>\n\n"
            "Trading operations have been restarted."
        )

    command_handler.register_command("start", handle_start)
    command_handler.register_command("help", handle_help)
    command_handler.register_command("stop", handle_stop)
    command_handler.register_command("resume", handle_resume)

    # Initialize volatility hunter
    volatility_analyzer = VolatilityAnalyzer(min_volume_surge=2.0, min_price_change=1.0)
    market_scanner = MarketScanner(
        broker=broker,
        overseas_broker=overseas_broker,
        volatility_analyzer=volatility_analyzer,
        context_store=context_store,
        top_n=5,
        max_concurrent_scans=1,  # Fully serialized to avoid EGW00201
    )

    # Initialize latency control system
    criticality_assessor = CriticalityAssessor(
        critical_pnl_threshold=-2.5,  # Near circuit breaker at -3.0%
        critical_price_change_threshold=5.0,  # 5% in 1 minute
        critical_volume_surge_threshold=10.0,  # 10x average
        high_volatility_threshold=70.0,
        low_volatility_threshold=30.0,
    )
    priority_queue = PriorityTaskQueue(max_size=1000)

    # Track last scan time for each market
    last_scan_time: dict[str, float] = {}

    # Track market open/close state for notifications
    _market_states: dict[str, bool] = {}  # market_code -> is_open

    # Trading control events
    shutdown = asyncio.Event()
    pause_trading = asyncio.Event()
    pause_trading.set()  # Default: trading enabled

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("The Ouroboros is alive. Mode: %s, Trading: %s", settings.MODE, settings.TRADE_MODE)
    logger.info("Enabled markets: %s", settings.enabled_market_list)

    # Notify system startup
    try:
        await telegram.notify_system_start(settings.MODE, settings.enabled_market_list)
    except Exception as exc:
        logger.warning("System startup notification failed: %s", exc)

    # Start command handler
    try:
        await command_handler.start_polling()
    except Exception as exc:
        logger.warning("Failed to start command handler: %s", exc)

    try:
        # Branch based on trading mode
        if settings.TRADE_MODE == "daily":
            # Daily trading mode: batch decisions at fixed intervals
            logger.info(
                "Daily trading mode: %d sessions every %d hours",
                settings.DAILY_SESSIONS,
                settings.SESSION_INTERVAL_HOURS,
            )

            session_interval = settings.SESSION_INTERVAL_HOURS * 3600  # Convert to seconds

            while not shutdown.is_set():
                # Wait for trading to be unpaused
                await pause_trading.wait()

                try:
                    await run_daily_session(
                        broker,
                        overseas_broker,
                        brain,
                        risk,
                        db_conn,
                        decision_logger,
                        context_store,
                        criticality_assessor,
                        telegram,
                        settings,
                    )
                except CircuitBreakerTripped:
                    logger.critical("Circuit breaker tripped — shutting down")
                    shutdown.set()
                    break
                except Exception as exc:
                    logger.exception("Daily session error: %s", exc)

                # Wait for next session or shutdown
                logger.info("Next session in %.1f hours", session_interval / 3600)
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=session_interval)
                except TimeoutError:
                    pass  # Normal — time for next session

        else:
            # Realtime trading mode: original per-stock loop
            logger.info("Realtime trading mode: 60s interval per stock")

            while not shutdown.is_set():
                # Wait for trading to be unpaused
                await pause_trading.wait()

                # Get currently open markets
                open_markets = get_open_markets(settings.enabled_market_list)

                if not open_markets:
                    # Notify market close for any markets that were open
                    for market_code, is_open in list(_market_states.items()):
                        if is_open:
                            try:
                                from src.markets.schedule import MARKETS

                                market_info = MARKETS.get(market_code)
                                if market_info:
                                    await telegram.notify_market_close(market_info.name, 0.0)
                            except Exception as exc:
                                logger.warning("Market close notification failed: %s", exc)
                            _market_states[market_code] = False

                    # No markets open — wait until next market opens
                    try:
                        next_market, next_open_time = get_next_market_open(
                            settings.enabled_market_list
                        )
                        now = datetime.now(UTC)
                        wait_seconds = (next_open_time - now).total_seconds()
                        logger.info(
                            "No markets open. Next market: %s, opens in %.1f hours",
                            next_market.name,
                            wait_seconds / 3600,
                        )
                        await asyncio.wait_for(shutdown.wait(), timeout=wait_seconds)
                    except TimeoutError:
                        continue  # Market should be open now
                    except ValueError as exc:
                        logger.error("Failed to find next market open: %s", exc)
                        await asyncio.sleep(TRADE_INTERVAL_SECONDS)
                    continue

                # Process each open market
                for market in open_markets:
                    if shutdown.is_set():
                        break

                    # Notify market open if it just opened
                    if not _market_states.get(market.code, False):
                        try:
                            await telegram.notify_market_open(market.name)
                        except Exception as exc:
                            logger.warning("Market open notification failed: %s", exc)
                        _market_states[market.code] = True

                    # Volatility Hunter: Scan market periodically to update watchlist
                    now_timestamp = asyncio.get_event_loop().time()
                    last_scan = last_scan_time.get(market.code, 0.0)
                    if now_timestamp - last_scan >= SCAN_INTERVAL_SECONDS:
                        try:
                            # Scan all stocks in the universe
                            stock_universe = STOCK_UNIVERSE.get(market.code, [])
                            if stock_universe:
                                logger.info("Volatility Hunter: Scanning %s market", market.name)
                                scan_result = await market_scanner.scan_market(
                                    market, stock_universe
                                )

                                # Update watchlist with top movers
                                current_watchlist = WATCHLISTS.get(market.code, [])
                                updated_watchlist = market_scanner.get_updated_watchlist(
                                    current_watchlist,
                                    scan_result,
                                    max_replacements=2,
                                )
                                WATCHLISTS[market.code] = updated_watchlist

                                logger.info(
                                    "Volatility Hunter: Watchlist updated for %s (%d top movers, %d breakouts)",
                                    market.name,
                                    len(scan_result.top_movers),
                                    len(scan_result.breakouts),
                                )

                            last_scan_time[market.code] = now_timestamp
                        except Exception as exc:
                            logger.error("Volatility Hunter scan failed for %s: %s", market.name, exc)

                    # Get watchlist for this market
                    watchlist = WATCHLISTS.get(market.code, [])
                    if not watchlist:
                        logger.debug("No watchlist for market %s", market.code)
                        continue

                    logger.info("Processing market: %s (%d stocks)", market.name, len(watchlist))

                    # Process each stock in the watchlist
                    for stock_code in watchlist:
                        if shutdown.is_set():
                            break

                        # Retry logic for connection errors
                        for attempt in range(1, MAX_CONNECTION_RETRIES + 1):
                            try:
                                await trading_cycle(
                                    broker,
                                    overseas_broker,
                                    brain,
                                    risk,
                                    db_conn,
                                    decision_logger,
                                    context_store,
                                    criticality_assessor,
                                    telegram,
                                    market,
                                    stock_code,
                                )
                                break  # Success — exit retry loop
                            except CircuitBreakerTripped as exc:
                                logger.critical("Circuit breaker tripped — shutting down")
                                try:
                                    await telegram.notify_circuit_breaker(
                                        pnl_pct=exc.pnl_pct,
                                        threshold=exc.threshold,
                                    )
                                except Exception as notify_exc:
                                    logger.warning(
                                        "Circuit breaker notification failed: %s", notify_exc
                                    )
                                raise
                            except ConnectionError as exc:
                                if attempt < MAX_CONNECTION_RETRIES:
                                    logger.warning(
                                        "Connection error for %s (attempt %d/%d): %s",
                                        stock_code,
                                        attempt,
                                        MAX_CONNECTION_RETRIES,
                                        exc,
                                    )
                                    await asyncio.sleep(2**attempt)  # Exponential backoff
                                else:
                                    logger.error(
                                        "Connection error for %s (all retries exhausted): %s",
                                        stock_code,
                                        exc,
                                    )
                                    break  # Give up on this stock
                            except Exception as exc:
                                logger.exception("Unexpected error for %s: %s", stock_code, exc)
                                break  # Don't retry on unexpected errors

                # Log priority queue metrics periodically
                metrics = await priority_queue.get_metrics()
                if metrics.total_enqueued > 0:
                    logger.info(
                        "Priority queue metrics: enqueued=%d, dequeued=%d, size=%d, timeouts=%d, errors=%d",
                        metrics.total_enqueued,
                        metrics.total_dequeued,
                        metrics.current_size,
                        metrics.total_timeouts,
                        metrics.total_errors,
                    )

                # Wait for next cycle or shutdown
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=TRADE_INTERVAL_SECONDS)
                except TimeoutError:
                    pass  # Normal — timeout means it's time for next cycle
    finally:
        # Clean up resources
        await command_handler.stop_polling()
        await broker.close()
        await telegram.close()
        db_conn.close()
        logger.info("The Ouroboros rests.")


def main() -> None:
    parser = argparse.ArgumentParser(description="The Ouroboros Trading Agent")
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default="paper",
        help="Trading mode (default: paper)",
    )
    args = parser.parse_args()

    setup_logging()
    settings = Settings(MODE=args.mode)  # type: ignore[call-arg]
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
