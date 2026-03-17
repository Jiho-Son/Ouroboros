"""Order decision helper functions.

Pure functions for order quantity determination, buy suppression,
FX buffer checks, and overnight exit logic.  Extracted from ``src/main.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.analysis.smart_scanner import ScanCandidate
from src.broker.balance_utils import _extract_held_qty_from_balance
from src.config import Settings
from src.core.order_policy import get_session_info
from src.db import get_open_position
from src.markets.schedule import MarketInfo

logger = logging.getLogger(__name__)

# Close-window session IDs used by _should_force_exit_for_overnight.
# Copied from src/main.py to avoid circular import.
_SESSION_CLOSE_WINDOWS = {"NXT_AFTER", "US_AFTER"}


def _resolve_sell_qty_for_pnl(*, sell_qty: int | None, buy_qty: int | None) -> int:
    """Choose quantity basis for SELL outcome PnL with safe fallback."""
    resolved_sell = int(sell_qty or 0)
    if resolved_sell > 0:
        return resolved_sell
    return max(0, int(buy_qty or 0))


def _resolve_buy_suppression_position(
    *,
    db_conn: Any,
    balance_data: dict[str, Any],
    stock_code: str,
    market: MarketInfo,
) -> dict[str, float | int] | None:
    """Resolve duplicate-BUY suppression position with market-specific source priority.

    Domestic: trust live broker balance first because DB may contain stale accepted BUY
    records (order accepted but not filled).
    Overseas: preserve existing behavior and trust DB open-position state first, then
    fallback to broker holdings if available.
    """
    broker_qty = _extract_held_qty_from_balance(
        balance_data, stock_code, is_domestic=market.is_domestic
    )
    existing_position = get_open_position(db_conn, stock_code, market.code)

    # Domestic duplicate-BUY suppression is broker-authoritative.
    if market.is_domestic:
        if broker_qty <= 0:
            return None
        entry_price = 0.0
        if existing_position and existing_position.get("price") is not None:
            entry_price = float(existing_position["price"])
        return {"price": entry_price, "quantity": broker_qty}

    # Overseas preserves DB-first suppression semantics.
    if existing_position:
        return {
            "price": float(existing_position.get("price") or 0.0),
            "quantity": int(existing_position.get("quantity") or 0),
        }
    if broker_qty > 0:
        return {"price": 0.0, "quantity": broker_qty}
    return None


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
    if (
        market.is_domestic
        or not market.code.startswith("US")
        or action != "BUY"
        or settings is None
    ):
        return False, total_cash - order_amount, 0.0
    remaining = total_cash - order_amount
    # Lazy import to avoid circular dependency (will move to session_risk in Task 3)
    from src.core.session_risk import _resolve_market_setting

    required = float(
        _resolve_market_setting(
            market=market,
            settings=settings,
            key="USD_BUFFER_MIN",
            default=1000.0,
        )
    )
    return remaining < required, remaining, required


def _should_block_buy_chasing_session_high(
    *,
    market: MarketInfo,
    action: str,
    current_price: float,
    session_high_price: float,
    price_change_pct: float,
    settings: Settings | None,
) -> tuple[bool, float, float, float]:
    """Block BUY when price is already extended and pinned near the session high."""
    if action != "BUY" or settings is None:
        return False, 0.0, 0.0, 0.0
    if current_price <= 0 or session_high_price <= 0 or current_price > session_high_price:
        return False, 0.0, 0.0, 0.0

    from src.core.session_risk import _resolve_market_setting

    min_gain_pct = float(
        _resolve_market_setting(
            market=market,
            settings=settings,
            key="BUY_CHASE_MIN_INTRADAY_GAIN_PCT",
            default=4.0,
        )
    )
    max_pullback_pct = float(
        _resolve_market_setting(
            market=market,
            settings=settings,
            key="BUY_CHASE_MAX_PULLBACK_FROM_HIGH_PCT",
            default=0.5,
        )
    )
    pullback_from_high_pct = ((session_high_price - current_price) / session_high_price) * 100.0
    blocked = price_change_pct >= min_gain_pct and pullback_from_high_pct <= max_pullback_pct
    return blocked, pullback_from_high_pct, min_gain_pct, max_pullback_pct


def _should_block_buy_above_recent_sell(
    *,
    market: MarketInfo,
    action: str,
    current_price: float,
    last_sell_price: float,
    last_sell_timestamp: str | None,
    settings: Settings | None,
    now: datetime | None = None,
) -> tuple[bool, int, int]:
    """Block BUY when it would re-enter above the latest SELL price too soon."""
    if action != "BUY" or current_price <= 0 or last_sell_price <= 0 or not last_sell_timestamp:
        return False, 0, 0

    try:
        last_sell_at = datetime.fromisoformat(last_sell_timestamp)
    except ValueError:
        return False, 0, 0
    if last_sell_at.tzinfo is None:
        last_sell_at = last_sell_at.replace(tzinfo=UTC)

    evaluation_time = now or datetime.now(UTC)
    elapsed_seconds = max(0, int((evaluation_time - last_sell_at).total_seconds()))

    from src.core.session_risk import _resolve_market_setting

    window_seconds = max(
        1,
        int(
            _resolve_market_setting(
                market=market,
                settings=settings,
                key="SELL_REENTRY_PRICE_GUARD_SECONDS",
                default=120,
            )
        ),
    )
    if elapsed_seconds >= window_seconds:
        return False, elapsed_seconds, window_seconds

    return current_price > last_sell_price, elapsed_seconds, window_seconds


def _should_force_exit_for_overnight(
    *,
    market: MarketInfo,
    settings: Settings | None,
) -> bool:
    session_id = get_session_info(market).session_id
    if session_id not in _SESSION_CLOSE_WINDOWS:
        return False
    from src.core.kill_switch_runtime import KILL_SWITCH

    if KILL_SWITCH.new_orders_blocked:
        return True
    if settings is None:
        return False
    # Lazy import to avoid circular dependency (will move to session_risk in Task 3)
    from src.core.session_risk import _resolve_market_setting

    overnight_enabled = _resolve_market_setting(
        market=market,
        settings=settings,
        key="OVERNIGHT_EXCEPTION_ENABLED",
        default=True,
    )
    return not bool(overnight_enabled)


def _resolve_domestic_quote_market_div_code(session_id: str) -> str:
    """Resolve domestic quote market code from current KR session."""
    return "NX" if session_id in {"NXT_PRE", "NXT_AFTER"} else "J"
