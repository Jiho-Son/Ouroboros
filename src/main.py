"""The Ouroboros — main trading loop.

Orchestrates the broker, brain, and risk manager into a continuous
trading cycle with configurable intervals.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import threading
from datetime import UTC, datetime
from typing import Any

from src.analysis.smart_scanner import ScanCandidate, SmartVolatilityScanner
from src.analysis.volatility import VolatilityAnalyzer
from src.brain.context_selector import ContextSelector
from src.brain.gemini_client import GeminiClient, TradeDecision
from src.broker.kis_api import KISBroker, kr_round_down
from src.broker.overseas import OverseasBroker
from src.config import Settings
from src.context.aggregator import ContextAggregator
from src.context.layer import ContextLayer
from src.context.scheduler import ContextScheduler
from src.context.store import ContextStore
from src.core.criticality import CriticalityAssessor
from src.core.blackout_manager import (
    BlackoutOrderManager,
    QueuedOrderIntent,
    parse_blackout_windows_kst,
)
from src.core.kill_switch import KillSwitchOrchestrator
from src.core.order_policy import OrderPolicyRejected, validate_order_policy
from src.core.priority_queue import PriorityTaskQueue
from src.core.risk_manager import CircuitBreakerTripped, FatFingerRejected, RiskManager
from src.db import (
    get_latest_buy_trade,
    get_open_position,
    get_recent_symbols,
    init_db,
    log_trade,
)
from src.evolution.daily_review import DailyReviewer
from src.evolution.optimizer import EvolutionOptimizer
from src.logging.decision_logger import DecisionLogger
from src.logging_config import setup_logging
from src.markets.schedule import MARKETS, MarketInfo, get_next_market_open, get_open_markets
from src.notifications.telegram_client import NotificationFilter, TelegramClient, TelegramCommandHandler
from src.strategy.models import DayPlaybook, MarketOutlook
from src.strategy.exit_rules import ExitRuleConfig, ExitRuleInput, evaluate_exit
from src.strategy.playbook_store import PlaybookStore
from src.strategy.pre_market_planner import PreMarketPlanner
from src.strategy.position_state_machine import PositionState
from src.strategy.scenario_engine import ScenarioEngine

logger = logging.getLogger(__name__)
KILL_SWITCH = KillSwitchOrchestrator()
BLACKOUT_ORDER_MANAGER = BlackoutOrderManager(
    enabled=False,
    windows=[],
    max_queue_size=500,
)


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
                wait_secs = 2 ** attempt
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
    settings: "Settings",
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
                balance_data = await overseas_broker.get_overseas_balance(
                    market.exchange_code
                )
                log_market = market_code  # e.g. "US_NASDAQ"
        except ConnectionError as exc:
            logger.warning(
                "Startup sync: balance fetch failed for %s — skipping: %s",
                market_code,
                exc,
            )
            continue

        held_codes = _extract_held_codes_from_balance(
            balance_data, is_domestic=market.is_domestic
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
        logger.info(
            "Startup sync complete: %d position(s) synced from broker", synced
        )
    else:
        logger.info("Startup sync: no new positions to sync from broker")
    return synced


def _extract_symbol_from_holding(item: dict[str, Any]) -> str:
    """Extract symbol from overseas holding payload variants."""
    for key in (
        "ovrs_pdno",
        "pdno",
        "ovrs_item_name",
        "prdt_name",
        "symb",
        "symbol",
        "stock_code",
    ):
        value = item.get(key)
        if isinstance(value, str):
            symbol = value.strip().upper()
            if symbol and symbol.replace(".", "").replace("-", "").isalnum():
                return symbol
    return ""


def _extract_held_codes_from_balance(
    balance_data: dict[str, Any],
    *,
    is_domestic: bool,
) -> list[str]:
    """Return stock codes with a positive orderable quantity from a balance response.

    Uses the broker's live output1 as the source of truth so that partial fills
    and manual external trades are always reflected correctly.
    """
    output1 = balance_data.get("output1", [])
    if isinstance(output1, dict):
        output1 = [output1]
    if not isinstance(output1, list):
        return []

    codes: list[str] = []
    for holding in output1:
        if not isinstance(holding, dict):
            continue
        code_key = "pdno" if is_domestic else "ovrs_pdno"
        code = str(holding.get(code_key, "")).strip().upper()
        if not code:
            continue
        if is_domestic:
            qty = int(holding.get("ord_psbl_qty") or holding.get("hldg_qty") or 0)
        else:
            # ord_psbl_qty (주문가능수량) is the actual sellable quantity.
            # ovrs_cblc_qty (해외잔고수량) includes unsettled/expired holdings
            # that cannot actually be sold (e.g. expired warrants).
            qty = int(
                holding.get("ord_psbl_qty")
                or holding.get("ovrs_cblc_qty")
                or holding.get("hldg_qty")
                or 0
            )
        if qty > 0:
            codes.append(code)
    return codes


def _extract_held_qty_from_balance(
    balance_data: dict[str, Any],
    stock_code: str,
    *,
    is_domestic: bool,
) -> int:
    """Extract the broker-confirmed orderable quantity for a stock.

    Uses the broker's live balance response (output1) as the source of truth
    rather than the local DB, because DB records reflect order quantity which
    may differ from actual fill quantity due to partial fills.

    Domestic fields (VTTC8434R output1):
        pdno          — 종목코드
        ord_psbl_qty  — 주문가능수량 (preferred: excludes unsettled)
        hldg_qty      — 보유수량 (fallback)

    Overseas fields (VTTS3012R / TTTS3012R output1):
        ovrs_pdno     — 종목코드
        ord_psbl_qty  — 주문가능수량 (preferred: actual sellable qty)
        ovrs_cblc_qty — 해외잔고수량 (fallback: total holding, may include
                        unsettled or expired positions with ord_psbl_qty=0)
        hldg_qty      — 보유수량 (last-resort fallback)
    """
    output1 = balance_data.get("output1", [])
    if isinstance(output1, dict):
        output1 = [output1]
    if not isinstance(output1, list):
        return 0

    for holding in output1:
        if not isinstance(holding, dict):
            continue
        code_key = "pdno" if is_domestic else "ovrs_pdno"
        held_code = str(holding.get(code_key, "")).strip().upper()
        if held_code != stock_code.strip().upper():
            continue
        if is_domestic:
            qty = int(holding.get("ord_psbl_qty") or holding.get("hldg_qty") or 0)
        else:
            qty = int(
                holding.get("ord_psbl_qty")
                or holding.get("ovrs_cblc_qty")
                or holding.get("hldg_qty")
                or 0
            )
        return qty
    return 0


def _extract_avg_price_from_balance(
    balance_data: dict[str, Any],
    stock_code: str,
    *,
    is_domestic: bool,
) -> float:
    """Extract the broker-reported average purchase price for a stock.

    Uses ``pchs_avg_pric`` (매입평균가격) from the balance response (output1).
    Returns 0.0 when absent so callers can use ``if price > 0`` as sentinel.

    Domestic fields (VTTC8434R output1):  pdno, pchs_avg_pric
    Overseas fields (VTTS3012R output1):  ovrs_pdno, pchs_avg_pric
    """
    output1 = balance_data.get("output1", [])
    if isinstance(output1, dict):
        output1 = [output1]
    if not isinstance(output1, list):
        return 0.0

    for holding in output1:
        if not isinstance(holding, dict):
            continue
        code_key = "pdno" if is_domestic else "ovrs_pdno"
        held_code = str(holding.get(code_key, "")).strip().upper()
        if held_code != stock_code.strip().upper():
            continue
        return safe_float(holding.get("pchs_avg_pric"), 0.0)
    return 0.0


def _determine_order_quantity(
    *,
    action: str,
    current_price: float,
    total_cash: float,
    candidate: ScanCandidate | None,
    settings: Settings | None,
    broker_held_qty: int = 0,
    playbook_allocation_pct: float | None = None,
    scenario_confidence: int = 80,
) -> int:
    """Determine order quantity using volatility-aware position sizing.

    Priority:
    1. playbook_allocation_pct (AI-specified) scaled by scenario_confidence
    2. Fallback: volatility-score-based allocation from scanner candidate
    """
    if action == "SELL":
        return broker_held_qty
    if current_price <= 0 or total_cash <= 0:
        return 0

    if settings is None or not settings.POSITION_SIZING_ENABLED:
        return 1

    # Use AI-specified allocation_pct if available
    if playbook_allocation_pct is not None:
        # Confidence scaling: confidence 80 → 1.0x, confidence 95 → 1.19x
        confidence_scale = scenario_confidence / 80.0
        effective_pct = min(
            settings.POSITION_MAX_ALLOCATION_PCT,
            max(
                settings.POSITION_MIN_ALLOCATION_PCT,
                playbook_allocation_pct * confidence_scale,
            ),
        )
        budget = total_cash * (effective_pct / 100.0)
        quantity = int(budget // current_price)
        return max(0, quantity)

    # Fallback: volatility-score-based allocation
    target_score = max(1.0, settings.POSITION_VOLATILITY_TARGET_SCORE)
    observed_score = candidate.score if candidate else target_score
    observed_score = max(1.0, min(100.0, observed_score))

    # Higher observed volatility score => smaller allocation.
    scaled_pct = settings.POSITION_BASE_ALLOCATION_PCT * (target_score / observed_score)
    allocation_pct = min(
        settings.POSITION_MAX_ALLOCATION_PCT,
        max(settings.POSITION_MIN_ALLOCATION_PCT, scaled_pct),
    )

    budget = total_cash * (allocation_pct / 100.0)
    quantity = int(budget // current_price)
    if quantity <= 0:
        return 0
    return quantity


def _should_block_overseas_buy_for_fx_buffer(
    *,
    market: MarketInfo,
    action: str,
    total_cash: float,
    order_amount: float,
    settings: Settings | None,
) -> tuple[bool, float, float]:
    if market.is_domestic or action != "BUY" or settings is None:
        return False, total_cash - order_amount, 0.0
    remaining = total_cash - order_amount
    required = settings.USD_BUFFER_MIN
    return remaining < required, remaining, required


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
        output1 = balance_data.get("output1", [])
        if isinstance(output1, dict):
            output1 = [output1]
        if isinstance(output1, list):
            for row in output1:
                if not isinstance(row, dict):
                    continue
                symbol = _extract_symbol_from_holding(row)
                if symbol:
                    symbols.append(symbol)
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


def _build_queued_order_intent(
    *,
    market: MarketInfo,
    stock_code: str,
    order_type: str,
    quantity: int,
    price: float,
    source: str,
) -> QueuedOrderIntent:
    return QueuedOrderIntent(
        market_code=market.code,
        exchange_code=market.exchange_code,
        stock_code=stock_code,
        order_type=order_type,
        quantity=quantity,
        price=price,
        source=source,
        queued_at=datetime.now(UTC),
    )


def _maybe_queue_order_intent(
    *,
    market: MarketInfo,
    stock_code: str,
    order_type: str,
    quantity: int,
    price: float,
    source: str,
) -> bool:
    if not BLACKOUT_ORDER_MANAGER.in_blackout():
        return False

    queued = BLACKOUT_ORDER_MANAGER.enqueue(
        _build_queued_order_intent(
            market=market,
            stock_code=stock_code,
            order_type=order_type,
            quantity=quantity,
            price=price,
            source=source,
        )
    )
    if queued:
        logger.warning(
            "Blackout active: queued order intent %s %s (%s) qty=%d price=%.4f source=%s pending=%d",
            order_type,
            stock_code,
            market.code,
            quantity,
            price,
            source,
            BLACKOUT_ORDER_MANAGER.pending_count,
        )
    else:
        logger.error(
            "Blackout queue full: dropped order intent %s %s (%s) qty=%d source=%s",
            order_type,
            stock_code,
            market.code,
            quantity,
            source,
        )
    return True


async def process_blackout_recovery_orders(
    *,
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    db_conn: Any,
) -> None:
    intents = BLACKOUT_ORDER_MANAGER.pop_recovery_batch()
    if not intents:
        return

    logger.info(
        "Blackout recovery started: processing %d queued intents",
        len(intents),
    )
    for intent in intents:
        market = MARKETS.get(intent.market_code)
        if market is None:
            continue

        open_position = get_open_position(db_conn, intent.stock_code, market.code)
        if intent.order_type == "BUY" and open_position is not None:
            logger.info(
                "Drop stale queued BUY %s (%s): position already open",
                intent.stock_code,
                market.code,
            )
            continue
        if intent.order_type == "SELL" and open_position is None:
            logger.info(
                "Drop stale queued SELL %s (%s): no open position",
                intent.stock_code,
                market.code,
            )
            continue

        try:
            validate_order_policy(
                market=market,
                order_type=intent.order_type,
                price=float(intent.price),
            )
            if market.is_domestic:
                result = await broker.send_order(
                    stock_code=intent.stock_code,
                    order_type=intent.order_type,
                    quantity=intent.quantity,
                    price=intent.price,
                )
            else:
                result = await overseas_broker.send_overseas_order(
                    exchange_code=market.exchange_code,
                    stock_code=intent.stock_code,
                    order_type=intent.order_type,
                    quantity=intent.quantity,
                    price=intent.price,
                )

            accepted = result.get("rt_cd", "0") == "0"
            if accepted:
                logger.info(
                    "Recovered queued order executed: %s %s (%s) qty=%d price=%.4f source=%s",
                    intent.order_type,
                    intent.stock_code,
                    market.code,
                    intent.quantity,
                    intent.price,
                    intent.source,
                )
                continue
            logger.warning(
                "Recovered queued order rejected: %s %s (%s) qty=%d msg=%s",
                intent.order_type,
                intent.stock_code,
                market.code,
                intent.quantity,
                result.get("msg1"),
            )
        except Exception as exc:
            if isinstance(exc, OrderPolicyRejected):
                logger.info(
                    "Drop queued intent by policy: %s %s (%s): %s",
                    intent.order_type,
                    intent.stock_code,
                    market.code,
                    exc,
                )
                continue
            logger.warning(
                "Recovered queued order failed: %s %s (%s): %s",
                intent.order_type,
                intent.stock_code,
                market.code,
                exc,
            )
            if intent.attempts < 2:
                intent.attempts += 1
                BLACKOUT_ORDER_MANAGER.requeue(intent)


def _resolve_kill_switch_markets(
    *,
    settings: Settings | None,
    current_market: MarketInfo | None,
) -> list[MarketInfo]:
    if settings is not None:
        markets: list[MarketInfo] = []
        seen: set[str] = set()
        for market_code in settings.enabled_market_list:
            market = MARKETS.get(market_code)
            if market is None or market.code in seen:
                continue
            markets.append(market)
            seen.add(market.code)
        if markets:
            return markets
    if current_market is not None:
        return [current_market]
    return []


async def _cancel_pending_orders_for_kill_switch(
    *,
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    markets: list[MarketInfo],
) -> None:
    failures: list[str] = []
    domestic = [m for m in markets if m.is_domestic]
    overseas = [m for m in markets if not m.is_domestic]

    if domestic:
        try:
            orders = await broker.get_domestic_pending_orders()
        except Exception as exc:
            logger.warning("KillSwitch: failed to fetch domestic pending orders: %s", exc)
            orders = []
        for order in orders:
            stock_code = str(order.get("pdno", ""))
            try:
                orgn_odno = order.get("orgn_odno", "")
                krx_fwdg_ord_orgno = order.get("ord_gno_brno", "")
                psbl_qty = int(order.get("psbl_qty", "0") or "0")
                if not stock_code or not orgn_odno or psbl_qty <= 0:
                    continue
                cancel_result = await broker.cancel_domestic_order(
                    stock_code=stock_code,
                    orgn_odno=orgn_odno,
                    krx_fwdg_ord_orgno=krx_fwdg_ord_orgno,
                    qty=psbl_qty,
                )
                if cancel_result.get("rt_cd") != "0":
                    failures.append(
                        "domestic cancel failed for"
                        f" {stock_code}: rt_cd={cancel_result.get('rt_cd')}"
                        f" msg={cancel_result.get('msg1')}"
                    )
            except Exception as exc:
                logger.warning("KillSwitch: domestic cancel failed: %s", exc)
                failures.append(f"domestic cancel exception for {stock_code}: {exc}")

    us_exchanges = frozenset({"NASD", "NYSE", "AMEX"})
    exchange_codes: list[str] = []
    seen_us = False
    for market in overseas:
        exc_code = market.exchange_code
        if exc_code in us_exchanges:
            if not seen_us:
                exchange_codes.append("NASD")
                seen_us = True
        elif exc_code not in exchange_codes:
            exchange_codes.append(exc_code)

    for exchange_code in exchange_codes:
        try:
            orders = await overseas_broker.get_overseas_pending_orders(exchange_code)
        except Exception as exc:
            logger.warning(
                "KillSwitch: failed to fetch overseas pending orders for %s: %s",
                exchange_code,
                exc,
            )
            continue
        for order in orders:
            stock_code = str(order.get("pdno", ""))
            order_exchange = str(order.get("ovrs_excg_cd") or exchange_code)
            try:
                odno = order.get("odno", "")
                nccs_qty = int(order.get("nccs_qty", "0") or "0")
                if not stock_code or not odno or nccs_qty <= 0:
                    continue
                cancel_result = await overseas_broker.cancel_overseas_order(
                    exchange_code=order_exchange,
                    stock_code=stock_code,
                    odno=odno,
                    qty=nccs_qty,
                )
                if cancel_result.get("rt_cd") != "0":
                    failures.append(
                        "overseas cancel failed for"
                        f" {order_exchange}/{stock_code}: rt_cd={cancel_result.get('rt_cd')}"
                        f" msg={cancel_result.get('msg1')}"
                    )
            except Exception as exc:
                logger.warning("KillSwitch: overseas cancel failed: %s", exc)
                failures.append(
                    f"overseas cancel exception for {order_exchange}/{stock_code}: {exc}"
                )

    if failures:
        raise RuntimeError("; ".join(failures[:3]))


async def _refresh_order_state_for_kill_switch(
    *,
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    markets: list[MarketInfo],
) -> None:
    seen_overseas: set[str] = set()
    for market in markets:
        try:
            if market.is_domestic:
                await broker.get_balance()
            elif market.exchange_code not in seen_overseas:
                seen_overseas.add(market.exchange_code)
                await overseas_broker.get_overseas_balance(market.exchange_code)
        except Exception as exc:
            logger.warning(
                "KillSwitch: refresh state failed for %s/%s: %s",
                market.code,
                market.exchange_code,
                exc,
            )


def _reduce_risk_for_kill_switch() -> None:
    dropped = BLACKOUT_ORDER_MANAGER.clear()
    logger.critical("KillSwitch: reduced queued order risk by clearing %d queued intents", dropped)


async def _trigger_emergency_kill_switch(
    *,
    reason: str,
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    telegram: TelegramClient,
    settings: Settings | None,
    current_market: MarketInfo | None,
    stock_code: str,
    pnl_pct: float,
    threshold: float,
) -> Any:
    markets = _resolve_kill_switch_markets(settings=settings, current_market=current_market)
    return await KILL_SWITCH.trigger(
        reason=reason,
        cancel_pending_orders=lambda: _cancel_pending_orders_for_kill_switch(
            broker=broker,
            overseas_broker=overseas_broker,
            markets=markets,
        ),
        refresh_order_state=lambda: _refresh_order_state_for_kill_switch(
            broker=broker,
            overseas_broker=overseas_broker,
            markets=markets,
        ),
        reduce_risk=_reduce_risk_for_kill_switch,
        snapshot_state=lambda: logger.critical(
            "KillSwitch snapshot %s/%s pnl=%.2f threshold=%.2f",
            current_market.code if current_market else "UNKNOWN",
            stock_code,
            pnl_pct,
            threshold,
        ),
        notify=lambda: telegram.notify_circuit_breaker(
            pnl_pct=pnl_pct,
            threshold=threshold,
        ),
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
) -> None:
    """Execute one trading cycle for a single stock."""
    cycle_start_time = asyncio.get_event_loop().time()

    # 1. Fetch market data
    price_output: dict[str, Any] = {}  # Populated for overseas markets; used for fallback metrics
    if market.is_domestic:
        current_price, price_change_pct, foreigner_net = await broker.get_current_price(
            stock_code
        )
        balance_data = await broker.get_balance()

        output2 = balance_data.get("output2", [{}])
        total_eval = safe_float(output2[0].get("tot_evlu_amt", "0")) if output2 else 0
        total_cash = safe_float(
            balance_data.get("output2", [{}])[0].get("dnca_tot_amt", "0")
            if output2
            else "0"
        )
        purchase_total = safe_float(output2[0].get("pchs_amt_smtl_amt", "0")) if output2 else 0
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
        purchase_total = safe_float(balance_info.get("frcr_buy_amt_smtl", "0") or "0")

        # Resolve current price first (needed for buying power API)
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
        foreigner_net = 0.0  # Not available for overseas
        price_change_pct = safe_float(price_output.get("rate", "0"))

        # Fetch available foreign currency cash via inquire-psamount (TTTS3007R/VTTS3007R).
        # TTTS3012R output2 does not include a cash/deposit field — frcr_dncl_amt_2 does not exist.
        # Source: 한국투자증권 오픈API 전체문서 (20260221) — '해외주식 매수가능금액조회' 시트
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

        # Paper mode fallback: VTS overseas balance API often fails for many accounts.
        # Only activate in paper mode — live mode must use real balance from KIS.
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

    # Calculate daily P&L %
    pnl_pct = (
        ((total_eval - purchase_total) / purchase_total * 100)
        if purchase_total > 0
        else 0.0
    )

    market_data: dict[str, Any] = {
        "stock_code": stock_code,
        "market_name": market.name,
        "current_price": current_price,
        "foreigner_net": foreigner_net,
        "price_change_pct": price_change_pct,
    }

    # Enrich market_data with scanner metrics for scenario engine
    market_candidates = scan_candidates.get(market.code, {})
    candidate = market_candidates.get(stock_code)
    if candidate:
        market_data["rsi"] = candidate.rsi
        market_data["volume_ratio"] = candidate.volume_ratio
    else:
        # Holding stocks not in scanner: derive metrics from price API data already fetched.
        # For overseas stocks, price_output contains high/low/rate from get_overseas_price.
        # For domestic stocks, only price_change_pct is available from get_current_price.
        market_data["rsi"] = max(0.0, min(100.0, 50.0 + price_change_pct * 2.0))
        if price_output and current_price > 0:
            pr_high = safe_float(
                price_output.get("high") or price_output.get("ovrs_hgpr")
                or price_output.get("stck_hgpr")
            )
            pr_low = safe_float(
                price_output.get("low") or price_output.get("ovrs_lwpr")
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

    # Enrich market_data with holding info for SELL/HOLD scenario conditions
    open_pos = get_open_position(db_conn, stock_code, market.code)
    if open_pos and current_price > 0:
        entry_price = safe_float(open_pos.get("price"), 0.0)
        if entry_price > 0:
            market_data["unrealized_pnl_pct"] = (
                (current_price - entry_price) / entry_price * 100
            )
        entry_ts = open_pos.get("timestamp")
        if entry_ts:
            try:
                entry_date = datetime.fromisoformat(entry_ts).date()
                market_data["holding_days"] = (datetime.now(UTC).date() - entry_date).days
            except (ValueError, TypeError):
                pass

    # 1.3. Record L7 real-time context (market-scoped keys)
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

    # Write pnl_pct to system_metrics (dashboard-only table, separate from AI context tree)
    db_conn.execute(
        "INSERT OR REPLACE INTO system_metrics (key, value, updated_at) VALUES (?, ?, ?)",
        (
            f"portfolio_pnl_pct_{market.code}",
            json.dumps({"pnl_pct": round(pnl_pct, 4)}),
            datetime.now(UTC).isoformat(),
        ),
    )
    db_conn.commit()

    # Build portfolio data for global rule evaluation
    portfolio_data = {
        "portfolio_pnl_pct": pnl_pct,
        "total_cash": total_cash,
        "total_eval": total_eval,
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
            f"volatility_{market.code}_{stock_code}",
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

    # 2. Evaluate scenario (local, no API call)
    match = scenario_engine.evaluate(playbook, stock_code, market_data, portfolio_data)
    decision = TradeDecision(
        action=match.action.value,
        confidence=match.confidence,
        rationale=match.rationale,
    )
    stock_playbook = playbook.get_stock_playbook(stock_code)

    # 2.1. Apply market_outlook-based BUY confidence threshold
    if decision.action == "BUY":
        base_threshold = (settings.CONFIDENCE_THRESHOLD if settings else 80)
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

    # BUY 결정 전 기존 포지션 체크 (중복 매수 방지)
    if decision.action == "BUY":
        existing_position = get_open_position(db_conn, stock_code, market.code)
        if not existing_position:
            # SELL 지정가 접수 후 미체결 시 DB는 종료로 기록되나 브로커는 여전히 보유 중.
            # 국내/해외 모두 라이브 브로커 잔고를 authoritative source로 사용.
            broker_qty = _extract_held_qty_from_balance(
                balance_data, stock_code, is_domestic=market.is_domestic
            )
            if broker_qty > 0:
                existing_position = {"price": 0.0, "quantity": broker_qty}
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

    if decision.action == "HOLD":
        open_position = get_open_position(db_conn, stock_code, market.code)
        if open_position:
            entry_price = safe_float(open_position.get("price"), 0.0)
            if entry_price > 0 and current_price > 0:
                loss_pct = (current_price - entry_price) / entry_price * 100
                stop_loss_threshold = -2.0
                take_profit_threshold = 3.0
                if stock_playbook and stock_playbook.scenarios:
                    stop_loss_threshold = stock_playbook.scenarios[0].stop_loss_pct
                    take_profit_threshold = stock_playbook.scenarios[0].take_profit_pct

                exit_eval = evaluate_exit(
                    current_state=PositionState.HOLDING,
                    config=ExitRuleConfig(
                        hard_stop_pct=stop_loss_threshold,
                        be_arm_pct=max(0.5, take_profit_threshold * 0.4),
                        arm_pct=take_profit_threshold,
                    ),
                    inp=ExitRuleInput(
                        current_price=current_price,
                        entry_price=entry_price,
                        peak_price=max(entry_price, current_price),
                        atr_value=0.0,
                        pred_down_prob=0.0,
                        liquidity_weak=market_data.get("volume_ratio", 1.0) < 1.0,
                    ),
                )

                if exit_eval.reason == "hard_stop":
                    decision = TradeDecision(
                        action="SELL",
                        confidence=95,
                        rationale=(
                            f"Stop-loss triggered ({loss_pct:.2f}% <= "
                            f"{stop_loss_threshold:.2f}%)"
                        ),
                    )
                    logger.info(
                        "Stop-loss override for %s (%s): %.2f%% <= %.2f%%",
                        stock_code,
                        market.name,
                        loss_pct,
                        stop_loss_threshold,
                    )
                elif exit_eval.reason == "arm_take_profit":
                    decision = TradeDecision(
                        action="SELL",
                        confidence=90,
                        rationale=(
                            f"Take-profit triggered ({loss_pct:.2f}% >= "
                            f"{take_profit_threshold:.2f}%)"
                        ),
                    )
                    logger.info(
                        "Take-profit override for %s (%s): %.2f%% >= %.2f%%",
                        stock_code,
                        market.name,
                        loss_pct,
                        take_profit_threshold,
                    )
    logger.info(
        "Decision for %s (%s): %s (confidence=%d)",
        stock_code,
        market.name,
        decision.action,
        decision.confidence,
    )

    # 2.1. Notify scenario match
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

    decision_id = decision_logger.log_decision(
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
    quantity = 0
    trade_price = current_price
    trade_pnl = 0.0
    if decision.action in ("BUY", "SELL"):
        if KILL_SWITCH.new_orders_blocked:
            logger.critical(
                "KillSwitch block active: skip %s order for %s (%s)",
                decision.action,
                stock_code,
                market.name,
            )
            return

        broker_held_qty = (
            _extract_held_qty_from_balance(
                balance_data, stock_code, is_domestic=market.is_domestic
            )
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
        if quantity <= 0:
            logger.info(
                "Skip %s %s (%s): no affordable quantity (cash=%.2f, price=%.2f)",
                decision.action,
                stock_code,
                market.name,
                total_cash,
                current_price,
            )
            return
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
                "Skip BUY %s (%s): FX buffer guard (remaining=%.2f, required=%.2f, cash=%.2f, order=%.2f)",
                stock_code,
                market.name,
                remaining_cash,
                required_buffer,
                total_cash,
                order_amount,
            )
            return

        # 4. Check BUY cooldown (set when a prior BUY failed due to insufficient balance)
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
                return

        # 5a. Risk check BEFORE order
        # SELL orders do not consume cash (they receive it), so fat-finger check
        # is skipped for SELLs — only circuit breaker applies.
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
            raise  # Re-raise to prevent trade
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

        # 5. Send order
        order_succeeded = True
        if market.is_domestic:
            # Use limit orders (지정가) for domestic stocks to avoid market order
            # quantity calculation issues. KRX tick rounding applied via kr_round_down.
            # BUY: +0.2% — ensures fill even when ask is slightly above last price.
            # SELL: -0.2% — ensures fill even when bid is slightly below last price.
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
                return
            if _maybe_queue_order_intent(
                market=market,
                stock_code=stock_code,
                order_type=decision.action,
                quantity=quantity,
                price=float(order_price),
                source="trading_cycle",
            ):
                return
            result = await broker.send_order(
                stock_code=stock_code,
                order_type=decision.action,
                quantity=quantity,
                price=order_price,
            )
        else:
            # For overseas orders, always use limit orders (지정가):
            # - KIS market orders (ORD_DVSN=01) calculate quantity based on upper limit
            #   price (상한가 기준), resulting in only 60-80% of intended cash being used.
            # - BUY: +0.2% above last price — tight enough to minimise overpayment while
            #   achieving >90% fill rate on large-cap US stocks.
            # - SELL: -0.2% below last price — ensures fill even when price dips slightly
            #   (placing at exact last price risks no-fill if the bid is just below).
            overseas_price: float
            # KIS requires at most 2 decimal places for prices >= $1 (≥1달러 소수점 2자리 제한).
            # Penny stocks (< $1) keep 4 decimal places to preserve price precision.
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
                return
            if _maybe_queue_order_intent(
                market=market,
                stock_code=stock_code,
                order_type=decision.action,
                quantity=quantity,
                price=float(overseas_price),
                source="trading_cycle",
            ):
                return
            result = await overseas_broker.send_overseas_order(
                exchange_code=market.exchange_code,
                stock_code=stock_code,
                order_type=decision.action,
                quantity=quantity,
                price=overseas_price,  # limit order
            )
            # Check if KIS rejected the order (rt_cd != "0")
            if result.get("rt_cd", "") != "0":
                order_succeeded = False
                msg1 = result.get("msg1") or ""
                logger.warning(
                    "Overseas order not accepted for %s: rt_cd=%s msg=%s",
                    stock_code,
                    result.get("rt_cd"),
                    msg1,
                )
                # Set BUY cooldown when the rejection is due to insufficient balance
                if decision.action == "BUY" and buy_cooldown is not None and "주문가능금액" in msg1:
                    cooldown_key = f"{market.code}:{stock_code}"
                    buy_cooldown[cooldown_key] = (
                        asyncio.get_event_loop().time() + _BUY_COOLDOWN_SECONDS
                    )
                    logger.info(
                        "BUY cooldown set for %s: %.0fs (insufficient balance)",
                        stock_code,
                        _BUY_COOLDOWN_SECONDS,
                    )
                # Close ghost position when broker has no matching balance.
                # This prevents infinite SELL retry cycles for positions that
                # exist in the DB (from startup sync) but are no longer
                # sellable at the broker (expired warrants, delisted stocks, etc.)
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
                            "[ghost-close] Broker reported no balance;"
                            " position closed without fill"
                        ),
                        quantity=0,
                        price=0.0,
                        pnl=0.0,
                        market=market.code,
                        exchange_code=market.exchange_code,
                        mode=settings.MODE if settings else "paper",
                    )
        logger.info("Order result: %s", result.get("msg1", "OK"))

        # 5.5. Notify trade execution (only on success)
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
            buy_trade = get_latest_buy_trade(db_conn, stock_code, market.code)
            if buy_trade and buy_trade.get("price") is not None:
                buy_price = float(buy_trade["price"])
                buy_qty = int(buy_trade.get("quantity") or 1)
                trade_pnl = (trade_price - buy_price) * buy_qty
                decision_logger.update_outcome(
                    decision_id=buy_trade["decision_id"],
                    pnl=trade_pnl,
                    accuracy=1 if trade_pnl > 0 else 0,
                )

    # 6. Log trade with selection context (skip if order was rejected)
    if decision.action in ("BUY", "SELL") and not order_succeeded:
        return
    selection_context = None
    if stock_code in market_candidates:
        candidate = market_candidates[stock_code]
        selection_context = {
            "rsi": candidate.rsi,
            "volume_ratio": candidate.volume_ratio,
            "signal": candidate.signal,
            "score": candidate.score,
        }

    log_trade(
        conn=db_conn,
        stock_code=stock_code,
        action=decision.action,
        confidence=decision.confidence,
        rationale=decision.rationale,
        quantity=quantity,
        price=trade_price,
        pnl=trade_pnl,
        market=market.code,
        exchange_code=market.exchange_code,
        selection_context=selection_context,
        decision_id=decision_id,
        mode=settings.MODE if settings else "paper",
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


async def handle_domestic_pending_orders(
    broker: KISBroker,
    telegram: TelegramClient,
    settings: Settings,
    sell_resubmit_counts: dict[str, int],
    buy_cooldown: dict[str, float] | None = None,
) -> None:
    """Check and handle unfilled (pending) domestic limit orders.

    Called once per market loop iteration before new orders are considered.
    In paper mode the KIS pending-orders API (TTTC0084R) is unsupported, so
    ``get_domestic_pending_orders`` returns [] immediately and this function
    exits without making further API calls.

    BUY pending  → cancel (to free up balance) + optionally set cooldown.
    SELL pending → cancel then resubmit at a wider spread (-0.4% from last
                   price, kr_round_down applied).  Resubmission is attempted
                   at most once per key per session to avoid infinite loops.

    Args:
        broker: KISBroker instance.
        telegram: TelegramClient for notifications.
        settings: Application settings.
        sell_resubmit_counts: Mutable dict tracking SELL resubmission attempts
            per "KR:{stock_code}" key.  Passed by reference so counts persist
            across calls within the same session.
        buy_cooldown: Optional cooldown dict shared with the main trading loop.
            When provided, cancelled BUY orders are added with a
            _BUY_COOLDOWN_SECONDS expiry.
    """
    try:
        orders = await broker.get_domestic_pending_orders()
    except Exception as exc:
        logger.warning("Failed to fetch domestic pending orders: %s", exc)
        return

    now = asyncio.get_event_loop().time()

    for order in orders:
        try:
            stock_code = order.get("pdno", "")
            orgn_odno = order.get("orgn_odno", "")
            krx_fwdg_ord_orgno = order.get("ord_gno_brno", "")
            sll_buy = order.get("sll_buy_dvsn_cd", "")  # "01"=SELL, "02"=BUY
            psbl_qty = int(order.get("psbl_qty", "0") or "0")
            key = f"KR:{stock_code}"

            if not stock_code or not orgn_odno or psbl_qty <= 0:
                continue

            # Cancel the pending order first regardless of direction.
            cancel_result = await broker.cancel_domestic_order(
                stock_code=stock_code,
                orgn_odno=orgn_odno,
                krx_fwdg_ord_orgno=krx_fwdg_ord_orgno,
                qty=psbl_qty,
            )
            if cancel_result.get("rt_cd") != "0":
                logger.warning(
                    "Cancel failed for KR %s: rt_cd=%s msg=%s",
                    stock_code,
                    cancel_result.get("rt_cd"),
                    cancel_result.get("msg1"),
                )
                continue

            if sll_buy == "02":
                # BUY pending → cancelled; set cooldown to avoid immediate re-buy.
                if buy_cooldown is not None:
                    buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                try:
                    await telegram.notify_unfilled_order(
                        stock_code=stock_code,
                        market="KR",
                        action="BUY",
                        quantity=psbl_qty,
                        outcome="cancelled",
                    )
                except Exception as notify_exc:
                    logger.warning("notify_unfilled_order failed: %s", notify_exc)

            elif sll_buy == "01":
                # SELL pending — attempt one resubmit at a wider spread.
                if sell_resubmit_counts.get(key, 0) >= 1:
                    # Already resubmitted once — only cancel (already done above).
                    logger.warning(
                        "SELL KR %s already resubmitted once — no further resubmit",
                        stock_code,
                    )
                    try:
                        await telegram.notify_unfilled_order(
                            stock_code=stock_code,
                            market="KR",
                            action="SELL",
                            quantity=psbl_qty,
                            outcome="cancelled",
                        )
                    except Exception as notify_exc:
                        logger.warning(
                            "notify_unfilled_order failed: %s", notify_exc
                        )
                else:
                    # First unfilled SELL → resubmit at last * 0.996 (-0.4%).
                    try:
                        last_price, _, _ = await broker.get_current_price(stock_code)
                        if last_price <= 0:
                            raise ValueError(
                                f"Invalid price ({last_price}) for {stock_code}"
                            )
                        new_price = kr_round_down(last_price * 0.996)
                        validate_order_policy(
                            market=MARKETS["KR"],
                            order_type="SELL",
                            price=float(new_price),
                        )
                        await broker.send_order(
                            stock_code=stock_code,
                            order_type="SELL",
                            quantity=psbl_qty,
                            price=new_price,
                        )
                        sell_resubmit_counts[key] = (
                            sell_resubmit_counts.get(key, 0) + 1
                        )
                        try:
                            await telegram.notify_unfilled_order(
                                stock_code=stock_code,
                                market="KR",
                                action="SELL",
                                quantity=psbl_qty,
                                outcome="resubmitted",
                                new_price=float(new_price),
                            )
                        except Exception as notify_exc:
                            logger.warning(
                                "notify_unfilled_order failed: %s", notify_exc
                            )
                    except Exception as exc:
                        logger.error(
                            "SELL resubmit failed for KR %s: %s",
                            stock_code,
                            exc,
                        )

        except Exception as exc:
            logger.error(
                "Error handling domestic pending order for %s: %s",
                order.get("pdno", "?"),
                exc,
            )


async def handle_overseas_pending_orders(
    overseas_broker: OverseasBroker,
    telegram: TelegramClient,
    settings: Settings,
    sell_resubmit_counts: dict[str, int],
    buy_cooldown: dict[str, float] | None = None,
) -> None:
    """Check and handle unfilled (pending) overseas limit orders.

    Called once per market loop iteration before new orders are considered.
    In paper mode the KIS pending-orders API (TTTS3018R) is unsupported, so
    this function returns immediately without making any API calls.

    BUY pending  → cancel (to free up balance) + optionally set cooldown.
    SELL pending → cancel then resubmit at a wider spread (-0.4% from last
                   price).  Resubmission is attempted at most once per key
                   per session to avoid infinite retry loops.

    Args:
        overseas_broker: OverseasBroker instance.
        telegram: TelegramClient for notifications.
        settings: Application settings (MODE, ENABLED_MARKETS).
        sell_resubmit_counts: Mutable dict tracking SELL resubmission attempts
            per "{exchange_code}:{stock_code}" key.  Passed by reference so
            counts persist across calls within the same session.
        buy_cooldown: Optional cooldown dict shared with the main trading loop.
            When provided, cancelled BUY orders are added with a
            _BUY_COOLDOWN_SECONDS expiry.
    """
    # Determine which exchange codes to query, deduplicating US exchanges.
    # NASD alone returns all US (NASD/NYSE/AMEX) pending orders.
    us_exchanges = frozenset({"NASD", "NYSE", "AMEX"})
    exchange_codes: list[str] = []
    seen_us = False
    for market_code in settings.enabled_market_list:
        market_info = MARKETS.get(market_code)
        if market_info is None or market_info.is_domestic:
            continue
        exc_code = market_info.exchange_code
        if exc_code in us_exchanges:
            if not seen_us:
                exchange_codes.append("NASD")
                seen_us = True
        elif exc_code not in exchange_codes:
            exchange_codes.append(exc_code)

    now = asyncio.get_event_loop().time()

    for exchange_code in exchange_codes:
        try:
            orders = await overseas_broker.get_overseas_pending_orders(exchange_code)
        except Exception as exc:
            logger.warning(
                "Failed to fetch pending orders for %s: %s", exchange_code, exc
            )
            continue

        for order in orders:
            try:
                stock_code = order.get("pdno", "")
                odno = order.get("odno", "")
                sll_buy = order.get("sll_buy_dvsn_cd", "")  # "01"=SELL, "02"=BUY
                nccs_qty = int(order.get("nccs_qty", "0") or "0")
                order_exchange = order.get("ovrs_excg_cd") or exchange_code
                key = f"{order_exchange}:{stock_code}"

                if not stock_code or not odno or nccs_qty <= 0:
                    continue

                # Cancel the pending order first regardless of direction.
                cancel_result = await overseas_broker.cancel_overseas_order(
                    exchange_code=order_exchange,
                    stock_code=stock_code,
                    odno=odno,
                    qty=nccs_qty,
                )
                if cancel_result.get("rt_cd") != "0":
                    logger.warning(
                        "Cancel failed for %s %s: rt_cd=%s msg=%s",
                        order_exchange,
                        stock_code,
                        cancel_result.get("rt_cd"),
                        cancel_result.get("msg1"),
                    )
                    continue

                if sll_buy == "02":
                    # BUY pending → cancelled; set cooldown to avoid immediate re-buy.
                    if buy_cooldown is not None:
                        buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                    try:
                        await telegram.notify_unfilled_order(
                            stock_code=stock_code,
                            market=order_exchange,
                            action="BUY",
                            quantity=nccs_qty,
                            outcome="cancelled",
                        )
                    except Exception as notify_exc:
                        logger.warning("notify_unfilled_order failed: %s", notify_exc)

                elif sll_buy == "01":
                    # SELL pending — attempt one resubmit at a wider spread.
                    if sell_resubmit_counts.get(key, 0) >= 1:
                        # Already resubmitted once — only cancel (already done above).
                        logger.warning(
                            "SELL %s %s already resubmitted once — no further resubmit",
                            order_exchange,
                            stock_code,
                        )
                        try:
                            await telegram.notify_unfilled_order(
                                stock_code=stock_code,
                                market=order_exchange,
                                action="SELL",
                                quantity=nccs_qty,
                                outcome="cancelled",
                            )
                        except Exception as notify_exc:
                            logger.warning(
                                "notify_unfilled_order failed: %s", notify_exc
                            )
                    else:
                        # First unfilled SELL → resubmit at last * 0.996 (-0.4%).
                        try:
                            price_data = await overseas_broker.get_overseas_price(
                                order_exchange, stock_code
                            )
                            last_price = float(
                                price_data.get("output", {}).get("last", "0") or "0"
                            )
                            if last_price <= 0:
                                raise ValueError(
                                    f"Invalid price ({last_price}) for {stock_code}"
                                )
                            new_price = round(last_price * 0.996, 4)
                            market_info = next(
                                (
                                    m for m in MARKETS.values()
                                    if m.exchange_code == order_exchange and not m.is_domestic
                                ),
                                None,
                            )
                            if market_info is not None:
                                validate_order_policy(
                                    market=market_info,
                                    order_type="SELL",
                                    price=float(new_price),
                                )
                            await overseas_broker.send_overseas_order(
                                exchange_code=order_exchange,
                                stock_code=stock_code,
                                order_type="SELL",
                                quantity=nccs_qty,
                                price=new_price,
                            )
                            sell_resubmit_counts[key] = (
                                sell_resubmit_counts.get(key, 0) + 1
                            )
                            try:
                                await telegram.notify_unfilled_order(
                                    stock_code=stock_code,
                                    market=order_exchange,
                                    action="SELL",
                                    quantity=nccs_qty,
                                    outcome="resubmitted",
                                    new_price=new_price,
                                )
                            except Exception as notify_exc:
                                logger.warning(
                                    "notify_unfilled_order failed: %s", notify_exc
                                )
                        except Exception as exc:
                            logger.error(
                                "SELL resubmit failed for %s %s: %s",
                                order_exchange,
                                stock_code,
                                exc,
                            )

            except Exception as exc:
                logger.error(
                    "Error handling pending order for %s: %s",
                    order.get("pdno", "?"),
                    exc,
                )


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
    # Get currently open markets
    open_markets = get_open_markets(settings.enabled_market_list)

    if not open_markets:
        logger.info("No markets open for this session")
        return daily_start_eval

    logger.info("Starting daily trading session for %d markets", len(open_markets))

    # BUY cooldown: prevents retrying stocks rejected for insufficient balance
    daily_buy_cooldown: dict[str, float] = {}  # "{market_code}:{stock_code}" -> expiry timestamp

    # Tracks SELL resubmission attempts per "{exchange_code}:{stock_code}" (max 1 per session).
    sell_resubmit_counts: dict[str, int] = {}

    # Process each open market
    for market in open_markets:
        await process_blackout_recovery_orders(
            broker=broker,
            overseas_broker=overseas_broker,
            db_conn=db_conn,
        )
        # Use market-local date for playbook keying
        market_today = datetime.now(market.timezone).date()

        # Check and handle domestic pending (unfilled) limit orders before new decisions.
        if market.is_domestic:
            try:
                await handle_domestic_pending_orders(
                    broker,
                    telegram,
                    settings,
                    sell_resubmit_counts,
                    daily_buy_cooldown,
                )
            except Exception as exc:
                logger.warning("Domestic pending order check failed: %s", exc)

        # Check and handle overseas pending (unfilled) limit orders before new decisions.
        if not market.is_domestic:
            try:
                await handle_overseas_pending_orders(
                    overseas_broker,
                    telegram,
                    settings,
                    sell_resubmit_counts,
                    daily_buy_cooldown,
                )
            except Exception as exc:
                logger.warning("Pending order check failed: %s", exc)

        # Dynamic stock discovery via scanner (no static watchlists)
        candidates_list: list[ScanCandidate] = []
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
        try:
            candidates_list = (
                await smart_scanner.scan(
                    market=market,
                    fallback_stocks=fallback_stocks,
                )
                if smart_scanner
                else []
            )
        except Exception as exc:
            logger.error("Smart Scanner failed for %s: %s", market.name, exc)

        if not candidates_list:
            logger.info("No scanner candidates for market %s — skipping", market.code)
            continue

        watchlist = [c.stock_code for c in candidates_list]
        candidate_map = {c.stock_code: c for c in candidates_list}
        logger.info("Processing market: %s (%d stocks)", market.name, len(watchlist))

        # Generate or load playbook (1 Gemini API call per market per day)
        playbook = playbook_store.load(market_today, market.code)
        if playbook is None:
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
                    )
                except Exception as exc:
                    logger.warning("Playbook notification failed: %s", exc)
                logger.info(
                    "Generated playbook for %s: %d stocks, %d scenarios",
                    market.code, playbook.stock_count, playbook.scenario_count,
                )
            except Exception as exc:
                logger.error("Playbook generation failed for %s: %s", market.code, exc)
                try:
                    await telegram.notify_playbook_failed(
                        market=market.code, reason=str(exc)[:200],
                    )
                except Exception as notify_exc:
                    logger.warning("Playbook failed notification error: %s", notify_exc)
                playbook = PreMarketPlanner._empty_playbook(market_today, market.code)

        # Collect market data for all stocks from scanner
        stocks_data = []
        for stock_code in watchlist:
            try:
                if market.is_domestic:
                    current_price, price_change_pct, foreigner_net = (
                        await _retry_connection(
                            broker.get_current_price,
                            stock_code,
                            label=stock_code,
                        )
                    )
                else:
                    price_data = await _retry_connection(
                        overseas_broker.get_overseas_price,
                        market.exchange_code,
                        stock_code,
                        label=f"{stock_code}@{market.exchange_code}",
                    )
                    current_price = safe_float(
                        price_data.get("output", {}).get("last", "0")
                    )
                    # Fallback: if price API returns 0, use scanner candidate price
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
                    price_change_pct = safe_float(
                        price_data.get("output", {}).get("rate", "0")
                    )
                    # Fall back to scanner candidate price if API returns 0.
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
                # Enrich with scanner metrics
                cand = candidate_map.get(stock_code)
                if cand:
                    stock_data["rsi"] = cand.rsi
                    stock_data["volume_ratio"] = cand.volume_ratio
                stocks_data.append(stock_data)
            except Exception as exc:
                logger.error("Failed to fetch data for %s: %s", stock_code, exc)
                continue

        if not stocks_data:
            logger.warning("No valid stock data for market %s", market.code)
            continue

        # Get balance data once for the market (read-only — safe to retry)
        try:
            if market.is_domestic:
                balance_data = await _retry_connection(
                    broker.get_balance, label=f"balance:{market.code}"
                )
            else:
                balance_data = await _retry_connection(
                    overseas_broker.get_overseas_balance,
                    market.exchange_code,
                    label=f"overseas_balance:{market.exchange_code}",
                )
        except ConnectionError as exc:
            logger.error(
                "Balance fetch failed for market %s after all retries — skipping market: %s",
                market.code,
                exc,
            )
            continue

        if market.is_domestic:
            output2 = balance_data.get("output2", [{}])
            total_eval = safe_float(
                output2[0].get("tot_evlu_amt", "0")
            ) if output2 else 0
            total_cash = safe_float(
                output2[0].get("dnca_tot_amt", "0")
            ) if output2 else 0
            purchase_total = safe_float(
                output2[0].get("pchs_amt_smtl_amt", "0")
            ) if output2 else 0
        else:
            output2 = balance_data.get("output2", [{}])
            if isinstance(output2, list) and output2:
                balance_info = output2[0]
            elif isinstance(output2, dict):
                balance_info = output2
            else:
                balance_info = {}

            total_eval = safe_float(balance_info.get("frcr_evlu_tota", "0") or "0")
            purchase_total = safe_float(
                balance_info.get("frcr_buy_amt_smtl", "0") or "0"
            )

            # Fetch available foreign currency cash via inquire-psamount (TTTS3007R/VTTS3007R).
            # TTTS3012R output2 does not include a cash/deposit field — frcr_dncl_amt_2 does not exist.
            # Use the first stock with a valid price as the reference for the buying power query.
            # Source: 한국투자증권 오픈API 전체문서 (20260221) — '해외주식 매수가능금액조회' 시트
            total_cash = 0.0
            ref_stock = next(
                (s for s in stocks_data if s.get("current_price", 0) > 0), None
            )
            if ref_stock:
                try:
                    ps_data = await overseas_broker.get_overseas_buying_power(
                        market.exchange_code,
                        ref_stock["stock_code"],
                        ref_stock["current_price"],
                    )
                    total_cash = safe_float(
                        ps_data.get("output", {}).get("ovrs_ord_psbl_amt", "0") or "0"
                    )
                except ConnectionError as exc:
                    logger.warning(
                        "Could not fetch overseas buying power for %s: %s",
                        market.exchange_code,
                        exc,
                    )

            # Paper mode fallback: VTS overseas balance API often fails for many accounts.
            # Only activate in paper mode — live mode must use real balance from KIS.
            if (
                total_cash <= 0
                and settings.MODE == "paper"
                and settings.PAPER_OVERSEAS_CASH > 0
            ):
                total_cash = settings.PAPER_OVERSEAS_CASH

        # Capture the day's opening portfolio value on the first market processed
        # in this session.  Used to compute intra-day P&L for the CB instead of
        # the cumulative purchase_total which spans the entire account history.
        if daily_start_eval <= 0 and total_eval > 0:
            daily_start_eval = total_eval
            logger.info(
                "Daily CB baseline set: total_eval=%.2f (first balance of the day)",
                daily_start_eval,
            )

        # Daily P&L: compare current eval vs start-of-day eval.
        # Falls back to purchase_total if daily_start_eval is unavailable (e.g. paper
        # mode where balance API returns 0 for all values).
        if daily_start_eval > 0:
            pnl_pct = (total_eval - daily_start_eval) / daily_start_eval * 100
        else:
            pnl_pct = (
                ((total_eval - purchase_total) / purchase_total * 100)
                if purchase_total > 0
                else 0.0
            )
        portfolio_data = {
            "portfolio_pnl_pct": pnl_pct,
            "total_cash": total_cash,
            "total_eval": total_eval,
        }

        # Evaluate scenarios for each stock (local, no API calls)
        logger.info(
            "Evaluating %d stocks against playbook for %s",
            len(stocks_data), market.name,
        )
        for stock_data in stocks_data:
            stock_code = stock_data["stock_code"]
            match = scenario_engine.evaluate(
                playbook, stock_code, stock_data, portfolio_data,
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

            # BUY 중복 방지: 브로커 잔고 기반 (미체결 SELL 리밋 주문 보호)
            if decision.action == "BUY":
                daily_existing = get_open_position(db_conn, stock_code, market.code)
                if not daily_existing:
                    # SELL 지정가 접수 후 미체결 시 DB는 종료로 기록되나 브로커는 여전히 보유 중.
                    # 국내/해외 모두 라이브 브로커 잔고를 authoritative source로 사용.
                    broker_qty = _extract_held_qty_from_balance(
                        balance_data, stock_code, is_domestic=market.is_domestic
                    )
                    if broker_qty > 0:
                        daily_existing = {"price": 0.0, "quantity": broker_qty}
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
                "scenario_match": match.match_details,
            }
            input_data = {
                "current_price": stock_data["current_price"],
                "foreigner_net": stock_data["foreigner_net"],
                "total_eval": total_eval,
                "total_cash": total_cash,
                "pnl_pct": pnl_pct,
            }

            decision_id = decision_logger.log_decision(
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
            quantity = 0
            trade_price = stock_data["current_price"]
            trade_pnl = 0.0
            order_succeeded = True
            if decision.action in ("BUY", "SELL"):
                if KILL_SWITCH.new_orders_blocked:
                    logger.critical(
                        "KillSwitch block active: skip %s order for %s (%s)",
                        decision.action,
                        stock_code,
                        market.name,
                    )
                    continue

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
                    continue
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
                        "Skip BUY %s (%s): FX buffer guard (remaining=%.2f, required=%.2f, cash=%.2f, order=%.2f)",
                        stock_code,
                        market.name,
                        remaining_cash,
                        required_buffer,
                        total_cash,
                        order_amount,
                    )
                    continue

                # Check BUY cooldown (insufficient balance)
                if decision.action == "BUY":
                    daily_cooldown_key = f"{market.code}:{stock_code}"
                    daily_cooldown_until = daily_buy_cooldown.get(daily_cooldown_key, 0.0)
                    now = asyncio.get_event_loop().time()
                    if now < daily_cooldown_until:
                        remaining = int(daily_cooldown_until - now)
                        logger.info(
                            "Skip BUY %s (%s): insufficient-balance cooldown active (%ds remaining)",
                            stock_code,
                            market.name,
                            remaining,
                        )
                        continue

                # Risk check
                # SELL orders do not consume cash (they receive it), so fat-finger
                # check is skipped for SELLs — only circuit breaker applies.
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
                    continue  # Skip this order
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

                # Send order
                order_succeeded = True
                try:
                    if market.is_domestic:
                        # Use limit orders (지정가) for domestic stocks.
                        # KRX tick rounding applied via kr_round_down.
                        if decision.action == "BUY":
                            order_price = kr_round_down(
                                stock_data["current_price"] * 1.002
                            )
                        else:
                            order_price = kr_round_down(
                                stock_data["current_price"] * 0.998
                            )
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
                            continue
                        if _maybe_queue_order_intent(
                            market=market,
                            stock_code=stock_code,
                            order_type=decision.action,
                            quantity=quantity,
                            price=float(order_price),
                            source="run_daily_session",
                        ):
                            continue
                        result = await broker.send_order(
                            stock_code=stock_code,
                            order_type=decision.action,
                            quantity=quantity,
                            price=order_price,
                        )
                    else:
                        # KIS VTS only accepts limit orders; use 0.5% premium for BUY
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
                            continue
                        if _maybe_queue_order_intent(
                            market=market,
                            stock_code=stock_code,
                            order_type=decision.action,
                            quantity=quantity,
                            price=float(order_price),
                            source="run_daily_session",
                        ):
                            continue
                        result = await overseas_broker.send_overseas_order(
                            exchange_code=market.exchange_code,
                            stock_code=stock_code,
                            order_type=decision.action,
                            quantity=quantity,
                            price=order_price,  # limit order
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

                    # Notify trade execution (only on success)
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
                    logger.error(
                        "Order execution failed for %s: %s", stock_code, exc
                    )
                    continue

                if decision.action == "SELL" and order_succeeded:
                    buy_trade = get_latest_buy_trade(db_conn, stock_code, market.code)
                    if buy_trade and buy_trade.get("price") is not None:
                        buy_price = float(buy_trade["price"])
                        buy_qty = int(buy_trade.get("quantity") or 1)
                        trade_pnl = (trade_price - buy_price) * buy_qty
                        decision_logger.update_outcome(
                            decision_id=buy_trade["decision_id"],
                            pnl=trade_pnl,
                            accuracy=1 if trade_pnl > 0 else 0,
                        )

            # Log trade (skip if order was rejected by API)
            if decision.action in ("BUY", "SELL") and not order_succeeded:
                continue
            log_trade(
                conn=db_conn,
                stock_code=stock_code,
                action=decision.action,
                confidence=decision.confidence,
                rationale=decision.rationale,
                quantity=quantity,
                price=trade_price,
                pnl=trade_pnl,
                market=market.code,
                exchange_code=market.exchange_code,
                decision_id=decision_id,
                mode=settings.MODE,
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
    scheduler: ContextScheduler, now: datetime | None = None,
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
    global BLACKOUT_ORDER_MANAGER
    BLACKOUT_ORDER_MANAGER = BlackoutOrderManager(
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
            "<b>▶️ Trading Resumed</b>\n\n"
            "Trading operations have been restarted."
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
            await telegram.send_message(
                "<b>⚠️ Error</b>\n\nFailed to retrieve trading status."
            )

    async def handle_positions() -> None:
        """Handle /positions command - show account summary."""
        try:
            # Get account balance
            balance = await broker.get_balance()
            output2 = balance.get("output2", [{}])

            if not output2:
                await telegram.send_message(
                    "<b>💼 Account Summary</b>\n\n"
                    "No balance information available."
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
            await telegram.send_message(
                "<b>⚠️ Error</b>\n\nFailed to retrieve positions."
            )

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
            await telegram.send_message(
                "<b>⚠️ Error</b>\n\nFailed to generate daily report."
            )

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
            await telegram.send_message(
                "<b>⚠️ Error</b>\n\nFailed to retrieve scenarios."
            )

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
                await telegram.send_message(
                    "<b>📝 Recent Reviews</b>\n\nNo scorecards available."
                )
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
            await telegram.send_message(
                "<b>⚠️ Error</b>\n\nFailed to retrieve reviews."
            )

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
                    f"❌ 알 수 없는 키: <code>{key}</code>\n"
                    f"유효한 키: {valid}"
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
            label = f"전체 알림" if key == "all" else f"<code>{key}</code> 알림"
            state = "켜짐" if value else "꺼짐"
            await telegram.send_message(f"{icon} {label} → {state}")
            logger.info("Notification filter changed via Telegram: %s=%s", key, value)
        else:
            valid = ", ".join(list(telegram.filter_status().keys()) + ["all"])
            await telegram.send_message(
                f"❌ 알 수 없는 키: <code>{key}</code>\n"
                f"유효한 키: {valid}"
            )

    async def handle_dashboard() -> None:
        """Handle /dashboard command - show dashboard URL if enabled."""
        if not settings.DASHBOARD_ENABLED:
            await telegram.send_message(
                "<b>🖥️ Dashboard</b>\n\nDashboard is not enabled."
            )
            return

        url = f"http://{settings.DASHBOARD_HOST}:{settings.DASHBOARD_PORT}"
        await telegram.send_message(
            "<b>🖥️ Dashboard</b>\n\n"
            f"<b>URL:</b> {url}"
        )

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

    # Tracks SELL resubmission attempts per "{exchange_code}:{stock_code}" (max 1 until restart).
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

    # Sync broker positions → DB to prevent double-buy on restart
    try:
        await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)
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

            while not shutdown.is_set():
                # Wait for trading to be unpaused
                await pause_trading.wait()
                _run_context_scheduler(context_scheduler, now=datetime.now(UTC))

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
                            _market_states[market_code] = False
                            # Clear playbook for closed market (new one generated next open)
                            playbooks.pop(market_code, None)

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

                    await process_blackout_recovery_orders(
                        broker=broker,
                        overseas_broker=overseas_broker,
                        db_conn=db_conn,
                    )

                    # Notify market open if it just opened
                    if not _market_states.get(market.code, False):
                        try:
                            await telegram.notify_market_open(market.name)
                        except Exception as exc:
                            logger.warning("Market open notification failed: %s", exc)
                        _market_states[market.code] = True

                    # Check and handle domestic pending (unfilled) limit orders.
                    if market.is_domestic:
                        try:
                            await handle_domestic_pending_orders(
                                broker,
                                telegram,
                                settings,
                                sell_resubmit_counts,
                                buy_cooldown,
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
                            )
                        except Exception as exc:
                            logger.warning("Pending order check failed: %s", exc)

                    # Smart Scanner: dynamic stock discovery (no static watchlists)
                    now_timestamp = asyncio.get_event_loop().time()
                    last_scan = last_scan_time.get(market.code, 0.0)
                    rescan_interval = settings.RESCAN_INTERVAL_SECONDS
                    if now_timestamp - last_scan >= rescan_interval:
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
                                # Use scanner results directly as trading candidates
                                active_stocks[market.code] = smart_scanner.get_stock_codes(
                                    candidates
                                )

                                # Store candidates per market for selection context logging
                                scan_candidates[market.code] = {
                                    c.stock_code: c for c in candidates
                                }

                                logger.info(
                                    "Smart Scanner: Found %d candidates for %s: %s",
                                    len(candidates),
                                    market.name,
                                    [f"{c.stock_code}(RSI={c.rsi:.0f})" for c in candidates],
                                )

                                # Get market-local date for playbook keying
                                market_today = datetime.now(
                                    market.timezone
                                ).date()

                                # Load or generate playbook (1 Gemini call per market per day)
                                if market.code not in playbooks:
                                    # Try DB first (survives process restart)
                                    stored_pb = playbook_store.load(market_today, market.code)
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
                                        try:
                                            pb = await pre_market_planner.generate_playbook(
                                                market=market.code,
                                                candidates=candidates,
                                                today=market_today,
                                            )
                                            playbook_store.save(pb)
                                            playbooks[market.code] = pb
                                            try:
                                                await telegram.notify_playbook_generated(
                                                    market=market.code,
                                                    stock_count=pb.stock_count,
                                                    scenario_count=pb.scenario_count,
                                                    token_count=pb.token_count,
                                                )
                                            except Exception as exc:
                                                logger.warning(
                                                    "Playbook notification failed: %s", exc
                                                )
                                        except Exception as exc:
                                            logger.error(
                                                "Playbook generation failed for %s: %s",
                                                market.code, exc,
                                            )
                                            try:
                                                await telegram.notify_playbook_failed(
                                                    market=market.code,
                                                    reason=str(exc)[:200],
                                                )
                                            except Exception:
                                                pass
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

                    # Get active stocks from scanner (dynamic, no static fallback).
                    # Also include currently-held positions so stop-loss /
                    # take-profit can fire even when a holding drops off the
                    # scanner.  Broker balance is the source of truth here —
                    # unlike the local DB it reflects actual fills and any
                    # manual trades done outside the bot.
                    scanner_codes = active_stocks.get(market.code, [])
                    try:
                        if market.is_domestic:
                            held_balance = await broker.get_balance()
                        else:
                            held_balance = await overseas_broker.get_overseas_balance(
                                market.exchange_code
                            )
                        held_codes = _extract_held_codes_from_balance(
                            held_balance, is_domestic=market.is_domestic
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to fetch holdings for %s: %s — skipping holdings merge",
                            market.name, exc,
                        )
                        held_codes = []

                    stock_codes = list(dict.fromkeys(scanner_codes + held_codes))
                    extra_held = [c for c in held_codes if c not in set(scanner_codes)]
                    if extra_held:
                        logger.info(
                            "Holdings added to loop for %s (not in scanner): %s",
                            market.name, extra_held,
                        )

                    if not stock_codes:
                        logger.debug("No active stocks for market %s", market.code)
                        continue

                    logger.info("Processing market: %s (%d stocks)", market.name, len(stock_codes))

                    # Process each stock from scanner results
                    for stock_code in stock_codes:
                        if shutdown.is_set():
                            break

                        # Get playbook for this market
                        market_playbook = playbooks.get(
                            market.code,
                            PreMarketPlanner._empty_playbook(
                                datetime.now(market.timezone).date(), market.code
                            ),
                        )

                        # Retry logic for connection errors
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
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Enable FastAPI dashboard server in background thread",
    )
    args = parser.parse_args()

    setup_logging()
    settings = Settings(MODE=args.mode)  # type: ignore[call-arg]
    settings = _apply_dashboard_flag(settings, args.dashboard)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
