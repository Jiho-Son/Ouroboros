"""Blackout-window order queueing and recovery logic.

Extracted from src/main.py (Task 7).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker
from src.config import Settings
from src.core.blackout_manager import BlackoutOrderManager, QueuedOrderIntent
from src.core.order_policy import OrderPolicyRejected, validate_order_policy
from src.core.session_risk import _resolve_market_setting
from src.db import get_open_position, log_trade
from src.markets.schedule import MARKETS, MarketInfo

logger = logging.getLogger(__name__)

BLACKOUT_ORDER_MANAGER = BlackoutOrderManager(
    enabled=False,
    windows=[],
    max_queue_size=500,
)


def _safe_float(value: str | float | None, default: float = 0.0) -> float:
    """Convert to float, handling empty strings and None."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _build_queued_order_intent(
    *,
    market: MarketInfo,
    session_id: str,
    stock_code: str,
    order_type: str,
    quantity: int,
    price: float,
    source: str,
) -> QueuedOrderIntent:
    return QueuedOrderIntent(
        market_code=market.code,
        exchange_code=market.exchange_code,
        session_id=session_id,
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
    session_id: str,
    stock_code: str,
    order_type: str,
    quantity: int,
    price: float,
    source: str,
) -> bool:
    if not BLACKOUT_ORDER_MANAGER.in_blackout():
        return False

    before_overflow_drops = BLACKOUT_ORDER_MANAGER.overflow_drop_count
    queued = BLACKOUT_ORDER_MANAGER.enqueue(
        _build_queued_order_intent(
            market=market,
            session_id=session_id,
            stock_code=stock_code,
            order_type=order_type,
            quantity=quantity,
            price=price,
            source=source,
        )
    )
    if queued:
        after_overflow_drops = BLACKOUT_ORDER_MANAGER.overflow_drop_count
        logger.warning(
            (
                "Blackout active: queued order intent %s %s (%s) "
                "qty=%d price=%.4f source=%s pending=%d"
            ),
            order_type,
            stock_code,
            market.code,
            quantity,
            price,
            source,
            BLACKOUT_ORDER_MANAGER.pending_count,
        )
        if after_overflow_drops > before_overflow_drops:
            logger.error(
                (
                    "Blackout queue overflow policy applied: evicted oldest intent "
                    "to keep latest %s %s (%s) source=%s pending=%d total_evicted=%d"
                ),
                order_type,
                stock_code,
                market.code,
                source,
                BLACKOUT_ORDER_MANAGER.pending_count,
                after_overflow_drops,
            )
    else:
        logger.error(
            "Blackout queue unavailable: could not queue order intent %s %s (%s) qty=%d source=%s",
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
    settings: Settings | None = None,
) -> None:
    # Lazy import to avoid circular dependency (stays in main.py)
    from src.main import _retry_connection

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
            revalidation_enabled = bool(
                _resolve_market_setting(
                    market=market,
                    settings=settings,
                    key="BLACKOUT_RECOVERY_PRICE_REVALIDATION_ENABLED",
                    default=True,
                )
            )
            if revalidation_enabled:
                if market.is_domestic:
                    current_price, _, _ = await _retry_connection(
                        broker.get_current_price,
                        intent.stock_code,
                        label=f"recovery_price:{market.code}:{intent.stock_code}",
                    )
                else:
                    price_data = await _retry_connection(
                        overseas_broker.get_overseas_price,
                        market.exchange_code,
                        intent.stock_code,
                        label=f"recovery_price:{market.code}:{intent.stock_code}",
                    )
                    current_price = _safe_float(price_data.get("output", {}).get("last"), 0.0)

                queued_price = float(intent.price)
                max_drift_pct = float(
                    _resolve_market_setting(
                        market=market,
                        settings=settings,
                        key="BLACKOUT_RECOVERY_MAX_PRICE_DRIFT_PCT",
                        default=5.0,
                    )
                )
                if queued_price <= 0 or current_price <= 0:
                    logger.info(
                        (
                            "Drop queued intent by price revalidation (invalid price): "
                            "%s %s (%s) queued=%.4f current=%.4f"
                        ),
                        intent.order_type,
                        intent.stock_code,
                        market.code,
                        queued_price,
                        current_price,
                    )
                    continue
                drift_pct = abs(current_price - queued_price) / queued_price * 100.0
                if drift_pct > max_drift_pct:
                    logger.info(
                        (
                            "Drop queued intent by price revalidation: %s %s (%s) "
                            "queued=%.4f current=%.4f drift=%.2f%% max=%.2f%%"
                        ),
                        intent.order_type,
                        intent.stock_code,
                        market.code,
                        queued_price,
                        current_price,
                        drift_pct,
                        max_drift_pct,
                    )
                    continue

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
                    session_id=intent.session_id,
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
                log_trade(
                    conn=db_conn,
                    stock_code=intent.stock_code,
                    action=intent.order_type,
                    confidence=0,
                    rationale=f"[blackout-recovery] {intent.source}",
                    quantity=intent.quantity,
                    price=float(intent.price),
                    pnl=0.0,
                    market=market.code,
                    exchange_code=market.exchange_code,
                    session_id=intent.session_id,
                )
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
