"""ATR / volatility helper functions.

Functions for ATR computation, trade PnL decomposition, and RSI-based
downside probability estimation.  Extracted from ``src/main.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.analysis.volatility import VolatilityAnalyzer
from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker
from src.markets.schedule import MarketInfo

logger = logging.getLogger(__name__)

_VOLATILITY_ANALYZER = VolatilityAnalyzer()

# ---------------------------------------------------------------------------
# Local helpers (avoid circular imports with main.py)
# ---------------------------------------------------------------------------

MAX_CONNECTION_RETRIES = 3


def _safe_float(value: str | float | None, default: float = 0.0) -> float:
    """Convert to float, handling empty strings and None.

    Local copy of ``src.main.safe_float`` to avoid a circular import
    (main -> atr_helpers -> main).
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


async def _retry_connection(coro_factory: Any, *args: Any, label: str = "", **kwargs: Any) -> Any:
    """Call an async function retrying on ConnectionError with exponential backoff.

    Local copy of ``src.main._retry_connection`` to avoid a circular import.
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


# ---------------------------------------------------------------------------
# Extracted functions
# ---------------------------------------------------------------------------


def _split_trade_pnl_components(
    *,
    market: MarketInfo,
    trade_pnl: float,
    buy_price: float,
    sell_price: float,
    quantity: int,
    buy_fx_rate: float | None = None,
    sell_fx_rate: float | None = None,
) -> tuple[float, float]:
    """Split total trade pnl into strategy/fx components.

    For overseas symbols, use buy/sell FX rates when both are available.
    Otherwise preserve backward-compatible behaviour (all strategy pnl).
    """
    if trade_pnl == 0.0:
        return 0.0, 0.0
    if market.is_domestic:
        return trade_pnl, 0.0

    if (
        buy_fx_rate is not None
        and sell_fx_rate is not None
        and buy_fx_rate > 0
        and sell_fx_rate > 0
        and quantity > 0
        and buy_price > 0
        and sell_price > 0
    ):
        buy_notional = buy_price * quantity
        fx_return = (sell_fx_rate - buy_fx_rate) / buy_fx_rate
        fx_pnl = buy_notional * fx_return
        strategy_pnl = trade_pnl - fx_pnl
        return strategy_pnl, fx_pnl

    return trade_pnl, 0.0


def _normalize_trade_pnl_to_usd(
    *,
    market: MarketInfo,
    trade_pnl: float,
    settlement_fx_rate: float | None = None,
) -> float:
    """Normalize settled trade PnL to USD for active KR/US markets."""
    if trade_pnl == 0.0:
        return 0.0
    if not market.is_domestic:
        return trade_pnl
    if settlement_fx_rate is None or settlement_fx_rate <= 0:
        return trade_pnl
    return trade_pnl / settlement_fx_rate


def _estimate_pred_down_prob_from_rsi(rsi: float | str | None) -> float:
    """Estimate downside probability from RSI using a simple linear mapping."""
    if rsi is None:
        return 0.5
    rsi_value = max(0.0, min(100.0, _safe_float(rsi, 50.0)))
    return rsi_value / 100.0


async def _compute_kr_atr_value(
    *,
    broker: KISBroker,
    stock_code: str,
    period: int = 14,
) -> float:
    """Compute ATR(period) for KR stocks using daily OHLC."""
    days = max(period + 1, 30)
    try:
        daily_prices = await _retry_connection(
            broker.get_daily_prices,
            stock_code,
            days=days,
            label=f"daily_prices:{stock_code}",
        )
    except ConnectionError as exc:
        logger.warning("ATR source unavailable for %s: %s", stock_code, exc)
        return 0.0
    except Exception as exc:
        logger.warning("Unexpected ATR fetch failure for %s: %s", stock_code, exc)
        return 0.0

    if not isinstance(daily_prices, list):
        return 0.0

    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for row in daily_prices:
        if not isinstance(row, dict):
            continue
        high = _safe_float(row.get("high"), 0.0)
        low = _safe_float(row.get("low"), 0.0)
        close = _safe_float(row.get("close"), 0.0)
        if high <= 0 or low <= 0 or close <= 0:
            continue
        highs.append(high)
        lows.append(low)
        closes.append(close)

    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return 0.0
    return max(0.0, _VOLATILITY_ANALYZER.calculate_atr(highs, lows, closes, period=period))


async def _compute_overseas_atr_value(
    *,
    overseas_broker: OverseasBroker,
    exchange_code: str,
    stock_code: str,
    period: int = 14,
) -> float:
    """Compute ATR(period) for overseas stocks using daily OHLC."""
    days = max(period + 1, 30)
    try:
        daily_prices = await _retry_connection(
            overseas_broker.get_daily_prices,
            exchange_code,
            stock_code,
            days=days,
            label=f"overseas_daily_prices:{exchange_code}:{stock_code}",
        )
    except ConnectionError as exc:
        logger.warning(
            "Overseas ATR source unavailable for %s/%s: %s",
            exchange_code,
            stock_code,
            exc,
        )
        return 0.0
    except Exception as exc:
        logger.warning(
            "Unexpected overseas ATR fetch failure for %s/%s: %s",
            exchange_code,
            stock_code,
            exc,
        )
        return 0.0

    if not isinstance(daily_prices, list):
        return 0.0

    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for row in daily_prices:
        if not isinstance(row, dict):
            continue
        high = _safe_float(row.get("high"), 0.0)
        low = _safe_float(row.get("low"), 0.0)
        close = _safe_float(row.get("close"), 0.0)
        if high <= 0 or low <= 0 or close <= 0:
            continue
        highs.append(high)
        lows.append(low)
        closes.append(close)

    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return 0.0
    return max(0.0, _VOLATILITY_ANALYZER.calculate_atr(highs, lows, closes, period=period))
