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
from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker
from src.config import Settings
from src.context.aggregator import ContextAggregator
from src.context.layer import ContextLayer
from src.context.scheduler import ContextScheduler
from src.context.store import ContextStore
from src.core.criticality import CriticalityAssessor
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
from src.markets.schedule import MarketInfo, get_next_market_open, get_open_markets
from src.notifications.telegram_client import NotificationFilter, TelegramClient, TelegramCommandHandler
from src.strategy.models import DayPlaybook
from src.strategy.playbook_store import PlaybookStore
from src.strategy.pre_market_planner import PreMarketPlanner
from src.strategy.scenario_engine import ScenarioEngine

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


TRADE_INTERVAL_SECONDS = 60
SCAN_INTERVAL_SECONDS = 60  # Scan markets every 60 seconds
MAX_CONNECTION_RETRIES = 3

# Daily trading mode constants (for Free tier API efficiency)
DAILY_TRADE_SESSIONS = 4  # Number of trading sessions per day
TRADE_SESSION_INTERVAL_HOURS = 6  # Hours between sessions


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


def _determine_order_quantity(
    *,
    action: str,
    current_price: float,
    total_cash: float,
    candidate: ScanCandidate | None,
    settings: Settings | None,
) -> int:
    """Determine order quantity using volatility-aware position sizing."""
    if action != "BUY":
        return 1
    if current_price <= 0 or total_cash <= 0:
        return 0

    if settings is None or not settings.POSITION_SIZING_ENABLED:
        return 1

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
) -> None:
    """Execute one trading cycle for a single stock."""
    cycle_start_time = asyncio.get_event_loop().time()

    # 1. Fetch market data
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
        total_cash = safe_float(balance_info.get("frcr_dncl_amt_2", "0") or "0")
        purchase_total = safe_float(balance_info.get("frcr_buy_amt_smtl", "0") or "0")

        # Paper mode fallback: VTS overseas balance API often fails for many accounts.
        if total_cash <= 0 and settings and settings.PAPER_OVERSEAS_CASH > 0:
            logger.debug(
                "Overseas cash balance is 0 for %s; using paper fallback %.2f USD",
                market.exchange_code,
                settings.PAPER_OVERSEAS_CASH,
            )
            total_cash = settings.PAPER_OVERSEAS_CASH

        current_price = safe_float(price_data.get("output", {}).get("last", "0"))
        # Fallback: if price API returns 0, use scanner candidate price
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
        price_change_pct = safe_float(price_data.get("output", {}).get("rate", "0"))

        # Price API may return 0/empty for certain VTS exchange codes.
        # Fall back to the scanner candidate's price so order sizing still works.
        if current_price <= 0:
            market_candidates_lookup = scan_candidates.get(market.code, {})
            cand_lookup = market_candidates_lookup.get(stock_code)
            if cand_lookup and cand_lookup.price > 0:
                current_price = cand_lookup.price
                logger.debug(
                    "Price API returned 0 for %s; using scanner price %.4f",
                    stock_code,
                    current_price,
                )

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

    if decision.action == "HOLD":
        open_position = get_open_position(db_conn, stock_code, market.code)
        if open_position:
            entry_price = safe_float(open_position.get("price"), 0.0)
            if entry_price > 0:
                loss_pct = (current_price - entry_price) / entry_price * 100
                stop_loss_threshold = -2.0
                take_profit_threshold = 3.0
                if stock_playbook and stock_playbook.scenarios:
                    stop_loss_threshold = stock_playbook.scenarios[0].stop_loss_pct
                    take_profit_threshold = stock_playbook.scenarios[0].take_profit_pct

                if loss_pct <= stop_loss_threshold:
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
                elif loss_pct >= take_profit_threshold:
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
        quantity = _determine_order_quantity(
            action=decision.action,
            current_price=current_price,
            total_cash=total_cash,
            candidate=candidate,
            settings=settings,
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
        order_succeeded = True
        if market.is_domestic:
            result = await broker.send_order(
                stock_code=stock_code,
                order_type=decision.action,
                quantity=quantity,
                price=0,  # market order
            )
        else:
            # For overseas orders:
            # - KIS VTS only accepts limit orders (지정가만 가능)
            # - BUY: use 0.5% premium over last price to improve fill probability
            #   (ask price is typically slightly above last, and VTS won't fill below ask)
            # - SELL: use last price as the limit
            if decision.action == "BUY":
                order_price = round(current_price * 1.005, 4)
            else:
                order_price = current_price
            result = await overseas_broker.send_overseas_order(
                exchange_code=market.exchange_code,
                stock_code=stock_code,
                order_type=decision.action,
                quantity=quantity,
                price=order_price,  # limit order — KIS VTS rejects market orders
            )
            # Check if KIS rejected the order (rt_cd != "0")
            if result.get("rt_cd", "") != "0":
                order_succeeded = False
                logger.warning(
                    "Overseas order not accepted for %s: rt_cd=%s msg=%s",
                    stock_code,
                    result.get("rt_cd"),
                    result.get("msg1"),
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
) -> None:
    """Execute one daily trading session.

    V2 proactive strategy: 1 Gemini call for playbook generation,
    then local scenario evaluation per stock (0 API calls).
    """
    # Get currently open markets
    open_markets = get_open_markets(settings.enabled_market_list)

    if not open_markets:
        logger.info("No markets open for this session")
        return

    logger.info("Starting daily trading session for %d markets", len(open_markets))

    # Process each open market
    for market in open_markets:
        # Use market-local date for playbook keying
        market_today = datetime.now(market.timezone).date()

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
                logger.warning(
                    "No dynamic overseas symbol universe for %s; scanner cannot run",
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
                        await broker.get_current_price(stock_code)
                    )
                else:
                    price_data = await overseas_broker.get_overseas_price(
                        market.exchange_code, stock_code
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

        # Get balance data once for the market
        if market.is_domestic:
            balance_data = await broker.get_balance()
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
            purchase_total = safe_float(
                balance_info.get("frcr_buy_amt_smtl", "0") or "0"
            )
            # Paper mode fallback: VTS overseas balance API often fails for many accounts.
            if total_cash <= 0 and settings.PAPER_OVERSEAS_CASH > 0:
                total_cash = settings.PAPER_OVERSEAS_CASH

            # VTS overseas balance API often returns 0; use paper fallback.
            if total_cash <= 0 and settings.PAPER_OVERSEAS_CASH > 0:
                total_cash = settings.PAPER_OVERSEAS_CASH

        # Calculate daily P&L %
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
                quantity = _determine_order_quantity(
                    action=decision.action,
                    current_price=stock_data["current_price"],
                    total_cash=total_cash,
                    candidate=candidate_map.get(stock_code),
                    settings=settings,
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
                        logger.warning(
                            "Circuit breaker notification failed: %s", notify_exc
                        )
                    raise

                # Send order
                order_succeeded = True
                try:
                    if market.is_domestic:
                        result = await broker.send_order(
                            stock_code=stock_code,
                            order_type=decision.action,
                            quantity=quantity,
                            price=0,  # market order
                        )
                    else:
                        # KIS VTS only accepts limit orders; use 0.5% premium for BUY
                        if decision.action == "BUY":
                            order_price = round(stock_data["current_price"] * 1.005, 4)
                        else:
                            order_price = stock_data["current_price"]
                        result = await overseas_broker.send_overseas_order(
                            exchange_code=market.exchange_code,
                            stock_code=stock_code,
                            order_type=decision.action,
                            quantity=quantity,
                            price=order_price,  # limit order
                        )
                        if result.get("rt_cd", "") != "0":
                            order_succeeded = False
                            logger.warning(
                                "Overseas order not accepted for %s: rt_cd=%s msg=%s",
                                stock_code,
                                result.get("rt_cd"),
                                result.get("msg1"),
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
            )

    logger.info("Daily trading session completed")


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

    def _serve() -> None:
        try:
            import uvicorn

            from src.dashboard import create_dashboard_app

            app = create_dashboard_app(settings.DB_PATH)
            uvicorn.run(
                app,
                host=settings.DASHBOARD_HOST,
                port=settings.DASHBOARD_PORT,
                log_level="info",
            )
        except Exception as exc:
            logger.warning("Dashboard server failed to start: %s", exc)

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
                _run_context_scheduler(context_scheduler, now=datetime.now(UTC))

                try:
                    await run_daily_session(
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

                    # Notify market open if it just opened
                    if not _market_states.get(market.code, False):
                        try:
                            await telegram.notify_market_open(market.name)
                        except Exception as exc:
                            logger.warning("Market open notification failed: %s", exc)
                        _market_states[market.code] = True

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
                                    logger.warning(
                                        "No dynamic overseas symbol universe for %s;"
                                        " scanner cannot run",
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

                    # Get active stocks from scanner (dynamic, no static fallback)
                    stock_codes = active_stocks.get(market.code, [])
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
