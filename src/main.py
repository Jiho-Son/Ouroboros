"""The Ouroboros — main trading loop.

Orchestrates the broker, brain, and risk manager into a continuous
trading cycle with configurable intervals.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import signal
import sys
import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.atr_helpers import (
    _split_trade_pnl_components,
)
from src.analysis.smart_scanner import ScanCandidate, SmartVolatilityScanner
from src.analysis.volatility import VolatilityAnalyzer
from src.brain.context_selector import ContextSelector
from src.brain.gemini_client import GeminiClient, TradeDecision
from src.broker.balance_utils import (
    _extract_avg_price_from_balance,
    _extract_buy_fx_rate,
    _extract_fx_rate_from_sources,
    _extract_held_codes_from_balance,
    _extract_held_qty_from_balance,
)
from src.broker.kis_api import KISBroker, kr_round_down
from src.broker.kis_websocket import (
    KISWebSocketClient,
    KISWebSocketPriceEvent,
    supports_realtime_price_market,
)
from src.broker.overseas import OverseasBroker
from src.broker.pending_orders import (
    handle_domestic_pending_orders,
    handle_overseas_pending_orders,
)
from src.config import Settings
from src.context.aggregator import ContextAggregator
from src.context.layer import ContextLayer
from src.context.scheduler import ContextScheduler
from src.context.store import ContextStore
from src.core.blackout_manager import (
    BlackoutOrderManager,
    parse_blackout_windows_kst,
)
from src.core.blackout_runtime import (
    _maybe_queue_order_intent,
    process_blackout_recovery_orders,
)
from src.core.criticality import CriticalityAssessor
from src.core.kill_switch_runtime import (
    KILL_SWITCH,
    _trigger_emergency_kill_switch,
)
from src.core.order_helpers import (
    _determine_order_quantity,
    _resolve_buy_suppression_position,
    _resolve_domestic_quote_market_div_code,
    _resolve_sell_qty_for_pnl,
    _should_block_overseas_buy_for_fx_buffer,
    _should_force_exit_for_overnight,
)
from src.core.order_policy import (
    OrderPolicyRejected,
    get_session_info,
    validate_order_policy,
)
from src.core.priority_queue import PriorityTaskQueue
from src.core.realtime_hard_stop import HardStopTrigger, RealtimeHardStopMonitor
from src.core.risk_manager import CircuitBreakerTripped, FatFingerRejected, RiskManager
from src.core.session_risk import (
    _STOPLOSS_REENTRY_COOLDOWN_UNTIL,
    _resolve_market_setting,
    _session_risk_overrides,
    _stoploss_cooldown_key,
    _stoploss_cooldown_minutes,
)
from src.db import (
    get_latest_buy_trade,
    get_open_position,
    get_recent_symbols,
    init_db,
    log_trade,
)
from src.decision_logging.decision_logger import DecisionLogger
from src.evolution.daily_review import DailyReviewer
from src.evolution.optimizer import EvolutionOptimizer
from src.logging_config import setup_logging
from src.markets.schedule import MARKETS, MarketInfo, get_next_market_open, get_open_markets
from src.notifications.telegram_client import (
    NotificationFilter,
    TelegramClient,
    TelegramCommandHandler,
)
from src.strategy.exit_manager import (
    _apply_staged_exit_override_for_hold,
    _clear_runtime_exit_cache_for_symbol,
    _inject_staged_exit_features,
    _merge_staged_exit_evidence_into_log,
    update_runtime_exit_peak,
)
from src.strategy.models import DayPlaybook, MarketOutlook
from src.strategy.playbook_store import PlaybookStore
from src.strategy.pre_market_planner import PreMarketPlanner
from src.strategy.scenario_engine import ScenarioEngine

logger = logging.getLogger(__name__)
_SESSION_CLOSE_WINDOWS = {"NXT_AFTER", "US_AFTER"}


def _ensure_runtime_mode_allowed(mode: str) -> None:
    """Reject runtime execution modes that are banned by policy."""
    if mode == "paper":
        raise ValueError("paper mode runtime execution is banned")


def _rollback_pending_order_position(
    *,
    db_conn: Any,
    market_code: str,
    exchange_code: str,
    stock_code: str,
    action: str,
    quantity: int | None = None,
    runtime_session_id: str,
    settings: Settings,
) -> None:
    """Rollback optimistic DB trades when a pending order is cancelled without replacement."""
    if action == "BUY":
        if not get_open_position(db_conn, stock_code, market_code):
            return
        log_trade(
            conn=db_conn,
            stock_code=stock_code,
            action="SELL",
            confidence=0,
            rationale="[pending-buy-cancel] Cancelled unfilled BUY without replacement",
            quantity=0,
            price=0.0,
            pnl=0.0,
            market=market_code,
            exchange_code=exchange_code,
            session_id=runtime_session_id,
            mode=settings.MODE,
        )
        return

    if get_open_position(db_conn, stock_code, market_code):
        return

    buy_trade = get_latest_buy_trade(
        db_conn,
        stock_code,
        market_code,
        exchange_code=exchange_code,
    )
    if not buy_trade or buy_trade.get("price") is None:
        return

    selection_context = buy_trade.get("selection_context")
    if isinstance(selection_context, str) and selection_context.strip():
        try:
            decoded_selection_context = json.loads(selection_context)
        except json.JSONDecodeError:
            decoded_selection_context = None
        selection_context = (
            decoded_selection_context if isinstance(decoded_selection_context, dict) else None
        )

    log_trade(
        conn=db_conn,
        stock_code=stock_code,
        action="BUY",
        confidence=0,
        rationale="[pending-sell-restore] Cancelled unfilled SELL without replacement",
        quantity=int(quantity if quantity is not None else (buy_trade.get("quantity") or 0)),
        price=float(buy_trade["price"]),
        pnl=0.0,
        market=market_code,
        exchange_code=exchange_code,
        session_id=runtime_session_id,
        selection_context=selection_context,
        decision_id=buy_trade.get("decision_id"),
        mode=settings.MODE,
    )


async def _sync_realtime_hard_stop_monitor(
    *,
    monitor: RealtimeHardStopMonitor | None,
    websocket_client: KISWebSocketClient | None,
    market: MarketInfo,
    stock_code: str,
    decision_action: str,
    open_position: dict[str, Any] | None,
    market_data: dict[str, Any],
) -> None:
    """Register/remove supported positions from the realtime hard-stop monitor."""
    if monitor is None or not supports_realtime_price_market(market.code):
        return

    if not open_position:
        monitor.remove(market.code, stock_code)
        if websocket_client is not None:
            await websocket_client.unsubscribe(market.code, stock_code)
        return

    if decision_action != "HOLD":
        monitor.remove(market.code, stock_code)
        if websocket_client is not None:
            await websocket_client.unsubscribe(market.code, stock_code)
        return

    raw_evidence = market_data.get("_staged_exit_evidence")
    if not isinstance(raw_evidence, dict):
        monitor.remove(market.code, stock_code)
        if websocket_client is not None:
            await websocket_client.unsubscribe(market.code, stock_code)
        return

    stop_loss_pct = safe_float(raw_evidence.get("stop_loss_threshold"), 0.0)
    entry_price = safe_float(open_position.get("price"), 0.0)
    quantity = int(open_position.get("quantity") or 0)
    if stop_loss_pct >= 0 or entry_price <= 0 or quantity <= 0:
        monitor.remove(market.code, stock_code)
        if websocket_client is not None:
            await websocket_client.unsubscribe(market.code, stock_code)
        return

    monitor.register(
        market_code=market.code,
        stock_code=stock_code,
        entry_price=entry_price,
        quantity=quantity,
        hard_stop_pct=stop_loss_pct,
        decision_id=str(open_position.get("decision_id") or ""),
        position_timestamp=str(open_position.get("timestamp") or ""),
    )
    if websocket_client is not None:
        await websocket_client.subscribe(market.code, stock_code)


async def _clear_realtime_hard_stop_tracking(
    *,
    monitor: RealtimeHardStopMonitor | None,
    websocket_client: KISWebSocketClient | None,
    market: MarketInfo,
    stock_code: str,
) -> None:
    """Remove a supported symbol from realtime hard-stop tracking."""
    if monitor is None or not supports_realtime_price_market(market.code):
        return

    monitor.remove(market.code, stock_code)
    if websocket_client is not None:
        try:
            await websocket_client.unsubscribe(market.code, stock_code)
        except Exception as exc:
            logger.warning(
                "Realtime hard-stop unsubscribe failed for %s (%s): %s",
                stock_code,
                market.name,
                exc,
            )


async def _handle_realtime_hard_stop_trigger(
    *,
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    db_conn: Any,
    decision_logger: DecisionLogger,
    telegram: TelegramClient,
    settings: Settings | None,
    monitor: RealtimeHardStopMonitor,
    websocket_client: KISWebSocketClient | None,
    trigger: HardStopTrigger,
) -> bool:
    """Submit a market-appropriate SELL order for a realtime hard-stop breach."""
    market = MARKETS[trigger.market_code]
    runtime_session_id = get_session_info(market).session_id
    current_price = float(trigger.last_price)
    if market.is_domestic:
        order_price = kr_round_down(current_price * 0.998)
    else:
        price_decimals = 2 if current_price >= 1.0 else 4
        order_price = round(current_price * 0.998, price_decimals)
    rationale = (
        "Realtime hard-stop triggered "
        f"(last_price={current_price:.2f} <= hard_stop_price={trigger.hard_stop_price:.2f})"
    )
    try:
        if market.is_domestic:
            balance_data = await broker.get_balance()
        else:
            balance_data = await overseas_broker.get_overseas_balance(market.exchange_code)
        quantity = _extract_held_qty_from_balance(
            balance_data,
            trigger.stock_code,
            is_domestic=market.is_domestic,
        )
    except Exception as exc:
        logger.warning(
            "Realtime hard-stop balance refresh failed for %s: %s",
            trigger.stock_code,
            exc,
        )
        monitor.release_in_flight(trigger.market_code, trigger.stock_code)
        return False

    sell_fx_rate = _extract_fx_rate_from_sources(balance_data)

    if quantity <= 0:
        logger.info(
            "Realtime hard-stop skipped for %s: broker shows no sellable balance",
            trigger.stock_code,
        )
        monitor.remove(trigger.market_code, trigger.stock_code)
        if websocket_client is not None:
            try:
                await websocket_client.unsubscribe(trigger.market_code, trigger.stock_code)
            except Exception as exc:
                logger.warning(
                    "Realtime hard-stop unsubscribe failed for %s (%s): %s",
                    trigger.stock_code,
                    market.name,
                    exc,
                )
        return True

    if _maybe_queue_order_intent(
        market=market,
        session_id=runtime_session_id,
        stock_code=trigger.stock_code,
        order_type="SELL",
        quantity=quantity,
        price=float(order_price),
        source="websocket_hard_stop",
    ):
        monitor.remove(trigger.market_code, trigger.stock_code)
        if websocket_client is not None:
            try:
                await websocket_client.unsubscribe(trigger.market_code, trigger.stock_code)
            except Exception as exc:
                logger.warning(
                    "Realtime hard-stop unsubscribe failed for %s (%s): %s",
                    trigger.stock_code,
                    market.name,
                    exc,
                )
        return True

    try:
        validate_order_policy(
            market=market,
            order_type="SELL",
            price=float(order_price),
        )
        if market.is_domestic:
            result = await broker.send_order(
                stock_code=trigger.stock_code,
                order_type="SELL",
                quantity=quantity,
                price=order_price,
                session_id=runtime_session_id,
            )
        else:
            result = await overseas_broker.send_overseas_order(
                exchange_code=market.exchange_code,
                stock_code=trigger.stock_code,
                order_type="SELL",
                quantity=quantity,
                price=order_price,
            )
        if result.get("rt_cd", "0") != "0":
            logger.warning(
                "Realtime hard-stop SELL rejected for %s: rt_cd=%s msg=%s",
                trigger.stock_code,
                result.get("rt_cd"),
                result.get("msg1"),
            )
            monitor.release_in_flight(trigger.market_code, trigger.stock_code)
            return False
    except Exception as exc:
        logger.warning("Realtime hard-stop handling failed for %s: %s", trigger.stock_code, exc)
        monitor.release_in_flight(trigger.market_code, trigger.stock_code)
        return False

    try:
        decision_id = decision_logger.log_decision(
            stock_code=trigger.stock_code,
            market=market.code,
            exchange_code=market.exchange_code,
            session_id=runtime_session_id,
            action="SELL",
            confidence=95,
            rationale=rationale,
            context_snapshot={
                "realtime_hard_stop": {
                    "source": "websocket_hard_stop",
                    "last_price": current_price,
                    "hard_stop_price": trigger.hard_stop_price,
                    "quantity": quantity,
                }
            },
            input_data={
                "current_price": current_price,
                "hard_stop_price": trigger.hard_stop_price,
                "quantity": quantity,
                "source": "websocket_hard_stop",
            },
        )

        try:
            await telegram.notify_trade_execution(
                stock_code=trigger.stock_code,
                market=market.name,
                action="SELL",
                quantity=quantity,
                price=current_price,
                confidence=95,
            )
        except Exception as exc:
            logger.warning("Telegram notification failed: %s", exc)

        buy_trade = get_latest_buy_trade(
            db_conn,
            trigger.stock_code,
            market.code,
            exchange_code=market.exchange_code,
        )
        buy_price = 0.0
        sell_qty = quantity
        trade_pnl = 0.0
        strategy_pnl: float | None = None
        fx_pnl: float | None = None
        if buy_trade and buy_trade.get("price") is not None:
            buy_price = float(buy_trade["price"])
            buy_qty = int(buy_trade.get("quantity") or 0)
            sell_qty = _resolve_sell_qty_for_pnl(sell_qty=quantity, buy_qty=buy_qty)
            trade_pnl = (current_price - buy_price) * sell_qty
            decision_logger.update_outcome(
                decision_id=buy_trade["decision_id"],
                pnl=trade_pnl,
                accuracy=1 if trade_pnl > 0 else 0,
            )
            if trade_pnl < 0:
                cooldown_key = _stoploss_cooldown_key(market=market, stock_code=trigger.stock_code)
                cooldown_minutes = _stoploss_cooldown_minutes(settings, market=market)
                _STOPLOSS_REENTRY_COOLDOWN_UNTIL[cooldown_key] = (
                    datetime.now(UTC).timestamp() + cooldown_minutes * 60
                )
            buy_fx_rate = _extract_buy_fx_rate(buy_trade)
            strategy_pnl, fx_pnl = _split_trade_pnl_components(
                market=market,
                trade_pnl=trade_pnl,
                buy_price=buy_price,
                sell_price=current_price,
                quantity=sell_qty,
                buy_fx_rate=buy_fx_rate,
                sell_fx_rate=sell_fx_rate,
            )

        selection_context: dict[str, Any] = {
            "source": "websocket_hard_stop",
            "hard_stop_price": trigger.hard_stop_price,
        }
        if sell_fx_rate is not None and not market.is_domestic:
            selection_context["fx_rate"] = sell_fx_rate

        log_trade(
            conn=db_conn,
            stock_code=trigger.stock_code,
            action="SELL",
            confidence=95,
            rationale=rationale,
            quantity=quantity,
            price=current_price,
            pnl=trade_pnl,
            strategy_pnl=strategy_pnl,
            fx_pnl=fx_pnl,
            market=market.code,
            exchange_code=market.exchange_code,
            session_id=runtime_session_id,
            selection_context=selection_context,
            decision_id=decision_id,
            mode=settings.MODE if settings else "paper",
        )
    except Exception as exc:
        logger.warning(
            "Realtime hard-stop post-submit handling failed for %s: %s",
            trigger.stock_code,
            exc,
        )
    finally:
        monitor.remove(trigger.market_code, trigger.stock_code)
        if websocket_client is not None:
            try:
                await websocket_client.unsubscribe(trigger.market_code, trigger.stock_code)
            except Exception as exc:
                logger.warning(
                    "Realtime hard-stop unsubscribe failed for %s (%s): %s",
                    trigger.stock_code,
                    market.name,
                    exc,
                )
    return True


async def _handle_realtime_price_event(
    *,
    event: KISWebSocketPriceEvent,
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    db_conn: Any,
    decision_logger: DecisionLogger,
    telegram: TelegramClient,
    settings: Settings | None,
    monitor: RealtimeHardStopMonitor,
    websocket_client: KISWebSocketClient | None,
) -> None:
    """Apply favorable-exit peak hints before evaluating realtime hard-stop triggers."""
    tracked = monitor.get(event.market_code, event.stock_code)
    if tracked is not None:
        update_runtime_exit_peak(
            market_code=event.market_code,
            stock_code=event.stock_code,
            decision_id=tracked.decision_id,
            position_timestamp=tracked.position_timestamp,
            entry_price=tracked.entry_price,
            last_price=float(event.price),
        )

    trigger = monitor.evaluate_price(event.market_code, event.stock_code, event.price)
    if trigger is None:
        return

    await _handle_realtime_hard_stop_trigger(
        broker=broker,
        overseas_broker=overseas_broker,
        db_conn=db_conn,
        decision_logger=decision_logger,
        telegram=telegram,
        settings=settings,
        monitor=monitor,
        websocket_client=websocket_client,
        trigger=trigger,
    )


def _restart_realtime_hard_stop_task_if_needed(
    *,
    client: KISWebSocketClient | None,
    task: asyncio.Task[None] | None,
) -> asyncio.Task[None] | None:
    """Restart websocket hard-stop monitoring when the background task exits."""
    if client is None:
        return None
    if task is not None and not task.done():
        return task

    if task is not None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            exc = None
        if exc is not None:
            logger.warning("Realtime hard-stop websocket task exited with error: %s", exc)
        else:
            logger.warning("Realtime hard-stop websocket task exited; restarting monitor")

    return asyncio.create_task(client.run())


def _acquire_live_runtime_lock(settings: Settings) -> Any:
    """Prevent duplicate live runtimes from starting on the same host."""
    if settings.MODE != "live":
        return None

    lock_path = Path("data/overnight/live_runtime.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise RuntimeError("another live runtime is already active") from exc

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def _release_live_runtime_lock(lock_file: Any) -> None:
    """Release the live runtime singleton lock."""
    if lock_file is None:
        return
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


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


TRADE_INTERVAL_SECONDS = 60
SCAN_INTERVAL_SECONDS = 60  # Scan markets every 60 seconds
MAX_CONNECTION_RETRIES = 3
_BUY_COOLDOWN_SECONDS = 600  # 10-minute cooldown after insufficient-balance rejection

# Daily trading mode constants (for Free tier API efficiency)
DAILY_TRADE_SESSIONS = 4  # Number of trading sessions per day
TRADE_SESSION_INTERVAL_HOURS = 6  # Hours between sessions


async def _retry_connection(coro_factory: Any, *args: Any, label: str = "", **kwargs: Any) -> Any:
    """Call an async function retrying on ConnectionError with exponential backoff.

    Retries up to MAX_CONNECTION_RETRIES times (exclusive of the first attempt),
    sleeping 2^attempt seconds between attempts.  Use only for idempotent read
    operations — never for order submission.

    Args:
        coro_factory: Async callable (method or function) to invoke.
        *args: Positional arguments forwarded to coro_factory.
        label: Human-readable label for log messages.
        **kwargs: Keyword arguments forwarded to coro_factory.

    Raises:
        ConnectionError: If all retries are exhausted.
    """
    for attempt in range(1, MAX_CONNECTION_RETRIES + 1):
        try:
            return await coro_factory(*args, **kwargs)
        except ConnectionError as exc:
            if attempt < MAX_CONNECTION_RETRIES:
                wait_secs = 2**attempt
                logger.warning(
                    "Connection error %s (attempt %d/%d), retrying in %ds: %s",
                    label,
                    attempt,
                    MAX_CONNECTION_RETRIES,
                    wait_secs,
                    exc,
                )
                await asyncio.sleep(wait_secs)
            else:
                logger.error(
                    "Connection error %s — all %d retries exhausted: %s",
                    label,
                    MAX_CONNECTION_RETRIES,
                    exc,
                )
                raise


async def sync_positions_from_broker(
    broker: Any,
    overseas_broker: Any,
    db_conn: Any,
    settings: Settings,
) -> int:
    """Sync open positions from the live broker into the local DB at startup.

    Fetches current holdings from the broker for all configured markets and
    inserts a synthetic BUY record for any position that the DB does not
    already know about.  This prevents double-buy when positions were opened
    in a previous session or entered manually outside the system.

    Returns:
        Number of new positions synced.
    """
    synced = 0
    seen_exchange_codes: set[str] = set()

    for market_code in settings.enabled_market_list:
        market = MARKETS.get(market_code)
        if market is None:
            continue

        try:
            if market.is_domestic:
                balance_data = await broker.get_balance()
                log_market = market_code  # "KR"
            else:
                if market.exchange_code in seen_exchange_codes:
                    continue
                seen_exchange_codes.add(market.exchange_code)
                balance_data = await overseas_broker.get_overseas_balance(market.exchange_code)
                log_market = market_code  # e.g. "US_NASDAQ"
        except ConnectionError as exc:
            logger.warning(
                "Startup sync: balance fetch failed for %s — skipping: %s",
                market_code,
                exc,
            )
            continue

        held_codes = _extract_held_codes_from_balance(
            balance_data,
            is_domestic=market.is_domestic,
            exchange_code=None if market.is_domestic else market.exchange_code,
        )
        for stock_code in held_codes:
            if get_open_position(db_conn, stock_code, log_market):
                continue  # already tracked
            qty = _extract_held_qty_from_balance(
                balance_data, stock_code, is_domestic=market.is_domestic
            )
            avg_price = _extract_avg_price_from_balance(
                balance_data, stock_code, is_domestic=market.is_domestic
            )
            log_trade(
                conn=db_conn,
                stock_code=stock_code,
                action="BUY",
                confidence=0,
                rationale="[startup-sync] Position detected from broker at startup",
                quantity=qty,
                price=avg_price,
                market=log_market,
                exchange_code=market.exchange_code,
                session_id=get_session_info(market).session_id,
                mode=settings.MODE,
            )
            logger.info(
                "Startup sync: %s/%s recorded as open position (qty=%d)",
                log_market,
                stock_code,
                qty,
            )
            synced += 1

    if synced:
        logger.info("Startup sync complete: %d position(s) synced from broker", synced)
    else:
        logger.info("Startup sync: no new positions to sync from broker")
    return synced


async def build_overseas_symbol_universe(
    db_conn: Any,
    overseas_broker: OverseasBroker,
    market: MarketInfo,
    active_stocks: dict[str, list[str]],
) -> list[str]:
    """Build dynamic overseas symbol universe from runtime, DB, and holdings."""
    symbols: list[str] = []

    # 1) Keep current active stocks first to avoid sudden churn between cycles.
    symbols.extend(active_stocks.get(market.code, []))

    # 2) Add recent symbols from own trading history (no fixed list).
    symbols.extend(get_recent_symbols(db_conn, market.code, limit=30))

    # 3) Add current overseas holdings from broker balance if available.
    try:
        balance_data = await overseas_broker.get_overseas_balance(market.exchange_code)
        symbols.extend(
            _extract_held_codes_from_balance(
                balance_data,
                is_domestic=False,
                exchange_code=market.exchange_code,
            )
        )
    except Exception as exc:
        logger.warning("Failed to build overseas holdings universe for %s: %s", market.code, exc)

    seen: set[str] = set()
    ordered_unique: list[str] = []
    for symbol in symbols:
        normalized = symbol.strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered_unique.append(normalized)
    return ordered_unique


async def _collect_trading_cycle_market_snapshot(
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    db_conn: Any,
    context_store: ContextStore,
    criticality_assessor: CriticalityAssessor,
    market: MarketInfo,
    stock_code: str,
    scan_candidates: dict[str, dict[str, ScanCandidate]],
    runtime_session_id: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    balance_info: dict[str, Any] = {}
    price_output: dict[str, Any] = {}
    if market.is_domestic:
        quote_market_div_code = _resolve_domestic_quote_market_div_code(runtime_session_id)
        current_price, price_change_pct, foreigner_net = await broker.get_current_price(
            stock_code,
            market_div_code=quote_market_div_code,
        )
        balance_data = await broker.get_balance()

        output2 = balance_data.get("output2", [{}])
        total_eval = safe_float(output2[0].get("tot_evlu_amt", "0")) if output2 else 0
        total_cash = safe_float(
            balance_data.get("output2", [{}])[0].get("dnca_tot_amt", "0") if output2 else "0"
        )
        purchase_total = safe_float(output2[0].get("pchs_amt_smtl_amt", "0")) if output2 else 0
    else:
        price_data = await overseas_broker.get_overseas_price(market.exchange_code, stock_code)
        balance_data = await overseas_broker.get_overseas_balance(market.exchange_code)

        output2 = balance_data.get("output2", [{}])
        if isinstance(output2, list) and output2:
            balance_info = output2[0]
        elif isinstance(output2, dict):
            balance_info = output2

        total_eval = safe_float(balance_info.get("frcr_evlu_tota", "0") or "0")
        purchase_total = safe_float(balance_info.get("frcr_buy_amt_smtl", "0") or "0")

        price_output = price_data.get("output", {})
        current_price = safe_float(price_output.get("last", "0"))
        if current_price <= 0:
            market_candidates_lookup = scan_candidates.get(market.code, {})
            cand_lookup = market_candidates_lookup.get(stock_code)
            if cand_lookup and cand_lookup.price > 0:
                logger.debug(
                    "Price API returned 0 for %s; using scanner candidate price %.4f",
                    stock_code,
                    cand_lookup.price,
                )
                current_price = cand_lookup.price
        foreigner_net = 0.0
        price_change_pct = safe_float(price_output.get("rate", "0"))

        total_cash = 0.0
        if current_price > 0:
            try:
                ps_data = await overseas_broker.get_overseas_buying_power(
                    market.exchange_code, stock_code, current_price
                )
                total_cash = safe_float(
                    ps_data.get("output", {}).get("ovrs_ord_psbl_amt", "0") or "0"
                )
            except ConnectionError as exc:
                logger.warning(
                    "Could not fetch overseas buying power for %s/%s: %s",
                    market.exchange_code,
                    stock_code,
                    exc,
                )

        if (
            total_cash <= 0
            and settings
            and settings.MODE == "paper"
            and settings.PAPER_OVERSEAS_CASH > 0
        ):
            logger.debug(
                "Overseas cash balance is 0 for %s; using paper fallback %.2f USD",
                market.exchange_code,
                settings.PAPER_OVERSEAS_CASH,
            )
            total_cash = settings.PAPER_OVERSEAS_CASH

    pnl_pct = ((total_eval - purchase_total) / purchase_total * 100) if purchase_total > 0 else 0.0
    market_data: dict[str, Any] = {
        "stock_code": stock_code,
        "market_name": market.name,
        "current_price": current_price,
        "foreigner_net": foreigner_net,
        "price_change_pct": price_change_pct,
    }
    session_high_price = safe_float(
        price_output.get("high") or price_output.get("ovrs_hgpr") or price_output.get("stck_hgpr")
    )
    if session_high_price > 0:
        market_data["session_high_price"] = session_high_price

    market_candidates = scan_candidates.get(market.code, {})
    candidate = market_candidates.get(stock_code)
    if candidate:
        market_data["rsi"] = candidate.rsi
        market_data["volume_ratio"] = candidate.volume_ratio
    else:
        market_data["rsi"] = max(0.0, min(100.0, 50.0 + price_change_pct * 2.0))
        if price_output and current_price > 0:
            pr_high = safe_float(
                price_output.get("high")
                or price_output.get("ovrs_hgpr")
                or price_output.get("stck_hgpr")
            )
            pr_low = safe_float(
                price_output.get("low")
                or price_output.get("ovrs_lwpr")
                or price_output.get("stck_lwpr")
            )
            if pr_high > 0 and pr_low > 0 and pr_high >= pr_low:
                intraday_range_pct = (pr_high - pr_low) / current_price * 100.0
                volatility_pct = max(abs(price_change_pct), intraday_range_pct)
                market_data["volume_ratio"] = max(1.0, volatility_pct / 2.0)
            else:
                market_data["volume_ratio"] = 1.0
        else:
            market_data["volume_ratio"] = 1.0

    open_pos = get_open_position(db_conn, stock_code, market.code)
    if open_pos and current_price > 0:
        entry_price = safe_float(open_pos.get("price"), 0.0)
        if entry_price > 0:
            market_data["unrealized_pnl_pct"] = (current_price - entry_price) / entry_price * 100
        entry_ts = open_pos.get("timestamp")
        if entry_ts:
            try:
                entry_date = datetime.fromisoformat(entry_ts).date()
                market_data["holding_days"] = (datetime.now(UTC).date() - entry_date).days
            except (ValueError, TypeError):
                pass

    timeframe = datetime.now(UTC).isoformat()
    context_store.set_context(
        ContextLayer.L7_REALTIME,
        timeframe,
        f"volatility_{market.code}_{stock_code}",
        {
            "momentum_score": 50.0,
            "volume_surge": 1.0,
            "price_change_1m": 0.0,
        },
    )
    context_store.set_context(
        ContextLayer.L7_REALTIME,
        timeframe,
        f"price_{market.code}_{stock_code}",
        {"current_price": current_price},
    )
    if candidate:
        context_store.set_context(
            ContextLayer.L7_REALTIME,
            timeframe,
            f"rsi_{market.code}_{stock_code}",
            {"rsi": candidate.rsi},
        )
        context_store.set_context(
            ContextLayer.L7_REALTIME,
            timeframe,
            f"volume_ratio_{market.code}_{stock_code}",
            {"volume_ratio": candidate.volume_ratio},
        )

    db_conn.execute(
        "INSERT OR REPLACE INTO system_metrics (key, value, updated_at) VALUES (?, ?, ?)",
        (
            f"portfolio_pnl_pct_{market.code}",
            json.dumps({"pnl_pct": round(pnl_pct, 4)}),
            datetime.now(UTC).isoformat(),
        ),
    )
    db_conn.commit()

    portfolio_data = {
        "portfolio_pnl_pct": pnl_pct,
        "total_cash": total_cash,
        "total_eval": total_eval,
    }
    latest_timeframe = context_store.get_latest_timeframe(ContextLayer.L7_REALTIME)
    volatility_score = 50.0
    volume_surge = 1.0
    price_change_1m = 0.0
    if latest_timeframe:
        volatility_data = context_store.get_context(
            ContextLayer.L7_REALTIME,
            latest_timeframe,
            f"volatility_{market.code}_{stock_code}",
        )
        if volatility_data:
            volatility_score = volatility_data.get("momentum_score", 50.0)
            volume_surge = volatility_data.get("volume_surge", 1.0)
            price_change_1m = volatility_data.get("price_change_1m", 0.0)

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

    return {
        "balance_data": balance_data,
        "balance_info": balance_info,
        "price_output": price_output,
        "current_price": current_price,
        "price_change_pct": price_change_pct,
        "foreigner_net": foreigner_net,
        "total_eval": total_eval,
        "total_cash": total_cash,
        "purchase_total": purchase_total,
        "pnl_pct": pnl_pct,
        "market_data": market_data,
        "portfolio_data": portfolio_data,
        "market_candidates": market_candidates,
        "candidate": candidate,
        "criticality": criticality,
    }


async def _evaluate_trading_cycle_decision(
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    scenario_engine: ScenarioEngine,
    playbook: DayPlaybook,
    db_conn: Any,
    decision_logger: DecisionLogger,
    telegram: TelegramClient,
    market: MarketInfo,
    stock_code: str,
    runtime_session_id: str,
    snapshot: dict[str, Any],
    settings: Settings | None = None,
    realtime_hard_stop_monitor: RealtimeHardStopMonitor | None = None,
    realtime_hard_stop_client: KISWebSocketClient | None = None,
) -> dict[str, Any]:
    market_data = snapshot["market_data"]
    portfolio_data = snapshot["portfolio_data"]
    current_price = snapshot["current_price"]
    total_eval = snapshot["total_eval"]
    total_cash = snapshot["total_cash"]
    purchase_total = snapshot["purchase_total"]
    pnl_pct = snapshot["pnl_pct"]
    foreigner_net = snapshot["foreigner_net"]
    price_change_pct = snapshot["price_change_pct"]
    balance_data = snapshot["balance_data"]

    match = scenario_engine.evaluate(playbook, stock_code, market_data, portfolio_data)
    decision = TradeDecision(
        action=match.action.value,
        confidence=match.confidence,
        rationale=match.rationale,
    )
    stock_playbook = playbook.get_stock_playbook(stock_code)

    if decision.action == "BUY":
        base_threshold = int(
            _resolve_market_setting(
                market=market,
                settings=settings,
                key="CONFIDENCE_THRESHOLD",
                default=80,
            )
        )
        outlook = playbook.market_outlook
        if outlook == MarketOutlook.BEARISH:
            min_confidence = 90
        elif outlook == MarketOutlook.BULLISH:
            min_confidence = 75
        else:
            min_confidence = base_threshold
        if match.confidence < min_confidence:
            logger.info(
                "BUY suppressed for %s (%s): confidence %d < %d (market_outlook=%s)",
                stock_code,
                market.name,
                match.confidence,
                min_confidence,
                outlook.value,
            )
            decision = TradeDecision(
                action="HOLD",
                confidence=match.confidence,
                rationale=(
                    f"BUY confidence {match.confidence} < {min_confidence} "
                    f"(market_outlook={outlook.value})"
                ),
            )

    if decision.action == "BUY":
        existing_position = _resolve_buy_suppression_position(
            db_conn=db_conn,
            balance_data=balance_data,
            stock_code=stock_code,
            market=market,
        )
        if existing_position:
            decision = TradeDecision(
                action="HOLD",
                confidence=decision.confidence,
                rationale=(
                    f"Already holding {stock_code} "
                    f"(entry={existing_position['price']:.4f}, "
                    f"qty={existing_position['quantity']})"
                ),
            )
            logger.info(
                "BUY suppressed for %s (%s): already holding open position",
                stock_code,
                market.name,
            )
        elif market.code.startswith("US"):
            min_price = float(
                _resolve_market_setting(
                    market=market,
                    settings=settings,
                    key="US_MIN_PRICE",
                    default=5.0,
                )
            )
            if current_price <= min_price:
                decision = TradeDecision(
                    action="HOLD",
                    confidence=decision.confidence,
                    rationale=(
                        f"US minimum price filter blocked BUY "
                        f"(price={current_price:.4f} <= {min_price:.4f})"
                    ),
                )
                logger.info(
                    "BUY suppressed for %s (%s): US min price filter %.4f <= %.4f",
                    stock_code,
                    market.name,
                    current_price,
                    min_price,
                )
        if decision.action == "BUY":
            cooldown_key = _stoploss_cooldown_key(market=market, stock_code=stock_code)
            now_epoch = datetime.now(UTC).timestamp()
            cooldown_until = _STOPLOSS_REENTRY_COOLDOWN_UNTIL.get(cooldown_key, 0.0)
            if now_epoch < cooldown_until:
                remaining = int(cooldown_until - now_epoch)
                decision = TradeDecision(
                    action="HOLD",
                    confidence=decision.confidence,
                    rationale=f"Stop-loss reentry cooldown active ({remaining}s remaining)",
                )
                logger.info(
                    "BUY suppressed for %s (%s): stop-loss cooldown active (%ds remaining)",
                    stock_code,
                    market.name,
                    remaining,
                )

    if decision.action == "HOLD":
        open_position = get_open_position(db_conn, stock_code, market.code)
        if not open_position:
            _clear_runtime_exit_cache_for_symbol(
                market_code=market.code,
                stock_code=stock_code,
            )
        await _inject_staged_exit_features(
            market=market,
            stock_code=stock_code,
            open_position=open_position,
            market_data=market_data,
            broker=broker,
            overseas_broker=overseas_broker,
        )
        decision = _apply_staged_exit_override_for_hold(
            decision=decision,
            market=market,
            stock_code=stock_code,
            open_position=open_position,
            market_data=market_data,
            stock_playbook=stock_playbook,
            settings=settings,
        )
        if (
            open_position
            and decision.action == "HOLD"
            and _should_force_exit_for_overnight(
                market=market,
                settings=settings,
            )
        ):
            decision = TradeDecision(
                action="SELL",
                confidence=max(decision.confidence, 85),
                rationale=(
                    "Forced exit by overnight policy (session close window / kill switch priority)"
                ),
            )
            logger.info(
                "Overnight policy override for %s (%s): HOLD -> SELL",
                stock_code,
                market.name,
            )
        await _sync_realtime_hard_stop_monitor(
            monitor=realtime_hard_stop_monitor,
            websocket_client=realtime_hard_stop_client,
            market=market,
            stock_code=stock_code,
            decision_action=decision.action,
            open_position=open_position,
            market_data=market_data,
        )
    else:
        await _sync_realtime_hard_stop_monitor(
            monitor=realtime_hard_stop_monitor,
            websocket_client=realtime_hard_stop_client,
            market=market,
            stock_code=stock_code,
            decision_action=decision.action,
            open_position=None,
            market_data=market_data,
        )
    logger.info(
        "Decision for %s (%s): %s (confidence=%d)",
        stock_code,
        market.name,
        decision.action,
        decision.confidence,
    )

    if match.matched_scenario is not None:
        try:
            condition_parts = [f"{k}={v}" for k, v in match.match_details.items()]
            await telegram.notify_scenario_matched(
                stock_code=stock_code,
                action=decision.action,
                condition_summary=", ".join(condition_parts) if condition_parts else "matched",
                confidence=float(decision.confidence),
            )
        except Exception as exc:
            logger.warning("Scenario matched notification failed: %s", exc)

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
        "scenario_match": match.match_details,
    }
    input_data = {
        "current_price": current_price,
        "foreigner_net": foreigner_net,
        "price_change_pct": price_change_pct,
        "total_eval": total_eval,
        "total_cash": total_cash,
        "pnl_pct": pnl_pct,
    }
    _merge_staged_exit_evidence_into_log(
        market_data=market_data,
        context_snapshot=context_snapshot,
        input_data=input_data,
    )
    decision_id = decision_logger.log_decision(
        stock_code=stock_code,
        market=market.code,
        exchange_code=market.exchange_code,
        session_id=runtime_session_id,
        action=decision.action,
        confidence=decision.confidence,
        rationale=decision.rationale,
        context_snapshot=context_snapshot,
        input_data=input_data,
    )
    return {
        "decision": decision,
        "match": match,
        "decision_id": decision_id,
    }


async def _execute_trading_cycle_action(
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    risk: RiskManager,
    db_conn: Any,
    decision_logger: DecisionLogger,
    telegram: TelegramClient,
    market: MarketInfo,
    stock_code: str,
    runtime_session_id: str,
    snapshot: dict[str, Any],
    decision_data: dict[str, Any],
    settings: Settings | None = None,
    buy_cooldown: dict[str, float] | None = None,
    realtime_hard_stop_monitor: RealtimeHardStopMonitor | None = None,
    realtime_hard_stop_client: KISWebSocketClient | None = None,
) -> dict[str, Any]:
    decision = decision_data["decision"]
    match = decision_data["match"]
    current_price = snapshot["current_price"]
    total_cash = snapshot["total_cash"]
    pnl_pct = snapshot["pnl_pct"]
    candidate = snapshot["candidate"]
    balance_data = snapshot["balance_data"]

    execution_result: dict[str, Any] = {
        "should_return": False,
        "order_succeeded": True,
        "quantity": 0,
        "trade_price": current_price,
        "trade_pnl": 0.0,
        "buy_trade": None,
        "buy_price": 0.0,
        "sell_qty": 0,
    }
    if decision.action not in ("BUY", "SELL"):
        return execution_result

    if KILL_SWITCH.new_orders_blocked and decision.action == "BUY":
        logger.critical(
            "KillSwitch block active: skip %s order for %s (%s)",
            decision.action,
            stock_code,
            market.name,
        )
        execution_result["should_return"] = True
        return execution_result

    broker_held_qty = (
        _extract_held_qty_from_balance(balance_data, stock_code, is_domestic=market.is_domestic)
        if decision.action == "SELL"
        else 0
    )
    matched_scenario = match.matched_scenario
    quantity = _determine_order_quantity(
        action=decision.action,
        current_price=current_price,
        total_cash=total_cash,
        candidate=candidate,
        settings=settings,
        broker_held_qty=broker_held_qty,
        playbook_allocation_pct=matched_scenario.allocation_pct if matched_scenario else None,
        scenario_confidence=match.confidence,
    )
    execution_result["quantity"] = quantity
    if quantity <= 0:
        logger.info(
            "Skip %s %s (%s): no affordable quantity (cash=%.2f, price=%.2f)",
            decision.action,
            stock_code,
            market.name,
            total_cash,
            current_price,
        )
        execution_result["should_return"] = True
        return execution_result

    order_amount = current_price * quantity
    fx_blocked, remaining_cash, required_buffer = _should_block_overseas_buy_for_fx_buffer(
        market=market,
        action=decision.action,
        total_cash=total_cash,
        order_amount=order_amount,
        settings=settings,
    )
    if fx_blocked:
        logger.warning(
            (
                "Skip BUY %s (%s): FX buffer guard "
                "(remaining=%.2f, required=%.2f, cash=%.2f, order=%.2f)"
            ),
            stock_code,
            market.name,
            remaining_cash,
            required_buffer,
            total_cash,
            order_amount,
        )
        execution_result["should_return"] = True
        return execution_result

    if decision.action == "BUY" and buy_cooldown is not None:
        cooldown_key = f"{market.code}:{stock_code}"
        cooldown_until = buy_cooldown.get(cooldown_key, 0.0)
        now = asyncio.get_event_loop().time()
        if now < cooldown_until:
            remaining = int(cooldown_until - now)
            logger.info(
                "Skip BUY %s (%s): insufficient-balance cooldown active (%ds remaining)",
                stock_code,
                market.name,
                remaining,
            )
            execution_result["should_return"] = True
            return execution_result

    try:
        if decision.action == "SELL":
            risk.check_circuit_breaker(pnl_pct)
        else:
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
        raise
    except CircuitBreakerTripped as exc:
        ks_report = await _trigger_emergency_kill_switch(
            reason=f"circuit_breaker:{market.code}:{stock_code}:{exc.pnl_pct:.2f}",
            broker=broker,
            overseas_broker=overseas_broker,
            telegram=telegram,
            settings=settings,
            current_market=market,
            stock_code=stock_code,
            pnl_pct=exc.pnl_pct,
            threshold=exc.threshold,
        )
        if ks_report.errors:
            logger.critical(
                "KillSwitch step errors for %s/%s: %s",
                market.code,
                stock_code,
                "; ".join(ks_report.errors),
            )
        raise

    order_succeeded = True
    if market.is_domestic:
        if decision.action == "BUY":
            order_price = kr_round_down(current_price * 1.002)
        else:
            order_price = kr_round_down(current_price * 0.998)
        try:
            validate_order_policy(
                market=market,
                order_type=decision.action,
                price=float(order_price),
            )
        except OrderPolicyRejected as exc:
            logger.warning(
                "Order policy rejected %s %s (%s): %s [session=%s]",
                decision.action,
                stock_code,
                market.name,
                exc,
                exc.session_id,
            )
            execution_result["should_return"] = True
            return execution_result
        if _maybe_queue_order_intent(
            market=market,
            session_id=runtime_session_id,
            stock_code=stock_code,
            order_type=decision.action,
            quantity=quantity,
            price=float(order_price),
            source="trading_cycle",
        ):
            execution_result["should_return"] = True
            return execution_result
        result = await broker.send_order(
            stock_code=stock_code,
            order_type=decision.action,
            quantity=quantity,
            price=order_price,
            session_id=runtime_session_id,
        )
        if result.get("rt_cd", "0") != "0":
            order_succeeded = False
            msg1 = result.get("msg1") or ""
            logger.warning(
                "KR order not accepted for %s: rt_cd=%s msg=%s",
                stock_code,
                result.get("rt_cd"),
                msg1,
            )
    else:
        _price_decimals = 2 if current_price >= 1.0 else 4
        if decision.action == "BUY":
            overseas_price = round(current_price * 1.002, _price_decimals)
        else:
            overseas_price = round(current_price * 0.998, _price_decimals)
        try:
            validate_order_policy(
                market=market,
                order_type=decision.action,
                price=float(overseas_price),
            )
        except OrderPolicyRejected as exc:
            logger.warning(
                "Order policy rejected %s %s (%s): %s [session=%s]",
                decision.action,
                stock_code,
                market.name,
                exc,
                exc.session_id,
            )
            execution_result["should_return"] = True
            return execution_result
        if _maybe_queue_order_intent(
            market=market,
            session_id=runtime_session_id,
            stock_code=stock_code,
            order_type=decision.action,
            quantity=quantity,
            price=float(overseas_price),
            source="trading_cycle",
        ):
            execution_result["should_return"] = True
            return execution_result
        result = await overseas_broker.send_overseas_order(
            exchange_code=market.exchange_code,
            stock_code=stock_code,
            order_type=decision.action,
            quantity=quantity,
            price=overseas_price,
        )
        if result.get("rt_cd", "") != "0":
            order_succeeded = False
            msg1 = result.get("msg1") or ""
            logger.warning(
                "Overseas order not accepted for %s: rt_cd=%s msg=%s",
                stock_code,
                result.get("rt_cd"),
                msg1,
            )
            if decision.action == "BUY" and buy_cooldown is not None and "주문가능금액" in msg1:
                cooldown_key = f"{market.code}:{stock_code}"
                buy_cooldown[cooldown_key] = asyncio.get_event_loop().time() + _BUY_COOLDOWN_SECONDS
                logger.info(
                    "BUY cooldown set for %s: %.0fs (insufficient balance)",
                    stock_code,
                    _BUY_COOLDOWN_SECONDS,
                )
            if decision.action == "SELL" and "잔고내역이 없습니다" in msg1:
                logger.warning(
                    "Ghost position detected for %s (%s): broker reports no balance."
                    " Closing DB position to prevent infinite retry.",
                    stock_code,
                    market.exchange_code,
                )
                log_trade(
                    conn=db_conn,
                    stock_code=stock_code,
                    action="SELL",
                    confidence=0,
                    rationale=(
                        "[ghost-close] Broker reported no balance; position closed without fill"
                    ),
                    quantity=0,
                    price=0.0,
                    pnl=0.0,
                    market=market.code,
                    exchange_code=market.exchange_code,
                    session_id=runtime_session_id,
                    mode=settings.MODE if settings else "paper",
                )
    logger.info("Order result: %s", result.get("msg1", "OK"))

    execution_result["order_succeeded"] = order_succeeded
    if order_succeeded:
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

    if decision.action == "SELL" and order_succeeded:
        await _clear_realtime_hard_stop_tracking(
            monitor=realtime_hard_stop_monitor,
            websocket_client=realtime_hard_stop_client,
            market=market,
            stock_code=stock_code,
        )
        buy_trade = get_latest_buy_trade(
            db_conn,
            stock_code,
            market.code,
            exchange_code=market.exchange_code,
        )
        execution_result["buy_trade"] = buy_trade
        if buy_trade and buy_trade.get("price") is not None:
            buy_price = float(buy_trade["price"])
            buy_qty = int(buy_trade.get("quantity") or 0)
            sell_qty = _resolve_sell_qty_for_pnl(sell_qty=quantity, buy_qty=buy_qty)
            trade_pnl = (current_price - buy_price) * sell_qty
            decision_logger.update_outcome(
                decision_id=buy_trade["decision_id"],
                pnl=trade_pnl,
                accuracy=1 if trade_pnl > 0 else 0,
            )
            execution_result["buy_price"] = buy_price
            execution_result["sell_qty"] = sell_qty
            execution_result["trade_pnl"] = trade_pnl
            if trade_pnl < 0:
                cooldown_key = _stoploss_cooldown_key(market=market, stock_code=stock_code)
                cooldown_minutes = _stoploss_cooldown_minutes(settings, market=market)
                _STOPLOSS_REENTRY_COOLDOWN_UNTIL[cooldown_key] = (
                    datetime.now(UTC).timestamp() + cooldown_minutes * 60
                )
                logger.info(
                    "Stop-loss cooldown set for %s (%s): %d minutes",
                    stock_code,
                    market.name,
                    cooldown_minutes,
                )
    return execution_result


def _log_trading_cycle_trade(
    db_conn: Any,
    market: MarketInfo,
    stock_code: str,
    runtime_session_id: str,
    snapshot: dict[str, Any],
    decision_data: dict[str, Any],
    execution_result: dict[str, Any],
    settings: Settings | None = None,
) -> None:
    decision = decision_data["decision"]
    decision_id = decision_data["decision_id"]
    if decision.action in ("BUY", "SELL") and not execution_result["order_succeeded"]:
        return

    market_candidates = snapshot["market_candidates"]
    balance_info = snapshot["balance_info"]
    price_output = snapshot["price_output"]
    candidate = market_candidates.get(stock_code)
    selection_context = None
    if candidate:
        selection_context = {
            "rsi": candidate.rsi,
            "volume_ratio": candidate.volume_ratio,
            "signal": candidate.signal,
            "score": candidate.score,
        }
    sell_fx_rate = _extract_fx_rate_from_sources(price_output, balance_info)
    if sell_fx_rate is not None and not market.is_domestic:
        if selection_context is None:
            selection_context = {"fx_rate": sell_fx_rate}
        else:
            selection_context["fx_rate"] = sell_fx_rate

    strategy_pnl: float | None = None
    fx_pnl: float | None = None
    if decision.action == "SELL" and execution_result["order_succeeded"]:
        buy_fx_rate = _extract_buy_fx_rate(execution_result["buy_trade"])
        strategy_pnl, fx_pnl = _split_trade_pnl_components(
            market=market,
            trade_pnl=execution_result["trade_pnl"],
            buy_price=execution_result["buy_price"],
            sell_price=execution_result["trade_price"],
            quantity=execution_result["sell_qty"] or execution_result["quantity"],
            buy_fx_rate=buy_fx_rate,
            sell_fx_rate=sell_fx_rate,
        )

    log_trade(
        conn=db_conn,
        stock_code=stock_code,
        action=decision.action,
        confidence=decision.confidence,
        rationale=decision.rationale,
        quantity=execution_result["quantity"],
        price=execution_result["trade_price"],
        pnl=execution_result["trade_pnl"],
        strategy_pnl=strategy_pnl,
        fx_pnl=fx_pnl,
        market=market.code,
        exchange_code=market.exchange_code,
        session_id=runtime_session_id,
        selection_context=selection_context,
        decision_id=decision_id,
        mode=settings.MODE if settings else "paper",
    )


async def trading_cycle(
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    scenario_engine: ScenarioEngine,
    playbook: DayPlaybook,
    risk: RiskManager,
    db_conn: Any,
    decision_logger: DecisionLogger,
    context_store: ContextStore,
    criticality_assessor: CriticalityAssessor,
    telegram: TelegramClient,
    market: MarketInfo,
    stock_code: str,
    scan_candidates: dict[str, dict[str, ScanCandidate]],
    settings: Settings | None = None,
    buy_cooldown: dict[str, float] | None = None,
    realtime_hard_stop_monitor: RealtimeHardStopMonitor | None = None,
    realtime_hard_stop_client: KISWebSocketClient | None = None,
) -> None:
    """Execute one trading cycle for a single stock."""
    cycle_start_time = asyncio.get_event_loop().time()
    _session_risk_overrides(market=market, settings=settings)
    runtime_session_id = get_session_info(market).session_id

    snapshot = await _collect_trading_cycle_market_snapshot(
        broker=broker,
        overseas_broker=overseas_broker,
        db_conn=db_conn,
        context_store=context_store,
        criticality_assessor=criticality_assessor,
        market=market,
        stock_code=stock_code,
        scan_candidates=scan_candidates,
        runtime_session_id=runtime_session_id,
        settings=settings,
    )
    decision_data = await _evaluate_trading_cycle_decision(
        broker=broker,
        overseas_broker=overseas_broker,
        scenario_engine=scenario_engine,
        playbook=playbook,
        db_conn=db_conn,
        decision_logger=decision_logger,
        telegram=telegram,
        market=market,
        stock_code=stock_code,
        runtime_session_id=runtime_session_id,
        snapshot=snapshot,
        settings=settings,
        realtime_hard_stop_monitor=realtime_hard_stop_monitor,
        realtime_hard_stop_client=realtime_hard_stop_client,
    )
    execution_result = await _execute_trading_cycle_action(
        broker=broker,
        overseas_broker=overseas_broker,
        risk=risk,
        db_conn=db_conn,
        decision_logger=decision_logger,
        telegram=telegram,
        market=market,
        stock_code=stock_code,
        runtime_session_id=runtime_session_id,
        snapshot=snapshot,
        decision_data=decision_data,
        settings=settings,
        buy_cooldown=buy_cooldown,
        realtime_hard_stop_monitor=realtime_hard_stop_monitor,
        realtime_hard_stop_client=realtime_hard_stop_client,
    )
    if execution_result["should_return"]:
        return

    _log_trading_cycle_trade(
        db_conn=db_conn,
        market=market,
        stock_code=stock_code,
        runtime_session_id=runtime_session_id,
        snapshot=snapshot,
        decision_data=decision_data,
        execution_result=execution_result,
        settings=settings,
    )

    cycle_end_time = asyncio.get_event_loop().time()
    cycle_latency = cycle_end_time - cycle_start_time
    timeout = criticality_assessor.get_timeout(snapshot["criticality"])

    if timeout and cycle_latency > timeout:
        logger.warning(
            "Trading cycle exceeded timeout for %s (criticality=%s, latency=%.2fs, timeout=%.2fs)",
            stock_code,
            snapshot["criticality"].value,
            cycle_latency,
            timeout,
        )
    else:
        logger.debug(
            "Trading cycle completed within timeout for %s (criticality=%s, latency=%.2fs)",
            stock_code,
            snapshot["criticality"].value,
            cycle_latency,
        )


async def _load_daily_session_market_candidates(
    *,
    db_conn: Any,
    market: MarketInfo,
    overseas_broker: OverseasBroker,
    smart_scanner: SmartVolatilityScanner | None,
) -> list[ScanCandidate]:
    """Load scanner candidates for one market in the daily session path."""
    fallback_stocks: list[str] | None = None
    if not market.is_domestic:
        fallback_stocks = await build_overseas_symbol_universe(
            db_conn=db_conn,
            overseas_broker=overseas_broker,
            market=market,
            active_stocks={},
        )
        if not fallback_stocks:
            logger.debug(
                "No dynamic overseas symbol universe for %s;"
                " scanner will use overseas ranking API",
                market.code,
            )

    if not smart_scanner:
        return []

    try:
        return await smart_scanner.scan(
            market=market,
            fallback_stocks=fallback_stocks,
        )
    except Exception as exc:
        logger.error("Smart Scanner failed for %s: %s", market.name, exc)
        return []


async def _prepare_daily_session_market(
    *,
    broker: KISBroker,
    db_conn: Any,
    market: MarketInfo,
    overseas_broker: OverseasBroker,
    settings: Settings,
    telegram: TelegramClient,
    sell_resubmit_counts: dict[str, int],
    daily_buy_cooldown: dict[str, float],
) -> tuple[str, str, date]:
    """Run market-level preparation before daily decisions."""
    _session_risk_overrides(market=market, settings=settings)
    runtime_session_id = get_session_info(market).session_id
    domestic_quote_market_div_code = _resolve_domestic_quote_market_div_code(runtime_session_id)
    await process_blackout_recovery_orders(
        broker=broker,
        overseas_broker=overseas_broker,
        db_conn=db_conn,
        settings=settings,
    )
    market_today = datetime.now(market.timezone).date()

    if market.is_domestic:
        try:
            await handle_domestic_pending_orders(
                broker,
                telegram,
                settings,
                sell_resubmit_counts,
                daily_buy_cooldown,
                quote_market_div_code=domestic_quote_market_div_code,
                rollback_open_position=lambda **kwargs: _rollback_pending_order_position(
                    db_conn=db_conn,
                    runtime_session_id=runtime_session_id,
                    settings=settings,
                    **kwargs,
                ),
            )
        except Exception as exc:
            logger.warning("Domestic pending order check failed: %s", exc)
    else:
        try:
            await handle_overseas_pending_orders(
                overseas_broker,
                telegram,
                settings,
                sell_resubmit_counts,
                daily_buy_cooldown,
                rollback_open_position=lambda **kwargs: _rollback_pending_order_position(
                    db_conn=db_conn,
                    runtime_session_id=runtime_session_id,
                    settings=settings,
                    **kwargs,
                ),
            )
        except Exception as exc:
            logger.warning("Pending order check failed: %s", exc)

    return runtime_session_id, domestic_quote_market_div_code, market_today


async def _load_or_generate_daily_playbook(
    *,
    candidates_list: list[ScanCandidate],
    market: MarketInfo,
    market_today: date,
    playbook_store: PlaybookStore,
    pre_market_planner: PreMarketPlanner,
    telegram: TelegramClient,
) -> DayPlaybook:
    """Load the market playbook or generate it for the current trading day."""
    playbook = playbook_store.load(market_today, market.code)
    if playbook is not None:
        return playbook

    try:
        playbook = await pre_market_planner.generate_playbook(
            market=market.code,
            candidates=candidates_list,
            today=market_today,
        )
        playbook_store.save(playbook)
        try:
            await telegram.notify_playbook_generated(
                market=market.code,
                stock_count=playbook.stock_count,
                scenario_count=playbook.scenario_count,
                token_count=playbook.token_count,
                slot="open",
            )
        except Exception as exc:
            logger.warning("Playbook notification failed: %s", exc)
        logger.info(
            "Generated playbook for %s: %d stocks, %d scenarios",
            market.code,
            playbook.stock_count,
            playbook.scenario_count,
        )
        return playbook
    except Exception as exc:
        logger.error("Playbook generation failed for %s: %s", market.code, exc)
        try:
            await telegram.notify_playbook_failed(
                market=market.code,
                reason=str(exc)[:200],
            )
        except Exception as notify_exc:
            logger.warning("Playbook failed notification error: %s", notify_exc)
        return PreMarketPlanner._empty_playbook(market_today, market.code)


async def _collect_daily_session_market_data(
    *,
    broker: KISBroker,
    market: MarketInfo,
    overseas_broker: OverseasBroker,
    candidates_list: list[ScanCandidate],
    domestic_quote_market_div_code: str,
) -> list[dict[str, Any]]:
    """Collect per-stock market snapshots for one daily session market."""
    stocks_data: list[dict[str, Any]] = []
    candidate_map = {candidate.stock_code: candidate for candidate in candidates_list}

    for stock_code in [candidate.stock_code for candidate in candidates_list]:
        try:
            if market.is_domestic:
                current_price, price_change_pct, foreigner_net = await _retry_connection(
                    broker.get_current_price,
                    stock_code,
                    market_div_code=domestic_quote_market_div_code,
                    label=stock_code,
                )
            else:
                price_data = await _retry_connection(
                    overseas_broker.get_overseas_price,
                    market.exchange_code,
                    stock_code,
                    label=f"{stock_code}@{market.exchange_code}",
                )
                current_price = safe_float(price_data.get("output", {}).get("last", "0"))
                if current_price <= 0:
                    cand_lookup = candidate_map.get(stock_code)
                    if cand_lookup and cand_lookup.price > 0:
                        logger.debug(
                            "Price API returned 0 for %s; using scanner candidate price %.4f",
                            stock_code,
                            cand_lookup.price,
                        )
                        current_price = cand_lookup.price
                foreigner_net = 0.0
                price_change_pct = safe_float(price_data.get("output", {}).get("rate", "0"))
                if current_price <= 0:
                    cand_lookup = candidate_map.get(stock_code)
                    if cand_lookup and cand_lookup.price > 0:
                        current_price = cand_lookup.price
                        logger.debug(
                            "Price API returned 0 for %s; using scanner price %.4f",
                            stock_code,
                            current_price,
                        )

            stock_data: dict[str, Any] = {
                "stock_code": stock_code,
                "market_name": market.name,
                "current_price": current_price,
                "foreigner_net": foreigner_net,
                "price_change_pct": price_change_pct,
            }
            if not market.is_domestic:
                session_high_price = safe_float(
                    price_data.get("output", {}).get("high")
                    or price_data.get("output", {}).get("ovrs_hgpr")
                    or price_data.get("output", {}).get("stck_hgpr")
                )
                if session_high_price > 0:
                    stock_data["session_high_price"] = session_high_price
            cand = candidate_map.get(stock_code)
            if cand:
                stock_data["rsi"] = cand.rsi
                stock_data["volume_ratio"] = cand.volume_ratio
            stocks_data.append(stock_data)
        except Exception as exc:
            logger.error("Failed to fetch data for %s: %s", stock_code, exc)
            continue

    return stocks_data


async def _get_daily_session_balance_snapshot(
    *,
    market: MarketInfo,
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    settings: Settings,
    stocks_data: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], float, float, float]:
    """Load balance and buying-power data for one market."""
    if market.is_domestic:
        balance_data = await _retry_connection(broker.get_balance, label=f"balance:{market.code}")
        output2 = balance_data.get("output2", [{}])
        total_eval = safe_float(output2[0].get("tot_evlu_amt", "0")) if output2 else 0
        total_cash = safe_float(output2[0].get("dnca_tot_amt", "0")) if output2 else 0
        purchase_total = safe_float(output2[0].get("pchs_amt_smtl_amt", "0")) if output2 else 0
        return balance_data, {}, total_eval, total_cash, purchase_total

    balance_data = await _retry_connection(
        overseas_broker.get_overseas_balance,
        market.exchange_code,
        label=f"overseas_balance:{market.exchange_code}",
    )
    output2 = balance_data.get("output2", [{}])
    if isinstance(output2, list) and output2:
        balance_info = output2[0]
    elif isinstance(output2, dict):
        balance_info = output2
    else:
        balance_info = {}

    total_eval = safe_float(balance_info.get("frcr_evlu_tota", "0") or "0")
    purchase_total = safe_float(balance_info.get("frcr_buy_amt_smtl", "0") or "0")
    total_cash = 0.0
    ref_stock = next((stock for stock in stocks_data if stock.get("current_price", 0) > 0), None)
    if ref_stock:
        try:
            ps_data = await overseas_broker.get_overseas_buying_power(
                market.exchange_code,
                ref_stock["stock_code"],
                ref_stock["current_price"],
            )
            total_cash = safe_float(ps_data.get("output", {}).get("ovrs_ord_psbl_amt", "0") or "0")
        except ConnectionError as exc:
            logger.warning(
                "Could not fetch overseas buying power for %s: %s",
                market.exchange_code,
                exc,
            )

    if total_cash <= 0 and settings.MODE == "paper" and settings.PAPER_OVERSEAS_CASH > 0:
        total_cash = settings.PAPER_OVERSEAS_CASH

    return balance_data, balance_info, total_eval, total_cash, purchase_total


async def _process_daily_session_stock(
    *,
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    scenario_engine: ScenarioEngine,
    playbook: DayPlaybook,
    risk: RiskManager,
    db_conn: Any,
    decision_logger: DecisionLogger,
    telegram: TelegramClient,
    settings: Settings,
    market: MarketInfo,
    stock_data: dict[str, Any],
    candidate_map: dict[str, ScanCandidate],
    portfolio_data: dict[str, float],
    balance_data: dict[str, Any],
    balance_info: dict[str, Any],
    purchase_total: float,
    pnl_pct: float,
    total_eval: float,
    total_cash: float,
    runtime_session_id: str,
    daily_buy_cooldown: dict[str, float],
) -> None:
    """Evaluate, log, and optionally execute one daily-session stock decision."""
    stock_code = stock_data["stock_code"]
    stock_playbook = playbook.get_stock_playbook(stock_code)
    match = scenario_engine.evaluate(
        playbook,
        stock_code,
        stock_data,
        portfolio_data,
    )
    decision = TradeDecision(
        action=match.action.value,
        confidence=match.confidence,
        rationale=match.rationale,
    )

    logger.info(
        "Decision for %s (%s): %s (confidence=%d)",
        stock_code,
        market.name,
        decision.action,
        decision.confidence,
    )

    if decision.action == "BUY":
        daily_existing = _resolve_buy_suppression_position(
            db_conn=db_conn,
            balance_data=balance_data,
            stock_code=stock_code,
            market=market,
        )
        if daily_existing:
            decision = TradeDecision(
                action="HOLD",
                confidence=decision.confidence,
                rationale=(
                    f"Already holding {stock_code} "
                    f"(entry={daily_existing['price']:.4f}, "
                    f"qty={daily_existing['quantity']})"
                ),
            )
            logger.info(
                "BUY suppressed for %s (%s): already holding open position",
                stock_code,
                market.name,
            )
        elif market.code.startswith("US"):
            min_price = float(
                _resolve_market_setting(
                    market=market,
                    settings=settings,
                    key="US_MIN_PRICE",
                    default=5.0,
                )
            )
            if stock_data["current_price"] <= min_price:
                decision = TradeDecision(
                    action="HOLD",
                    confidence=decision.confidence,
                    rationale=(
                        f"US minimum price filter blocked BUY "
                        f"(price={stock_data['current_price']:.4f} <= {min_price:.4f})"
                    ),
                )
                logger.info(
                    "BUY suppressed for %s (%s): US min price filter %.4f <= %.4f",
                    stock_code,
                    market.name,
                    stock_data["current_price"],
                    min_price,
                )
        if decision.action == "BUY":
            cooldown_key = _stoploss_cooldown_key(market=market, stock_code=stock_code)
            now_epoch = datetime.now(UTC).timestamp()
            cooldown_until = _STOPLOSS_REENTRY_COOLDOWN_UNTIL.get(cooldown_key, 0.0)
            if now_epoch < cooldown_until:
                remaining = int(cooldown_until - now_epoch)
                decision = TradeDecision(
                    action="HOLD",
                    confidence=decision.confidence,
                    rationale=f"Stop-loss reentry cooldown active ({remaining}s remaining)",
                )
                logger.info(
                    "BUY suppressed for %s (%s): stop-loss cooldown active (%ds remaining)",
                    stock_code,
                    market.name,
                    remaining,
                )

    if decision.action == "HOLD":
        daily_open = get_open_position(db_conn, stock_code, market.code)
        if not daily_open:
            _clear_runtime_exit_cache_for_symbol(
                market_code=market.code,
                stock_code=stock_code,
            )
        await _inject_staged_exit_features(
            market=market,
            stock_code=stock_code,
            open_position=daily_open,
            market_data=stock_data,
            broker=broker,
            overseas_broker=overseas_broker,
        )
        decision = _apply_staged_exit_override_for_hold(
            decision=decision,
            market=market,
            stock_code=stock_code,
            open_position=daily_open,
            market_data=stock_data,
            stock_playbook=stock_playbook,
            settings=settings,
        )
        if (
            daily_open
            and decision.action == "HOLD"
            and _should_force_exit_for_overnight(
                market=market,
                settings=settings,
            )
        ):
            decision = TradeDecision(
                action="SELL",
                confidence=max(decision.confidence, 85),
                rationale=(
                    "Forced exit by overnight policy"
                    " (session close window / kill switch priority)"
                ),
            )
            logger.info(
                "Daily overnight policy override for %s (%s): HOLD -> SELL",
                stock_code,
                market.name,
            )

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
        "scenario_match": match.match_details,
    }
    input_data = {
        "current_price": stock_data["current_price"],
        "foreigner_net": stock_data["foreigner_net"],
        "total_eval": total_eval,
        "total_cash": total_cash,
        "pnl_pct": pnl_pct,
    }
    _merge_staged_exit_evidence_into_log(
        market_data=stock_data,
        context_snapshot=context_snapshot,
        input_data=input_data,
    )

    decision_id = decision_logger.log_decision(
        stock_code=stock_code,
        market=market.code,
        exchange_code=market.exchange_code,
        session_id=runtime_session_id,
        action=decision.action,
        confidence=decision.confidence,
        rationale=decision.rationale,
        context_snapshot=context_snapshot,
        input_data=input_data,
    )

    quantity = 0
    trade_price = stock_data["current_price"]
    trade_pnl = 0.0
    buy_trade: dict[str, Any] | None = None
    buy_price = 0.0
    sell_qty = 0
    order_succeeded = True
    if decision.action in ("BUY", "SELL"):
        if KILL_SWITCH.new_orders_blocked and decision.action == "BUY":
            logger.critical(
                "KillSwitch block active: skip %s order for %s (%s)",
                decision.action,
                stock_code,
                market.name,
            )
            return

        daily_broker_held_qty = (
            _extract_held_qty_from_balance(
                balance_data, stock_code, is_domestic=market.is_domestic
            )
            if decision.action == "SELL"
            else 0
        )
        quantity = _determine_order_quantity(
            action=decision.action,
            current_price=stock_data["current_price"],
            total_cash=total_cash,
            candidate=candidate_map.get(stock_code),
            settings=settings,
            broker_held_qty=daily_broker_held_qty,
        )
        if quantity <= 0:
            logger.info(
                "Skip %s %s (%s): no affordable quantity (cash=%.2f, price=%.2f)",
                decision.action,
                stock_code,
                market.name,
                total_cash,
                stock_data["current_price"],
            )
            return
        order_amount = stock_data["current_price"] * quantity
        fx_blocked, remaining_cash, required_buffer = _should_block_overseas_buy_for_fx_buffer(
            market=market,
            action=decision.action,
            total_cash=total_cash,
            order_amount=order_amount,
            settings=settings,
        )
        if fx_blocked:
            logger.warning(
                (
                    "Skip BUY %s (%s): FX buffer guard "
                    "(remaining=%.2f, required=%.2f, cash=%.2f, order=%.2f)"
                ),
                stock_code,
                market.name,
                remaining_cash,
                required_buffer,
                total_cash,
                order_amount,
            )
            return

        if decision.action == "BUY":
            daily_cooldown_key = f"{market.code}:{stock_code}"
            daily_cooldown_until = daily_buy_cooldown.get(daily_cooldown_key, 0.0)
            now = asyncio.get_event_loop().time()
            if now < daily_cooldown_until:
                remaining = int(daily_cooldown_until - now)
                logger.info(
                    (
                        "Skip BUY %s (%s): insufficient-balance cooldown active "
                        "(%ds remaining)"
                    ),
                    stock_code,
                    market.name,
                    remaining,
                )
                return

        try:
            if decision.action == "SELL":
                risk.check_circuit_breaker(pnl_pct)
            else:
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
            return
        except CircuitBreakerTripped as exc:
            ks_report = await _trigger_emergency_kill_switch(
                reason=f"daily_circuit_breaker:{market.code}:{stock_code}:{exc.pnl_pct:.2f}",
                broker=broker,
                overseas_broker=overseas_broker,
                telegram=telegram,
                settings=settings,
                current_market=market,
                stock_code=stock_code,
                pnl_pct=exc.pnl_pct,
                threshold=exc.threshold,
            )
            logger.critical("Circuit breaker tripped — stopping session")
            if ks_report.errors:
                logger.critical(
                    "Daily KillSwitch step errors for %s/%s: %s",
                    market.code,
                    stock_code,
                    "; ".join(ks_report.errors),
                )
            raise

        try:
            if market.is_domestic:
                if decision.action == "BUY":
                    order_price = kr_round_down(stock_data["current_price"] * 1.002)
                else:
                    order_price = kr_round_down(stock_data["current_price"] * 0.998)
                try:
                    validate_order_policy(
                        market=market,
                        order_type=decision.action,
                        price=float(order_price),
                    )
                except OrderPolicyRejected as exc:
                    logger.warning(
                        "Order policy rejected %s %s (%s): %s [session=%s]",
                        decision.action,
                        stock_code,
                        market.name,
                        exc,
                        exc.session_id,
                    )
                    return
                if _maybe_queue_order_intent(
                    market=market,
                    session_id=runtime_session_id,
                    stock_code=stock_code,
                    order_type=decision.action,
                    quantity=quantity,
                    price=float(order_price),
                    source="run_daily_session",
                ):
                    return
                result = await broker.send_order(
                    stock_code=stock_code,
                    order_type=decision.action,
                    quantity=quantity,
                    price=order_price,
                    session_id=runtime_session_id,
                )
                if result.get("rt_cd", "0") != "0":
                    order_succeeded = False
                    daily_msg1 = result.get("msg1") or ""
                    logger.warning(
                        "KR order not accepted for %s: rt_cd=%s msg=%s",
                        stock_code,
                        result.get("rt_cd"),
                        daily_msg1,
                    )
            else:
                if decision.action == "BUY":
                    order_price = round(stock_data["current_price"] * 1.005, 4)
                else:
                    order_price = stock_data["current_price"]
                try:
                    validate_order_policy(
                        market=market,
                        order_type=decision.action,
                        price=float(order_price),
                    )
                except OrderPolicyRejected as exc:
                    logger.warning(
                        "Order policy rejected %s %s (%s): %s [session=%s]",
                        decision.action,
                        stock_code,
                        market.name,
                        exc,
                        exc.session_id,
                    )
                    return
                if _maybe_queue_order_intent(
                    market=market,
                    session_id=runtime_session_id,
                    stock_code=stock_code,
                    order_type=decision.action,
                    quantity=quantity,
                    price=float(order_price),
                    source="run_daily_session",
                ):
                    return
                result = await overseas_broker.send_overseas_order(
                    exchange_code=market.exchange_code,
                    stock_code=stock_code,
                    order_type=decision.action,
                    quantity=quantity,
                    price=order_price,
                )
                if result.get("rt_cd", "") != "0":
                    order_succeeded = False
                    daily_msg1 = result.get("msg1") or ""
                    logger.warning(
                        "Overseas order not accepted for %s: rt_cd=%s msg=%s",
                        stock_code,
                        result.get("rt_cd"),
                        daily_msg1,
                    )
                    if decision.action == "BUY" and "주문가능금액" in daily_msg1:
                        daily_cooldown_key = f"{market.code}:{stock_code}"
                        daily_buy_cooldown[daily_cooldown_key] = (
                            asyncio.get_event_loop().time() + _BUY_COOLDOWN_SECONDS
                        )
                        logger.info(
                            "BUY cooldown set for %s: %.0fs (insufficient balance)",
                            stock_code,
                            _BUY_COOLDOWN_SECONDS,
                        )
            logger.info("Order result: %s", result.get("msg1", "OK"))

            if order_succeeded:
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
            return

        if decision.action == "SELL" and order_succeeded:
            buy_trade = get_latest_buy_trade(
                db_conn,
                stock_code,
                market.code,
                exchange_code=market.exchange_code,
            )
            if buy_trade and buy_trade.get("price") is not None:
                buy_price = float(buy_trade["price"])
                buy_qty = int(buy_trade.get("quantity") or 0)
                sell_qty = _resolve_sell_qty_for_pnl(
                    sell_qty=quantity,
                    buy_qty=buy_qty,
                )
                trade_pnl = (trade_price - buy_price) * sell_qty
                decision_logger.update_outcome(
                    decision_id=buy_trade["decision_id"],
                    pnl=trade_pnl,
                    accuracy=1 if trade_pnl > 0 else 0,
                )
                if trade_pnl < 0:
                    cooldown_key = _stoploss_cooldown_key(
                        market=market, stock_code=stock_code
                    )
                    cooldown_minutes = _stoploss_cooldown_minutes(
                        settings,
                        market=market,
                    )
                    _STOPLOSS_REENTRY_COOLDOWN_UNTIL[cooldown_key] = (
                        datetime.now(UTC).timestamp() + cooldown_minutes * 60
                    )
                    logger.info(
                        "Stop-loss cooldown set for %s (%s): %d minutes",
                        stock_code,
                        market.name,
                        cooldown_minutes,
                    )

    if decision.action in ("BUY", "SELL") and not order_succeeded:
        return

    strategy_pnl: float | None = None
    fx_pnl: float | None = None
    selection_context: dict[str, Any] | None = None
    if decision.action == "SELL" and order_succeeded:
        buy_fx_rate = _extract_buy_fx_rate(buy_trade)
        sell_fx_rate = _extract_fx_rate_from_sources(balance_info, stock_data)
        strategy_pnl, fx_pnl = _split_trade_pnl_components(
            market=market,
            trade_pnl=trade_pnl,
            buy_price=buy_price,
            sell_price=trade_price,
            quantity=sell_qty or quantity,
            buy_fx_rate=buy_fx_rate,
            sell_fx_rate=sell_fx_rate,
        )
        if sell_fx_rate is not None and not market.is_domestic:
            selection_context = {"fx_rate": sell_fx_rate}
    elif not market.is_domestic:
        snapshot_fx_rate = _extract_fx_rate_from_sources(balance_info, stock_data)
        if snapshot_fx_rate is not None:
            selection_context = {"fx_rate": snapshot_fx_rate}

    log_trade(
        conn=db_conn,
        stock_code=stock_code,
        action=decision.action,
        confidence=decision.confidence,
        rationale=decision.rationale,
        quantity=quantity,
        price=trade_price,
        pnl=trade_pnl,
        strategy_pnl=strategy_pnl,
        fx_pnl=fx_pnl,
        market=market.code,
        exchange_code=market.exchange_code,
        session_id=runtime_session_id,
        selection_context=selection_context,
        decision_id=decision_id,
        mode=settings.MODE,
    )


async def _run_daily_session_market(
    *,
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    scenario_engine: ScenarioEngine,
    playbook_store: PlaybookStore,
    pre_market_planner: PreMarketPlanner,
    risk: RiskManager,
    db_conn: Any,
    decision_logger: DecisionLogger,
    telegram: TelegramClient,
    settings: Settings,
    market: MarketInfo,
    smart_scanner: SmartVolatilityScanner | None,
    daily_start_eval: float,
    daily_buy_cooldown: dict[str, float],
    sell_resubmit_counts: dict[str, int],
) -> float:
    """Execute one open market within the daily session path."""
    runtime_session_id, domestic_quote_market_div_code, market_today = (
        await _prepare_daily_session_market(
            broker=broker,
            db_conn=db_conn,
            market=market,
            overseas_broker=overseas_broker,
            settings=settings,
            telegram=telegram,
            sell_resubmit_counts=sell_resubmit_counts,
            daily_buy_cooldown=daily_buy_cooldown,
        )
    )

    candidates_list = await _load_daily_session_market_candidates(
        db_conn=db_conn,
        market=market,
        overseas_broker=overseas_broker,
        smart_scanner=smart_scanner,
    )
    if not candidates_list:
        logger.info("No scanner candidates for market %s — skipping", market.code)
        return daily_start_eval

    watchlist = [candidate.stock_code for candidate in candidates_list]
    candidate_map = {candidate.stock_code: candidate for candidate in candidates_list}
    logger.info("Processing market: %s (%d stocks)", market.name, len(watchlist))

    playbook = await _load_or_generate_daily_playbook(
        candidates_list=candidates_list,
        market=market,
        market_today=market_today,
        playbook_store=playbook_store,
        pre_market_planner=pre_market_planner,
        telegram=telegram,
    )

    stocks_data = await _collect_daily_session_market_data(
        broker=broker,
        market=market,
        overseas_broker=overseas_broker,
        candidates_list=candidates_list,
        domestic_quote_market_div_code=domestic_quote_market_div_code,
    )
    if not stocks_data:
        logger.warning("No valid stock data for market %s", market.code)
        return daily_start_eval

    try:
        balance_data, balance_info, total_eval, total_cash, purchase_total = (
            await _get_daily_session_balance_snapshot(
                market=market,
                broker=broker,
                overseas_broker=overseas_broker,
                settings=settings,
                stocks_data=stocks_data,
            )
        )
    except ConnectionError as exc:
        logger.error(
            "Balance fetch failed for market %s after all retries — skipping market: %s",
            market.code,
            exc,
        )
        return daily_start_eval

    if daily_start_eval <= 0 and total_eval > 0:
        daily_start_eval = total_eval
        logger.info(
            "Daily CB baseline set: total_eval=%.2f (first balance of the day)",
            daily_start_eval,
        )

    if daily_start_eval > 0:
        pnl_pct = (total_eval - daily_start_eval) / daily_start_eval * 100
    else:
        pnl_pct = (
            ((total_eval - purchase_total) / purchase_total * 100) if purchase_total > 0 else 0.0
        )
    portfolio_data = {
        "portfolio_pnl_pct": pnl_pct,
        "total_cash": total_cash,
        "total_eval": total_eval,
    }

    logger.info(
        "Evaluating %d stocks against playbook for %s",
        len(stocks_data),
        market.name,
    )
    for stock_data in stocks_data:
        await _process_daily_session_stock(
            broker=broker,
            overseas_broker=overseas_broker,
            scenario_engine=scenario_engine,
            playbook=playbook,
            risk=risk,
            db_conn=db_conn,
            decision_logger=decision_logger,
            telegram=telegram,
            settings=settings,
            market=market,
            stock_data=stock_data,
            candidate_map=candidate_map,
            portfolio_data=portfolio_data,
            balance_data=balance_data,
            balance_info=balance_info,
            purchase_total=purchase_total,
            pnl_pct=pnl_pct,
            total_eval=total_eval,
            total_cash=total_cash,
            runtime_session_id=runtime_session_id,
            daily_buy_cooldown=daily_buy_cooldown,
        )

    return daily_start_eval


async def run_daily_session(
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    scenario_engine: ScenarioEngine,
    playbook_store: PlaybookStore,
    pre_market_planner: PreMarketPlanner,
    risk: RiskManager,
    db_conn: Any,
    decision_logger: DecisionLogger,
    context_store: ContextStore,
    criticality_assessor: CriticalityAssessor,
    telegram: TelegramClient,
    settings: Settings,
    smart_scanner: SmartVolatilityScanner | None = None,
    daily_start_eval: float = 0.0,
) -> float:
    """Execute one daily trading session.

    V2 proactive strategy: 1 Gemini call for playbook generation,
    then local scenario evaluation per stock (0 API calls).

    Args:
        daily_start_eval: Portfolio evaluation at the start of the trading day.
            Used to compute intra-day P&L for the Circuit Breaker.
            Pass 0.0 on the first session of each day; the function will set
            it from the first balance query and return it for subsequent
            sessions.

    Returns:
        The daily_start_eval value that should be forwarded to the next
        session of the same trading day.
    """
    open_markets = get_open_markets(settings.enabled_market_list)
    if not open_markets:
        logger.info("No markets open for this session")
        return daily_start_eval

    logger.info("Starting daily trading session for %d markets", len(open_markets))
    daily_buy_cooldown: dict[str, float] = {}
    sell_resubmit_counts: dict[str, int] = {}

    for market in open_markets:
        daily_start_eval = await _run_daily_session_market(
            broker=broker,
            overseas_broker=overseas_broker,
            scenario_engine=scenario_engine,
            playbook_store=playbook_store,
            pre_market_planner=pre_market_planner,
            risk=risk,
            db_conn=db_conn,
            decision_logger=decision_logger,
            telegram=telegram,
            settings=settings,
            market=market,
            smart_scanner=smart_scanner,
            daily_start_eval=daily_start_eval,
            daily_buy_cooldown=daily_buy_cooldown,
            sell_resubmit_counts=sell_resubmit_counts,
        )

    logger.info("Daily trading session completed")
    return daily_start_eval


async def _handle_market_close(
    market_code: str,
    market_name: str,
    market_timezone: Any,
    telegram: TelegramClient,
    context_aggregator: ContextAggregator,
    daily_reviewer: DailyReviewer,
    evolution_optimizer: EvolutionOptimizer | None = None,
) -> None:
    """Handle market-close tasks: notify, aggregate, review, and store context."""
    await telegram.notify_market_close(market_name, 0.0)

    market_date = datetime.now(market_timezone).date().isoformat()
    context_aggregator.aggregate_daily_from_trades(
        date=market_date,
        market=market_code,
    )

    scorecard = daily_reviewer.generate_scorecard(market_date, market_code)
    daily_reviewer.store_scorecard_in_context(scorecard)

    lessons = await daily_reviewer.generate_lessons(scorecard)
    if lessons:
        scorecard.lessons = lessons
        daily_reviewer.store_scorecard_in_context(scorecard)

    await telegram.send_message(
        f"<b>Daily Review ({market_code})</b>\n"
        f"Date: {scorecard.date}\n"
        f"Decisions: {scorecard.total_decisions}\n"
        f"P&L: {scorecard.total_pnl:+.2f}\n"
        f"Win Rate: {scorecard.win_rate:.2f}%\n"
        f"Lessons: {', '.join(scorecard.lessons) if scorecard.lessons else 'N/A'}"
    )

    if evolution_optimizer is not None:
        await _run_evolution_loop(
            evolution_optimizer=evolution_optimizer,
            telegram=telegram,
            market_code=market_code,
            market_date=market_date,
        )


def _run_context_scheduler(
    scheduler: ContextScheduler,
    now: datetime | None = None,
) -> None:
    """Run periodic context scheduler tasks and log when anything executes."""
    result = scheduler.run_if_due(now=now)
    if any(
        [
            result.weekly,
            result.monthly,
            result.quarterly,
            result.annual,
            result.legacy,
            result.cleanup,
        ]
    ):
        logger.info(
            (
                "Context scheduler ran (weekly=%s, monthly=%s, quarterly=%s, "
                "annual=%s, legacy=%s, cleanup=%s)"
            ),
            result.weekly,
            result.monthly,
            result.quarterly,
            result.annual,
            result.legacy,
            result.cleanup,
        )


def _has_market_session_transition(
    market_states: dict[str, str], market_code: str, session_id: str
) -> bool:
    """Return True when market session changed (or market has no prior state)."""
    return market_states.get(market_code) != session_id


def _should_rescan_market(
    *, last_scan: float, now_timestamp: float, rescan_interval: float, session_changed: bool
) -> bool:
    """Force rescan on session transition; otherwise follow interval cadence."""
    return session_changed or (now_timestamp - last_scan >= rescan_interval)


_MID_SESSION_REFRESH_SESSIONS: dict[str, str] = {
    "US_NASDAQ": "US_REG",
    "US_NYSE": "US_REG",
    "US_AMEX": "US_REG",
    "KR": "KRX_REG",
}

_MID_SESSION_REFRESH_TZ: dict[str, ZoneInfo] = {
    "US_NASDAQ": ZoneInfo("America/New_York"),
    "US_NYSE": ZoneInfo("America/New_York"),
    "US_AMEX": ZoneInfo("America/New_York"),
    "KR": ZoneInfo("Asia/Seoul"),
}


def _should_mid_session_refresh(
    *,
    market_code: str,
    session_id: str,
    now: datetime,
    mid_refreshed: set[str],
) -> bool:
    """Return True when a mid-session playbook refresh should fire.

    Triggers once per day at or after 12:00 (local market time) during the regular session.
    Considers all stored slots; 'mid' takes priority over all others.
    """
    expected_session = _MID_SESSION_REFRESH_SESSIONS.get(market_code)
    if expected_session is None or session_id != expected_session:
        return False
    if market_code in mid_refreshed:
        return False
    market_tz = _MID_SESSION_REFRESH_TZ.get(market_code, UTC)
    local_now = now.astimezone(market_tz)
    return local_now.hour >= 12


def _should_reuse_stored_playbook(*, market_code: str, session_id: str) -> bool:
    """Return whether DB-stored playbook can be reused for realtime loop bootstrap.

    For KR regular session (`KRX_REG`), always generate a fresh playbook instead of
    reusing an earlier session's stored playbook (issue #419). For US regular
    session (`US_DAY`), also generate a fresh playbook on session transition so
    pre-market assumptions do not leak into regular session.
    """
    return not (
        (market_code == "KR" and session_id == "KRX_REG")
        or (market_code.startswith("US") and session_id == "US_DAY")
    )


def _should_refresh_cached_playbook_on_session_transition(
    *, session_changed: bool, market_code: str, session_id: str
) -> bool:
    """Return True when session transition requires dropping cached playbook."""
    return session_changed and not _should_reuse_stored_playbook(
        market_code=market_code,
        session_id=session_id,
    )


def _refresh_cached_playbook_on_session_transition(
    *,
    playbooks: dict[str, DayPlaybook],
    session_changed: bool,
    market_code: str,
    session_id: str,
) -> bool:
    """Drop cached playbook when a session transition requires fresh generation.

    Returns True when an existing cache entry was removed.
    """
    if not _should_refresh_cached_playbook_on_session_transition(
        session_changed=session_changed,
        market_code=market_code,
        session_id=session_id,
    ):
        return False
    return playbooks.pop(market_code, None) is not None


async def _run_markets_in_parallel(
    markets: list[Any], processor: Callable[[Any], Awaitable[None]]
) -> None:
    """Run market processors in parallel and fail fast on the first exception."""
    if not markets:
        return

    tasks = [asyncio.create_task(processor(market)) for market in markets]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    first_exc: BaseException | None = None
    for task in done:
        exc = task.exception()
        if exc is not None and first_exc is None:
            first_exc = exc

    if first_exc is not None:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        raise first_exc

    if pending:
        await asyncio.gather(*pending)


async def _run_evolution_loop(
    evolution_optimizer: EvolutionOptimizer,
    telegram: TelegramClient,
    market_code: str,
    market_date: str,
) -> None:
    """Run evolution loop once at US close (end of trading day)."""
    if not market_code.startswith("US"):
        return

    try:
        pr_info = await evolution_optimizer.evolve()
    except Exception as exc:
        logger.warning("Evolution loop failed on %s: %s", market_date, exc)
        return

    if pr_info is None:
        logger.info("Evolution loop skipped on %s (no actionable failures)", market_date)
        return

    try:
        await telegram.send_message(
            "<b>Evolution Update</b>\n"
            f"Date: {market_date}\n"
            f"PR: {pr_info.get('title', 'N/A')}\n"
            f"Branch: {pr_info.get('branch', 'N/A')}\n"
            f"Status: {pr_info.get('status', 'N/A')}"
        )
    except Exception as exc:
        logger.warning("Evolution notification failed on %s: %s", market_date, exc)


def _start_dashboard_server(settings: Settings) -> threading.Thread | None:
    """Start FastAPI dashboard in a daemon thread when enabled."""
    if not settings.DASHBOARD_ENABLED:
        return None

    # Validate dependencies before spawning the thread so startup failures are
    # reported synchronously (avoids the misleading "started" → "failed" log pair).
    try:
        import uvicorn  # noqa: F401

        from src.dashboard import create_dashboard_app  # noqa: F401
    except ImportError as exc:
        logger.warning("Dashboard server unavailable (missing dependency): %s", exc)
        return None

    def _serve() -> None:
        try:
            import uvicorn

            from src.dashboard import create_dashboard_app

            app = create_dashboard_app(settings.DB_PATH, mode=settings.MODE)
            uvicorn.run(
                app,
                host=settings.DASHBOARD_HOST,
                port=settings.DASHBOARD_PORT,
                log_level="info",
            )
        except Exception as exc:
            logger.warning("Dashboard server stopped unexpectedly: %s", exc)

    thread = threading.Thread(
        target=_serve,
        name="dashboard-server",
        daemon=True,
    )
    thread.start()
    logger.info(
        "Dashboard server started at http://%s:%d",
        settings.DASHBOARD_HOST,
        settings.DASHBOARD_PORT,
    )
    return thread


def _apply_dashboard_flag(settings: Settings, dashboard_flag: bool) -> Settings:
    """Apply CLI dashboard flag over environment settings."""
    if dashboard_flag and not settings.DASHBOARD_ENABLED:
        return settings.model_copy(update={"DASHBOARD_ENABLED": True})
    return settings


async def run(settings: Settings) -> None:
    """Main async loop — iterate over open markets on a timer."""
    _ensure_runtime_mode_allowed(settings.MODE)
    runtime_lock = _acquire_live_runtime_lock(settings)
    current_task = asyncio.current_task()
    if current_task is not None:
        current_task.add_done_callback(lambda _: _release_live_runtime_lock(runtime_lock))
    import src.core.blackout_runtime as _br_mod
    _br_mod.BLACKOUT_ORDER_MANAGER = BlackoutOrderManager(
        enabled=settings.ORDER_BLACKOUT_ENABLED,
        windows=parse_blackout_windows_kst(settings.ORDER_BLACKOUT_WINDOWS_KST),
        max_queue_size=settings.ORDER_BLACKOUT_QUEUE_MAX,
    )
    logger.info(
        "Blackout manager initialized: enabled=%s windows=%s queue_max=%d",
        settings.ORDER_BLACKOUT_ENABLED,
        settings.ORDER_BLACKOUT_WINDOWS_KST,
        settings.ORDER_BLACKOUT_QUEUE_MAX,
    )

    broker = KISBroker(settings)
    overseas_broker = OverseasBroker(broker)
    brain = GeminiClient(settings)
    risk = RiskManager(settings)
    db_conn = init_db(settings.DB_PATH)
    decision_logger = DecisionLogger(db_conn)
    context_store = ContextStore(db_conn)
    context_aggregator = ContextAggregator(db_conn)
    context_scheduler = ContextScheduler(
        aggregator=context_aggregator,
        store=context_store,
    )
    evolution_optimizer = EvolutionOptimizer(settings)

    # V2 proactive strategy components
    context_selector = ContextSelector(context_store)
    scenario_engine = ScenarioEngine()
    playbook_store = PlaybookStore(db_conn)
    daily_reviewer = DailyReviewer(db_conn, context_store, gemini_client=brain)
    pre_market_planner = PreMarketPlanner(
        gemini_client=brain,
        context_store=context_store,
        context_selector=context_selector,
        settings=settings,
    )

    # Track playbooks per market (in-memory cache)
    playbooks: dict[str, DayPlaybook] = {}
    mid_refreshed: set[str] = set()  # 당일 mid-session refresh가 완료된 마켓
    _pre_refresh_playbooks: dict[str, DayPlaybook | None] = {}  # rollback용 백업 (issue #436)

    # Initialize Telegram notifications
    telegram = TelegramClient(
        bot_token=settings.TELEGRAM_BOT_TOKEN,
        chat_id=settings.TELEGRAM_CHAT_ID,
        enabled=settings.TELEGRAM_ENABLED,
        notification_filter=NotificationFilter(
            trades=settings.TELEGRAM_NOTIFY_TRADES,
            market_open_close=settings.TELEGRAM_NOTIFY_MARKET_OPEN_CLOSE,
            fat_finger=settings.TELEGRAM_NOTIFY_FAT_FINGER,
            system_events=settings.TELEGRAM_NOTIFY_SYSTEM_EVENTS,
            playbook=settings.TELEGRAM_NOTIFY_PLAYBOOK,
            scenario_match=settings.TELEGRAM_NOTIFY_SCENARIO_MATCH,
            errors=settings.TELEGRAM_NOTIFY_ERRORS,
        ),
    )
    realtime_hard_stop_monitor = RealtimeHardStopMonitor()
    realtime_hard_stop_client: KISWebSocketClient | None = None
    realtime_hard_stop_task: asyncio.Task[None] | None = None

    if settings.TRADE_MODE == "realtime" and settings.REALTIME_HARD_STOP_ENABLED:
        async def _on_realtime_price(event: KISWebSocketPriceEvent) -> None:
            await _handle_realtime_price_event(
                event=event,
                broker=broker,
                overseas_broker=overseas_broker,
                db_conn=db_conn,
                decision_logger=decision_logger,
                telegram=telegram,
                settings=settings,
                monitor=realtime_hard_stop_monitor,
                websocket_client=realtime_hard_stop_client,
            )

        realtime_hard_stop_client = KISWebSocketClient(
            broker=broker,
            ws_url=f"{settings.kis_ws_url.rstrip('/')}{settings.KIS_WS_PATH}",
            on_price=_on_realtime_price,
            retry_delay_seconds=settings.REALTIME_HARD_STOP_RETRY_DELAY_SECONDS,
            max_retries=settings.REALTIME_HARD_STOP_MAX_RETRIES,
        )
        realtime_hard_stop_task = asyncio.create_task(realtime_hard_stop_client.run())
        logger.info("Realtime KR hard-stop websocket monitor started")

    # Initialize Telegram command handler
    command_handler = TelegramCommandHandler(telegram)

    # Register basic commands
    async def handle_help() -> None:
        """Handle /help command."""
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
            "/resume - Resume trading\n"
            "/notify - Show notification filter status\n"
            "/notify [key] [on|off] - Toggle notification type\n"
            "  Keys: trades, market, scenario, playbook,\n"
            "        system, fatfinger, errors, all"
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
            "<b>▶️ Trading Resumed</b>\n\nTrading operations have been restarted."
        )

    async def handle_status() -> None:
        """Handle /status command - show trading status."""
        try:
            # Get trading status
            trading_status = "Active" if pause_trading.is_set() else "Paused"

            # Calculate P&L from balance data
            try:
                balance = await broker.get_balance()
                output2 = balance.get("output2", [{}])
                if output2:
                    total_eval = safe_float(output2[0].get("tot_evlu_amt", "0"))
                    purchase_total = safe_float(output2[0].get("pchs_amt_smtl_amt", "0"))
                    current_pnl = (
                        ((total_eval - purchase_total) / purchase_total * 100)
                        if purchase_total > 0
                        else 0.0
                    )
                    pnl_str = f"{current_pnl:+.2f}%"
                else:
                    pnl_str = "N/A"
            except Exception as exc:
                logger.warning("Failed to get P&L: %s", exc)
                pnl_str = "N/A"

            # Format market list
            markets_str = ", ".join(settings.enabled_market_list)

            message = (
                "<b>📊 Trading Status</b>\n\n"
                f"<b>Mode:</b> {settings.MODE.upper()}\n"
                f"<b>Markets:</b> {markets_str}\n"
                f"<b>Trading:</b> {trading_status}\n\n"
                f"<b>Current P&L:</b> {pnl_str}\n"
                f"<b>Circuit Breaker:</b> {risk._cb_threshold:.1f}%"
            )
            await telegram.send_message(message)

        except Exception as exc:
            logger.error("Error in /status handler: %s", exc)
            await telegram.send_message("<b>⚠️ Error</b>\n\nFailed to retrieve trading status.")

    async def handle_positions() -> None:
        """Handle /positions command - show account summary."""
        try:
            # Get account balance
            balance = await broker.get_balance()
            output2 = balance.get("output2", [{}])

            if not output2:
                await telegram.send_message(
                    "<b>💼 Account Summary</b>\n\nNo balance information available."
                )
                return

            # Extract account-level data
            total_eval = safe_float(output2[0].get("tot_evlu_amt", "0"))
            total_cash = safe_float(output2[0].get("dnca_tot_amt", "0"))
            purchase_total = safe_float(output2[0].get("pchs_amt_smtl_amt", "0"))

            # Calculate P&L
            pnl_pct = (
                ((total_eval - purchase_total) / purchase_total * 100)
                if purchase_total > 0
                else 0.0
            )
            pnl_sign = "+" if pnl_pct >= 0 else ""

            message = (
                "<b>💼 Account Summary</b>\n\n"
                f"<b>Total Evaluation:</b> ₩{total_eval:,.0f}\n"
                f"<b>Available Cash:</b> ₩{total_cash:,.0f}\n"
                f"<b>Purchase Total:</b> ₩{purchase_total:,.0f}\n"
                f"<b>P&L:</b> {pnl_sign}{pnl_pct:.2f}%\n\n"
                "<i>Note: Individual position details require API enhancement</i>"
            )
            await telegram.send_message(message)

        except Exception as exc:
            logger.error("Error in /positions handler: %s", exc)
            await telegram.send_message("<b>⚠️ Error</b>\n\nFailed to retrieve positions.")

    async def handle_report() -> None:
        """Handle /report command - show daily summary metrics."""
        try:
            today = datetime.now(UTC).date().isoformat()
            trade_row = db_conn.execute(
                """
                SELECT COUNT(*) AS trade_count,
                       COALESCE(SUM(pnl), 0.0) AS total_pnl,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins
                FROM trades
                WHERE DATE(timestamp) = ?
                """,
                (today,),
            ).fetchone()
            decision_row = db_conn.execute(
                """
                SELECT COUNT(*) AS decision_count,
                       COALESCE(AVG(confidence), 0.0) AS avg_confidence
                FROM decision_logs
                WHERE DATE(timestamp) = ?
                """,
                (today,),
            ).fetchone()

            trade_count = int(trade_row[0] if trade_row else 0)
            total_pnl = float(trade_row[1] if trade_row else 0.0)
            wins = int(trade_row[2] if trade_row and trade_row[2] is not None else 0)
            decision_count = int(decision_row[0] if decision_row else 0)
            avg_confidence = float(decision_row[1] if decision_row else 0.0)
            win_rate = (wins / trade_count * 100.0) if trade_count > 0 else 0.0

            await telegram.send_message(
                "<b>📈 Daily Report</b>\n\n"
                f"<b>Date:</b> {today}\n"
                f"<b>Trades:</b> {trade_count}\n"
                f"<b>Total P&L:</b> {total_pnl:+.2f}\n"
                f"<b>Win Rate:</b> {win_rate:.2f}%\n"
                f"<b>Decisions:</b> {decision_count}\n"
                f"<b>Avg Confidence:</b> {avg_confidence:.2f}"
            )
        except Exception as exc:
            logger.error("Error in /report handler: %s", exc)
            await telegram.send_message("<b>⚠️ Error</b>\n\nFailed to generate daily report.")

    async def handle_scenarios() -> None:
        """Handle /scenarios command - show today's playbook scenarios."""
        try:
            today = datetime.now(UTC).date().isoformat()
            rows = db_conn.execute(
                """
                SELECT market, playbook_json
                FROM playbooks
                WHERE date = ?
                ORDER BY market
                """,
                (today,),
            ).fetchall()

            if not rows:
                await telegram.send_message(
                    "<b>🧠 Today's Scenarios</b>\n\nNo playbooks found for today."
                )
                return

            lines = ["<b>🧠 Today's Scenarios</b>", ""]
            for market, playbook_json in rows:
                lines.append(f"<b>{market}</b>")
                playbook_data = {}
                try:
                    playbook_data = json.loads(playbook_json)
                except Exception:
                    playbook_data = {}

                stock_playbooks = playbook_data.get("stock_playbooks", [])
                if not stock_playbooks:
                    lines.append("- No scenarios")
                    lines.append("")
                    continue

                for stock_pb in stock_playbooks:
                    stock_code = stock_pb.get("stock_code", "N/A")
                    scenarios = stock_pb.get("scenarios", [])
                    for sc in scenarios:
                        action = sc.get("action", "HOLD")
                        confidence = sc.get("confidence", 0)
                        lines.append(f"- {stock_code}: {action} ({confidence})")
                lines.append("")

            await telegram.send_message("\n".join(lines).strip())
        except Exception as exc:
            logger.error("Error in /scenarios handler: %s", exc)
            await telegram.send_message("<b>⚠️ Error</b>\n\nFailed to retrieve scenarios.")

    async def handle_review() -> None:
        """Handle /review command - show recent scorecards."""
        try:
            rows = db_conn.execute(
                """
                SELECT timeframe, key, value
                FROM contexts
                WHERE layer = 'L6_DAILY' AND key LIKE 'scorecard_%'
                ORDER BY updated_at DESC
                LIMIT 5
                """
            ).fetchall()

            if not rows:
                await telegram.send_message("<b>📝 Recent Reviews</b>\n\nNo scorecards available.")
                return

            lines = ["<b>📝 Recent Reviews</b>", ""]
            for timeframe, key, value in rows:
                scorecard = json.loads(value)
                market = key.replace("scorecard_", "")
                total_pnl = float(scorecard.get("total_pnl", 0.0))
                win_rate = float(scorecard.get("win_rate", 0.0))
                decisions = int(scorecard.get("total_decisions", 0))
                lines.append(
                    f"- {timeframe} {market}: P&L {total_pnl:+.2f}, "
                    f"Win {win_rate:.2f}%, Decisions {decisions}"
                )

            await telegram.send_message("\n".join(lines))
        except Exception as exc:
            logger.error("Error in /review handler: %s", exc)
            await telegram.send_message("<b>⚠️ Error</b>\n\nFailed to retrieve reviews.")

    async def handle_notify(args: list[str]) -> None:
        """Handle /notify [key] [on|off] — query or change notification filters."""
        status = telegram.filter_status()

        # /notify — show current state
        if not args:
            lines = ["<b>🔔 알림 필터 현재 상태</b>\n"]
            for key, enabled in status.items():
                icon = "✅" if enabled else "❌"
                lines.append(f"{icon} <code>{key}</code>")
            lines.append("\n<i>예) /notify scenario off</i>")
            lines.append("<i>예) /notify all off</i>")
            await telegram.send_message("\n".join(lines))
            return

        # /notify [key] — missing on/off
        if len(args) == 1:
            key = args[0].lower()
            if key == "all":
                lines = ["<b>🔔 알림 필터 현재 상태</b>\n"]
                for k, enabled in status.items():
                    icon = "✅" if enabled else "❌"
                    lines.append(f"{icon} <code>{k}</code>")
                await telegram.send_message("\n".join(lines))
            elif key in status:
                icon = "✅" if status[key] else "❌"
                await telegram.send_message(
                    f"<b>🔔 {key}</b>: {icon} {'켜짐' if status[key] else '꺼짐'}\n"
                    f"<i>/notify {key} on  또는  /notify {key} off</i>"
                )
            else:
                valid = ", ".join(list(status.keys()) + ["all"])
                await telegram.send_message(
                    f"❌ 알 수 없는 키: <code>{key}</code>\n유효한 키: {valid}"
                )
            return

        # /notify [key] [on|off]
        key, toggle = args[0].lower(), args[1].lower()
        if toggle not in ("on", "off"):
            await telegram.send_message("❌ on 또는 off 를 입력해 주세요.")
            return
        value = toggle == "on"
        if telegram.set_notification(key, value):
            icon = "✅" if value else "❌"
            label = "전체 알림" if key == "all" else f"<code>{key}</code> 알림"
            state = "켜짐" if value else "꺼짐"
            await telegram.send_message(f"{icon} {label} → {state}")
            logger.info("Notification filter changed via Telegram: %s=%s", key, value)
        else:
            valid = ", ".join(list(telegram.filter_status().keys()) + ["all"])
            await telegram.send_message(f"❌ 알 수 없는 키: <code>{key}</code>\n유효한 키: {valid}")

    async def handle_dashboard() -> None:
        """Handle /dashboard command - show dashboard URL if enabled."""
        if not settings.DASHBOARD_ENABLED:
            await telegram.send_message("<b>🖥️ Dashboard</b>\n\nDashboard is not enabled.")
            return

        url = f"http://{settings.DASHBOARD_HOST}:{settings.DASHBOARD_PORT}"
        await telegram.send_message(f"<b>🖥️ Dashboard</b>\n\n<b>URL:</b> {url}")

    command_handler.register_command("help", handle_help)
    command_handler.register_command("stop", handle_stop)
    command_handler.register_command("resume", handle_resume)
    command_handler.register_command("status", handle_status)
    command_handler.register_command("positions", handle_positions)
    command_handler.register_command("report", handle_report)
    command_handler.register_command("scenarios", handle_scenarios)
    command_handler.register_command("review", handle_review)
    command_handler.register_command("dashboard", handle_dashboard)
    command_handler.register_command_with_args("notify", handle_notify)

    # Initialize volatility hunter
    volatility_analyzer = VolatilityAnalyzer(min_volume_surge=2.0, min_price_change=1.0)
    # Initialize smart scanner (Python-first, AI-last pipeline)
    smart_scanner = SmartVolatilityScanner(
        broker=broker,
        overseas_broker=overseas_broker,
        volatility_analyzer=volatility_analyzer,
        settings=settings,
    )

    # Track scan candidates per market for selection context logging
    scan_candidates: dict[str, dict[str, ScanCandidate]] = {}  # market -> {stock_code -> candidate}

    # Active stocks per market (dynamically discovered by scanner)
    active_stocks: dict[str, list[str]] = {}  # market_code -> [stock_codes]

    # BUY cooldown: prevents retrying a stock rejected for insufficient balance
    buy_cooldown: dict[str, float] = {}  # "{market_code}:{stock_code}" -> expiry timestamp

    # Tracks resubmission attempts per key (max 1 until restart).
    # SELL: "{exchange_code}:{stock_code}", BUY: "BUY:{exchange_code}:{stock_code}".
    sell_resubmit_counts: dict[str, int] = {}

    # Initialize latency control system
    criticality_assessor = CriticalityAssessor(
        critical_pnl_threshold=-2.5,  # Near circuit breaker at -3.0%
        critical_price_change_threshold=5.0,  # 5% in 1 minute
        critical_volume_surge_threshold=10.0,  # 10x average
        high_volatility_threshold=70.0,
        low_volatility_threshold=30.0,
    )
    priority_queue = PriorityTaskQueue(max_size=1000)
    _start_dashboard_server(settings)

    # Track last scan time for each market
    last_scan_time: dict[str, float] = {}

    # Track market open/close state for notifications
    _market_states: dict[str, str] = {}  # market_code -> session_id

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

    # Sync broker positions → DB to prevent double-buy on restart
    try:
        await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)
    except asyncio.CancelledError:
        logger.error("Startup position sync cancelled — propagating shutdown")
        raise
    except Exception as exc:
        logger.warning("Startup position sync failed (non-fatal): %s", exc)

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

            # daily_start_eval: portfolio eval captured at the first session of each
            # trading day.  Reset on calendar-date change so the CB measures only
            # today's drawdown, not cumulative account history.
            _cb_daily_start_eval: float = 0.0
            _cb_last_date: str = ""

            while not shutdown.is_set():
                # Wait for trading to be unpaused
                await pause_trading.wait()
                realtime_hard_stop_task = _restart_realtime_hard_stop_task_if_needed(
                    client=realtime_hard_stop_client,
                    task=realtime_hard_stop_task,
                )
                _run_context_scheduler(context_scheduler, now=datetime.now(UTC))

                # Reset intra-day CB baseline on a new calendar date
                today_str = datetime.now(UTC).date().isoformat()
                if today_str != _cb_last_date:
                    _cb_last_date = today_str
                    _cb_daily_start_eval = 0.0
                    logger.info("New trading day %s — daily CB baseline reset", today_str)

                try:
                    _cb_daily_start_eval = await run_daily_session(
                        broker,
                        overseas_broker,
                        scenario_engine,
                        playbook_store,
                        pre_market_planner,
                        risk,
                        db_conn,
                        decision_logger,
                        context_store,
                        criticality_assessor,
                        telegram,
                        settings,
                        smart_scanner=smart_scanner,
                        daily_start_eval=_cb_daily_start_eval,
                    )
                except CircuitBreakerTripped:
                    logger.critical("Circuit breaker tripped — shutting down")
                    await telegram.notify_circuit_breaker(
                        pnl_pct=settings.CIRCUIT_BREAKER_PCT,
                        threshold=settings.CIRCUIT_BREAKER_PCT,
                    )
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

            _mid_last_date: str = ""

            while not shutdown.is_set():
                # Wait for trading to be unpaused
                await pause_trading.wait()
                realtime_hard_stop_task = _restart_realtime_hard_stop_task_if_needed(
                    client=realtime_hard_stop_client,
                    task=realtime_hard_stop_task,
                )
                _run_context_scheduler(context_scheduler, now=datetime.now(UTC))

                # Reset mid_refreshed on a new calendar date so each trading day
                # gets a fresh mid-session refresh opportunity.
                _today = datetime.now(UTC).date().isoformat()
                if _today != _mid_last_date:
                    _mid_last_date = _today
                    mid_refreshed.clear()
                    _pre_refresh_playbooks.clear()
                    logger.debug("New trading day %s — mid_refreshed reset", _today)

                # Get currently open markets
                open_markets = get_open_markets(
                    settings.enabled_market_list,
                    include_extended_sessions=True,
                )

                if not open_markets:
                    # Notify market close for any markets that were open
                    for market_code, session_id in list(_market_states.items()):
                        if session_id:
                            try:
                                from src.markets.schedule import MARKETS

                                market_info = MARKETS.get(market_code)
                                if market_info:
                                    await _handle_market_close(
                                        market_code=market_code,
                                        market_name=market_info.name,
                                        market_timezone=market_info.timezone,
                                        telegram=telegram,
                                        context_aggregator=context_aggregator,
                                        daily_reviewer=daily_reviewer,
                                        evolution_optimizer=evolution_optimizer,
                                    )
                            except Exception as exc:
                                logger.warning("Market close notification failed: %s", exc)
                            _market_states.pop(market_code, None)
                            # Clear playbook for closed market (new one generated next open)
                            playbooks.pop(market_code, None)

                    # No markets open — wait until next market opens
                    try:
                        next_market, next_open_time = get_next_market_open(
                            settings.enabled_market_list,
                            include_extended_sessions=True,
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

                async def _process_realtime_market(market: MarketInfo) -> None:
                    if shutdown.is_set():
                        return

                    session_info = get_session_info(market)
                    _session_risk_overrides(market=market, settings=settings)
                    logger.info(
                        "Market session active: %s (%s) session=%s",
                        market.code,
                        market.name,
                        session_info.session_id,
                    )

                    await process_blackout_recovery_orders(
                        broker=broker,
                        overseas_broker=overseas_broker,
                        db_conn=db_conn,
                        settings=settings,
                    )

                    # Notify on market/session transition (e.g., US_PRE -> US_REG)
                    session_changed = _has_market_session_transition(
                        _market_states, market.code, session_info.session_id
                    )
                    # Force KR/US regular-session playbook regeneration on session transition.
                    # Without this, an in-memory playbook created in pre-market sessions
                    # (e.g., NXT_PRE, US_PRE) can persist into regular sessions
                    # (KRX_REG, US_DAY) and bypass the stored-playbook reuse gate.
                    if _refresh_cached_playbook_on_session_transition(
                        playbooks=playbooks,
                        session_changed=session_changed,
                        market_code=market.code,
                        session_id=session_info.session_id,
                    ):
                        logger.info(
                            "Session transition requires fresh playbook for %s session=%s",
                            market.code,
                            session_info.session_id,
                        )
                    if session_changed:
                        try:
                            await telegram.notify_market_open(market.name)
                        except Exception as exc:
                            logger.warning("Market open notification failed: %s", exc)
                        _market_states[market.code] = session_info.session_id

                    # Mid-session playbook refresh (12:00 현지 시각)
                    now_utc = datetime.now(UTC)
                    if _should_mid_session_refresh(
                        market_code=market.code,
                        session_id=session_info.session_id,
                        now=now_utc,
                        mid_refreshed=mid_refreshed,
                    ):
                        logger.info(
                            "Mid-session refresh triggered for %s (session=%s)",
                            market.code,
                            session_info.session_id,
                        )
                        # Back up playbook before evicting; restored on failure (issue #436)
                        _pre_refresh_playbooks[market.code] = playbooks.pop(market.code, None)
                        mid_refreshed.add(market.code)

                    # Check and handle domestic pending (unfilled) limit orders.
                    if market.is_domestic:
                        try:
                            await handle_domestic_pending_orders(
                                broker,
                                telegram,
                                settings,
                                sell_resubmit_counts,
                                buy_cooldown,
                                quote_market_div_code=_resolve_domestic_quote_market_div_code(
                                    session_info.session_id
                                ),
                                rollback_open_position=(
                                    lambda **kwargs: _rollback_pending_order_position(
                                        db_conn=db_conn,
                                        runtime_session_id=session_info.session_id,
                                        settings=settings,
                                        **kwargs,
                                    )
                                ),
                            )
                        except Exception as exc:
                            logger.warning("Domestic pending order check failed: %s", exc)

                    # Check and handle overseas pending (unfilled) limit orders.
                    if not market.is_domestic:
                        try:
                            await handle_overseas_pending_orders(
                                overseas_broker,
                                telegram,
                                settings,
                                sell_resubmit_counts,
                                buy_cooldown,
                                rollback_open_position=(
                                    lambda **kwargs: _rollback_pending_order_position(
                                        db_conn=db_conn,
                                        runtime_session_id=session_info.session_id,
                                        settings=settings,
                                        **kwargs,
                                    )
                                ),
                            )
                        except Exception as exc:
                            logger.warning("Pending order check failed: %s", exc)

                    # Smart Scanner: dynamic stock discovery (no static watchlists)
                    now_timestamp = asyncio.get_event_loop().time()
                    last_scan = last_scan_time.get(market.code, 0.0)
                    rescan_interval = settings.RESCAN_INTERVAL_SECONDS
                    if _should_rescan_market(
                        last_scan=last_scan,
                        now_timestamp=now_timestamp,
                        rescan_interval=rescan_interval,
                        session_changed=session_changed,
                    ):
                        try:
                            logger.info("Smart Scanner: Scanning %s market", market.name)

                            fallback_stocks: list[str] | None = None
                            if not market.is_domestic:
                                fallback_stocks = await build_overseas_symbol_universe(
                                    db_conn=db_conn,
                                    overseas_broker=overseas_broker,
                                    market=market,
                                    active_stocks=active_stocks,
                                )
                                if not fallback_stocks:
                                    logger.debug(
                                        "No dynamic overseas symbol universe for %s;"
                                        " scanner will use overseas ranking API",
                                        market.code,
                                    )

                            candidates = await smart_scanner.scan(
                                market=market,
                                fallback_stocks=fallback_stocks,
                            )

                            if candidates:
                                active_stocks[market.code] = smart_scanner.get_stock_codes(
                                    candidates
                                )
                                scan_candidates[market.code] = {c.stock_code: c for c in candidates}

                                logger.info(
                                    "Smart Scanner: Found %d candidates for %s: %s",
                                    len(candidates),
                                    market.name,
                                    [f"{c.stock_code}(RSI={c.rsi:.0f})" for c in candidates],
                                )

                                market_today = datetime.now(market.timezone).date()
                                if market.code not in playbooks:
                                    reuse_stored_pb = _should_reuse_stored_playbook(
                                        market_code=market.code,
                                        session_id=session_info.session_id,
                                    )
                                    stored_pb = (
                                        playbook_store.load_latest(market_today, market.code)
                                        if reuse_stored_pb
                                        else None
                                    )
                                    # If a mid-session playbook exists in the DB, mark this
                                    # market as already refreshed to avoid re-triggering the
                                    # 12:00 mid-session refresh on restart.
                                    if reuse_stored_pb and playbook_store.load(
                                        market_today, market.code, slot="mid"
                                    ) is not None:
                                        mid_refreshed.add(market.code)
                                        logger.debug(
                                            "Resumed with mid-session playbook for %s"
                                            " — suppressing refresh trigger",
                                            market.code,
                                        )
                                    if stored_pb is not None:
                                        playbooks[market.code] = stored_pb
                                        logger.info(
                                            "Loaded existing playbook for %s from DB"
                                            " (%d stocks, %d scenarios)",
                                            market.code,
                                            stored_pb.stock_count,
                                            stored_pb.scenario_count,
                                        )
                                    else:
                                        if not reuse_stored_pb:
                                            logger.info(
                                                "Skipping stored playbook for %s session=%s;"
                                                " generating fresh playbook",
                                                market.code,
                                                session_info.session_id,
                                            )
                                        try:
                                            pb = await pre_market_planner.generate_playbook(
                                                market=market.code,
                                                candidates=candidates,
                                                today=market_today,
                                            )
                                            save_slot = (
                                                "mid"
                                                if market.code in mid_refreshed
                                                else "open"
                                            )
                                            playbook_store.save(pb, slot=save_slot)
                                            playbooks[market.code] = pb
                                            # Generation succeeded — discard pre-refresh backup
                                            _pre_refresh_playbooks.pop(market.code, None)
                                            try:
                                                await telegram.notify_playbook_generated(
                                                    market=market.code,
                                                    stock_count=pb.stock_count,
                                                    scenario_count=pb.scenario_count,
                                                    token_count=pb.token_count,
                                                    slot=save_slot,
                                                )
                                            except Exception as exc:
                                                logger.warning(
                                                    "Playbook notification failed: %s", exc
                                                )
                                        except Exception as exc:
                                            logger.error(
                                                "Playbook generation failed for %s: %s",
                                                market.code,
                                                exc,
                                            )
                                            try:
                                                await telegram.notify_playbook_failed(
                                                    market=market.code,
                                                    reason=str(exc)[:200],
                                                )
                                            except Exception:
                                                pass
                                            # Restore pre-refresh playbook if available (issue #436)
                                            fallback = _pre_refresh_playbooks.pop(
                                                market.code, None
                                            )
                                            if fallback is not None:
                                                playbooks[market.code] = fallback
                                                logger.warning(
                                                    "Mid-session refresh failed for %s;"
                                                    " retaining pre-refresh open playbook",
                                                    market.code,
                                                )
                                            else:
                                                playbooks[market.code] = (
                                                    PreMarketPlanner._empty_playbook(
                                                        market_today, market.code
                                                    )
                                                )
                            else:
                                logger.info(
                                    "Smart Scanner: No candidates for %s — no trades", market.name
                                )
                                active_stocks[market.code] = []

                            last_scan_time[market.code] = now_timestamp

                        except Exception as exc:
                            logger.error("Smart Scanner failed for %s: %s", market.name, exc)

                    scanner_codes = active_stocks.get(market.code, [])
                    try:
                        if market.is_domestic:
                            held_balance = await broker.get_balance()
                        else:
                            held_balance = await overseas_broker.get_overseas_balance(
                                market.exchange_code
                            )
                        held_codes = _extract_held_codes_from_balance(
                            held_balance,
                            is_domestic=market.is_domestic,
                            exchange_code=None if market.is_domestic else market.exchange_code,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to fetch holdings for %s: %s — skipping holdings merge",
                            market.name,
                            exc,
                        )
                        held_codes = []

                    stock_codes = list(dict.fromkeys(scanner_codes + held_codes))
                    extra_held = [c for c in held_codes if c not in set(scanner_codes)]
                    if extra_held:
                        logger.info(
                            "Holdings added to loop for %s (not in scanner): %s",
                            market.name,
                            extra_held,
                        )

                    if not stock_codes:
                        logger.debug("No active stocks for market %s", market.code)
                        return

                    logger.info("Processing market: %s (%d stocks)", market.name, len(stock_codes))

                    for stock_code in stock_codes:
                        if shutdown.is_set():
                            break

                        market_playbook = playbooks.get(
                            market.code,
                            PreMarketPlanner._empty_playbook(
                                datetime.now(market.timezone).date(), market.code
                            ),
                        )

                        for attempt in range(1, MAX_CONNECTION_RETRIES + 1):
                            try:
                                await trading_cycle(
                                    broker,
                                    overseas_broker,
                                    scenario_engine,
                                    market_playbook,
                                    risk,
                                    db_conn,
                                    decision_logger,
                                    context_store,
                                    criticality_assessor,
                                    telegram,
                                    market,
                                    stock_code,
                                    scan_candidates,
                                    settings,
                                    buy_cooldown,
                                    realtime_hard_stop_monitor,
                                    realtime_hard_stop_client,
                                )
                                break
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
                                    await asyncio.sleep(2**attempt)
                                else:
                                    logger.error(
                                        "Connection error for %s (all retries exhausted): %s",
                                        stock_code,
                                        exc,
                                    )
                                    break
                            except Exception as exc:
                                logger.exception("Unexpected error for %s: %s", stock_code, exc)
                                break

                await _run_markets_in_parallel(open_markets, _process_realtime_market)

                # Log priority queue metrics periodically
                metrics = await priority_queue.get_metrics()
                if metrics.total_enqueued > 0:
                    logger.info(
                        "Priority queue metrics: enqueued=%d, dequeued=%d,"
                        " size=%d, timeouts=%d, errors=%d",
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
        # Notify shutdown before closing resources
        await telegram.notify_system_shutdown("Normal shutdown")
        # Clean up resources
        if realtime_hard_stop_client is not None:
            await realtime_hard_stop_client.stop()
        if realtime_hard_stop_task is not None:
            await realtime_hard_stop_task
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
        default=None,
        help="Trading mode override (live only; omit to use environment/default settings)",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Enable FastAPI dashboard server in background thread",
    )
    args = parser.parse_args()

    setup_logging()
    settings = Settings() if args.mode is None else Settings(MODE=args.mode)  # type: ignore[call-arg]
    _ensure_runtime_mode_allowed(settings.MODE)
    settings = _apply_dashboard_flag(settings, args.dashboard)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
