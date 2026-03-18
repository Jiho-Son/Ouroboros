"""Handle unfilled (pending) domestic and overseas limit orders.

Extracted from ``src/main.py`` to reduce module size.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

from src.broker.balance_utils import (
    _extract_held_codes_from_balance,
    _extract_held_qty_from_balance,
)
from src.broker.kis_api import KISBroker, kr_round_down
from src.broker.orderbook_utils import extract_orderbook_top_levels
from src.broker.overseas import OverseasBroker
from src.config import Settings
from src.core.order_policy import (
    classify_session_id,
    validate_order_policy,
)
from src.markets.schedule import MARKETS
from src.notifications.telegram_client import TelegramClient

logger = logging.getLogger(__name__)

_BUY_COOLDOWN_SECONDS = 600  # 10-minute cooldown after insufficient-balance rejection
_BUY_RETRY_MULTIPLIER = 1.004
_SELL_RETRY_MULTIPLIER = 0.996

RollbackOpenPosition = Callable[..., None]


def _resolve_overseas_market_code(exchange_code: str) -> str:
    for market_code, market in MARKETS.items():
        if market.exchange_code == exchange_code and not market.is_domestic:
            return market_code
    logger.warning(
        "Unknown overseas exchange code %s for pending-order rollback; using fallback market key",
        exchange_code,
    )
    return f"US_{exchange_code}"


def _rollback_pending_position(
    *,
    rollback_open_position: RollbackOpenPosition | None,
    market_code: str,
    exchange_code: str,
    stock_code: str,
    action: str,
    quantity: int | None = None,
) -> None:
    if rollback_open_position is None:
        return
    kwargs: dict[str, Any] = {
        "market_code": market_code,
        "exchange_code": exchange_code,
        "stock_code": stock_code,
        "action": action,
    }
    if quantity is not None:
        kwargs["quantity"] = quantity
    rollback_open_position(**kwargs)


def _resolve_executable_gap_cap_pct(*, market_code: str, settings: Settings) -> float:
    """Resolve executable-quote gap cap, preferring market-specific overrides."""
    normalized = market_code.strip().upper()
    caps_by_market = settings.executable_quote_gap_caps_by_market
    if normalized in caps_by_market:
        return caps_by_market[normalized]
    if normalized.startswith("US_") and "US" in caps_by_market:
        return caps_by_market["US"]
    return float(settings.EXECUTABLE_QUOTE_MAX_GAP_PCT)


def _resolve_retry_price_from_executable_quote(
    *,
    order_type: str,
    stock_code: str,
    market_code: str,
    last_price: float,
    fallback_price: float,
    executable_quote: float | None,
    settings: Settings,
    enforce_gap_cap: bool,
) -> tuple[float | None, float | None, bool]:
    """Resolve retry price using executable quote first, with gap-cap protection."""
    if executable_quote is not None and executable_quote > 0:
        gap_pct: float | None = None
        if last_price > 0:
            gap_pct = abs(executable_quote - last_price) / last_price * 100.0
            if enforce_gap_cap:
                max_gap_pct = _resolve_executable_gap_cap_pct(
                    market_code=market_code,
                    settings=settings,
                )
                if gap_pct > max_gap_pct:
                    logger.warning(
                        "Skip %s retry for %s: executable quote gap too wide "
                        "(market=%s last=%.4f executable=%.4f gap_pct=%.2f cap_pct=%.2f)",
                        order_type,
                        stock_code,
                        market_code,
                        last_price,
                        executable_quote,
                        gap_pct,
                        max_gap_pct,
                    )
                    return None, gap_pct, True
        return executable_quote, gap_pct, False
    return fallback_price, None, False


async def _fetch_optional_quote_payload(
    *,
    obj: Any,
    method_name: str,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Call optional async quote fetch method when available."""
    method = getattr(obj, method_name, None)
    if method is None or not callable(method):
        return {}
    result = method(**kwargs)
    if inspect.isawaitable(result):
        resolved = await result
    else:
        resolved = result
    if isinstance(resolved, dict):
        return resolved
    return {}


async def _fetch_optional_orderbook_top_levels(
    *,
    obj: Any,
    method_name: str,
    kwargs: dict[str, Any],
    log_context: str,
) -> tuple[float | None, float | None]:
    """Fetch optional orderbook payload and resolve executable top levels."""
    try:
        payload = await _fetch_optional_quote_payload(
            obj=obj,
            method_name=method_name,
            kwargs=kwargs,
        )
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", log_context, exc)
        return None, None
    return extract_orderbook_top_levels(payload)


def _require_order_acceptance(
    result: dict[str, Any],
    *,
    order_type: str,
    stock_code: str,
    exchange_code: str,
) -> None:
    if result.get("rt_cd", "0") == "0":
        return
    raise ValueError(
        f"Order rejected ({order_type} {stock_code} {exchange_code} "
        f"rt_cd={result.get('rt_cd')} msg={result.get('msg1', '')})"
    )


def _is_ambiguous_submit_error(exc: Exception) -> bool:
    """Return True when submit result is unknown and rollback would be unsafe."""
    return isinstance(exc, ConnectionError)


def _has_matching_domestic_pending_order(
    orders: list[dict[str, Any]],
    *,
    stock_code: str,
    action: str,
    exchange_code: str,
) -> bool:
    expected_side = "02" if action == "BUY" else "01"
    normalized_stock_code = stock_code.strip().upper()
    normalized_exchange_code = exchange_code.strip().upper()
    for order in orders:
        pending_stock_code = str(order.get("pdno", "")).strip().upper()
        pending_side = str(order.get("sll_buy_dvsn_cd", "")).strip()
        pending_exchange = str(order.get("order_exchange") or exchange_code).strip().upper()
        pending_qty = int(order.get("psbl_qty", "0") or "0")
        if (
            pending_stock_code == normalized_stock_code
            and pending_side == expected_side
            and pending_exchange == normalized_exchange_code
            and pending_qty > 0
        ):
            return True
    return False


def _has_matching_overseas_pending_order(
    orders: list[dict[str, Any]],
    *,
    stock_code: str,
    action: str,
    exchange_code: str,
) -> bool:
    expected_side = "02" if action == "BUY" else "01"
    normalized_stock_code = stock_code.strip().upper()
    normalized_exchange_code = exchange_code.strip().upper()
    for order in orders:
        pending_stock_code = str(order.get("pdno", "")).strip().upper()
        pending_side = str(order.get("sll_buy_dvsn_cd", "")).strip()
        pending_exchange = str(order.get("ovrs_excg_cd") or exchange_code).strip().upper()
        pending_qty = int(order.get("nccs_qty", "0") or "0")
        if (
            pending_stock_code == normalized_stock_code
            and pending_side == expected_side
            and pending_exchange == normalized_exchange_code
            and pending_qty > 0
        ):
            return True
    return False


async def _reconcile_domestic_ambiguous_submit(
    broker: KISBroker,
    *,
    stock_code: str,
    action: str,
    exchange_code: str,
) -> tuple[str, int | None]:
    """Return state and broker-confirmed qty for a domestic ambiguous resubmit."""
    try:
        refreshed_orders = await broker.get_domestic_pending_orders()
    except Exception as exc:
        logger.warning(
            "Failed to reconcile domestic ambiguous %s submit for %s via pending orders: %s",
            action,
            stock_code,
            exc,
        )
    else:
        if _has_matching_domestic_pending_order(
            refreshed_orders,
            stock_code=stock_code,
            action=action,
            exchange_code=exchange_code,
        ):
            if action == "SELL":
                for order in refreshed_orders:
                    if _has_matching_domestic_pending_order(
                        [order],
                        stock_code=stock_code,
                        action=action,
                        exchange_code=exchange_code,
                    ):
                        return "pending", int(order.get("psbl_qty", "0") or "0")
            return "pending", None

    try:
        balance_data = await broker.get_balance()
    except Exception as exc:
        logger.warning(
            "Failed to reconcile domestic ambiguous %s submit for %s via holdings: %s",
            action,
            stock_code,
            exc,
        )
        return "unknown", None

    held_codes = _extract_held_codes_from_balance(balance_data, is_domestic=True)
    if stock_code.strip().upper() in held_codes:
        return (
            "held",
            _extract_held_qty_from_balance(balance_data, stock_code, is_domestic=True),
        )
    return "absent", None


async def _reconcile_overseas_ambiguous_submit(
    overseas_broker: OverseasBroker,
    *,
    stock_code: str,
    action: str,
    exchange_code: str,
) -> tuple[str, int | None]:
    """Return state and broker-confirmed qty for an overseas ambiguous resubmit."""
    try:
        refreshed_orders = await overseas_broker.get_overseas_pending_orders(exchange_code)
    except Exception as exc:
        logger.warning(
            "Failed to reconcile overseas ambiguous %s submit for %s %s via pending orders: %s",
            action,
            exchange_code,
            stock_code,
            exc,
        )
    else:
        if _has_matching_overseas_pending_order(
            refreshed_orders,
            stock_code=stock_code,
            action=action,
            exchange_code=exchange_code,
        ):
            if action == "SELL":
                for order in refreshed_orders:
                    if _has_matching_overseas_pending_order(
                        [order],
                        stock_code=stock_code,
                        action=action,
                        exchange_code=exchange_code,
                    ):
                        return "pending", int(order.get("nccs_qty", "0") or "0")
            return "pending", None

    try:
        balance_data = await overseas_broker.get_overseas_balance(exchange_code)
    except Exception as exc:
        logger.warning(
            "Failed to reconcile overseas ambiguous %s submit for %s %s via holdings: %s",
            action,
            exchange_code,
            stock_code,
            exc,
        )
        return "unknown", None

    held_codes = _extract_held_codes_from_balance(
        balance_data,
        is_domestic=False,
        exchange_code=exchange_code,
    )
    if stock_code.strip().upper() in held_codes:
        return (
            "held",
            _extract_held_qty_from_balance(balance_data, stock_code, is_domestic=False),
        )
    return "absent", None


async def handle_domestic_pending_orders(
    broker: KISBroker,
    telegram: TelegramClient,
    settings: Settings,
    sell_resubmit_counts: dict[str, int],
    buy_cooldown: dict[str, float] | None = None,
    quote_market_div_code: str = "J",
    rollback_open_position: RollbackOpenPosition | None = None,
) -> None:
    """Check and handle unfilled (pending) domestic limit orders.

    Called once per market loop iteration before new orders are considered.
    In paper mode the KIS pending-orders API (TTTC0084R) is unsupported, so
    ``get_domestic_pending_orders`` returns [] immediately and this function
    exits without making further API calls.

    BUY pending  → cancel then resubmit at +0.4% from last price (chase buy)
                   at most once per key per session. On subsequent unfilled BUY,
                   only cancel + set cooldown.
    SELL pending → cancel then resubmit at a wider spread (-0.4% from last
                   price, kr_round_down applied).  Resubmission is attempted
                   at most once per key per session to avoid infinite loops.

    Args:
        broker: KISBroker instance.
        telegram: TelegramClient for notifications.
        settings: Application settings.
        sell_resubmit_counts: Mutable dict tracking per-key resubmission attempts.
            SELL uses "KR:{stock_code}" key.
            BUY uses "BUY:KR:{stock_code}" key.
            Passed by reference so counts persist across calls within the same session.
        buy_cooldown: Optional cooldown dict shared with the main trading loop.
            When provided, cancelled BUY orders are added with a
            _BUY_COOLDOWN_SECONDS expiry.
        quote_market_div_code: KIS market division code used for price queries
            ("NX" for NXT_PRE/NXT_AFTER sessions, "J" otherwise).
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
            orgn_odno = order.get("orgn_odno", "") or order.get("odno", "")
            krx_fwdg_ord_orgno = order.get("ord_gno_brno", "")
            order_exchange = str(order.get("order_exchange") or "KRX")
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
                order_exchange=order_exchange,
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
                # BUY pending — attempt one chase resubmit, then give up.
                buy_resubmit_key = f"BUY:{key}"
                if sell_resubmit_counts.get(buy_resubmit_key, 0) >= 1:
                    if buy_cooldown is not None:
                        buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                    logger.warning(
                        "BUY KR %s already resubmitted once — cancel only + cooldown",
                        stock_code,
                    )
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
                    _rollback_pending_position(
                        rollback_open_position=rollback_open_position,
                        market_code="KR",
                        exchange_code=order_exchange,
                        stock_code=stock_code,
                        action="BUY",
                    )
                else:
                    try:
                        last_price, _, _ = await broker.get_current_price(
                            stock_code, market_div_code=quote_market_div_code
                        )
                        if last_price <= 0:
                            raise ValueError(f"Invalid price ({last_price}) for {stock_code}")
                        fallback_price = float(kr_round_down(last_price * _BUY_RETRY_MULTIPLIER))
                        executable_ask, _ = await _fetch_optional_orderbook_top_levels(
                            obj=broker,
                            method_name="get_orderbook_by_market",
                            kwargs={
                                "stock_code": stock_code,
                                "market_div_code": quote_market_div_code,
                            },
                            log_context=f"domestic orderbook for {stock_code}",
                        )
                        new_price, _, gap_rejected = _resolve_retry_price_from_executable_quote(
                            order_type="BUY",
                            stock_code=stock_code,
                            market_code="KR",
                            last_price=float(last_price),
                            fallback_price=fallback_price,
                            executable_quote=executable_ask,
                            settings=settings,
                            # Intentional policy: BUY retries apply gap-cap in all sessions.
                            enforce_gap_cap=True,
                        )
                        if gap_rejected:
                            if buy_cooldown is not None:
                                buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                            logger.warning(
                                "BUY KR %s cancelled after executable ask gap rejection",
                                stock_code,
                            )
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
                            _rollback_pending_position(
                                rollback_open_position=rollback_open_position,
                                market_code="KR",
                                exchange_code=order_exchange,
                                stock_code=stock_code,
                                action="BUY",
                            )
                            continue
                        if new_price is None:
                            raise ValueError(f"Failed to resolve BUY retry price for {stock_code}")
                        validate_order_policy(
                            market=MARKETS["KR"],
                            order_type="BUY",
                            price=float(new_price),
                        )
                        result = await broker.send_order(
                            stock_code=stock_code,
                            order_type="BUY",
                            quantity=psbl_qty,
                            price=new_price,
                            session_id=classify_session_id(MARKETS["KR"]),
                        )
                        _require_order_acceptance(
                            result,
                            order_type="BUY",
                            stock_code=stock_code,
                            exchange_code=order_exchange,
                        )
                        sell_resubmit_counts[buy_resubmit_key] = (
                            sell_resubmit_counts.get(buy_resubmit_key, 0) + 1
                        )
                        try:
                            await telegram.notify_unfilled_order(
                                stock_code=stock_code,
                                market="KR",
                                action="BUY",
                                quantity=psbl_qty,
                                outcome="resubmitted",
                                new_price=float(new_price),
                            )
                        except Exception as notify_exc:
                            logger.warning("notify_unfilled_order failed: %s", notify_exc)
                    except Exception as exc:
                        logger.error(
                            "BUY resubmit failed for KR %s: %s",
                            stock_code,
                            exc,
                        )
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
                        if _is_ambiguous_submit_error(exc):
                            reconcile_state, _ = await _reconcile_domestic_ambiguous_submit(
                                broker,
                                stock_code=stock_code,
                                action="BUY",
                                exchange_code=order_exchange,
                            )
                            if reconcile_state == "pending":
                                sell_resubmit_counts[buy_resubmit_key] = (
                                    sell_resubmit_counts.get(buy_resubmit_key, 0) + 1
                                )
                                logger.warning(
                                    "Confirm BUY resubmit pending for KR %s after ambiguous submit",
                                    stock_code,
                                )
                                continue
                            if reconcile_state == "held":
                                logger.warning(
                                    "Confirm BUY holding for KR %s after ambiguous submit",
                                    stock_code,
                                )
                                continue
                            if reconcile_state == "unknown":
                                if buy_cooldown is not None:
                                    buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                                logger.warning(
                                    "Skip BUY rollback for KR %s: resubmit submit status unknown",
                                    stock_code,
                                )
                                continue
                            if buy_cooldown is not None:
                                buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                                logger.warning(
                                    "Confirm BUY absence for KR %s after ambiguous submit",
                                    stock_code,
                                )
                        else:
                            if buy_cooldown is not None:
                                buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                        if _is_ambiguous_submit_error(exc):
                            logger.warning(
                                "Rollback BUY for KR %s after broker confirms no replacement",
                                stock_code,
                            )
                        _rollback_pending_position(
                            rollback_open_position=rollback_open_position,
                            market_code="KR",
                            exchange_code=order_exchange,
                            stock_code=stock_code,
                            action="BUY",
                        )

            elif sll_buy == "01":
                # SELL pending — attempt one resubmit at a wider spread.
                if sell_resubmit_counts.get(key, 0) >= 1:
                    # Already resubmitted once — only cancel (already done above).
                    # Increment to 2 so trading_cycle() can distinguish this
                    # "retry exhausted" state from "first resubmit still live" (count=1).
                    sell_resubmit_counts[key] = sell_resubmit_counts.get(key, 0) + 1
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
                        logger.warning("notify_unfilled_order failed: %s", notify_exc)
                    _rollback_pending_position(
                        rollback_open_position=rollback_open_position,
                        market_code="KR",
                        exchange_code=order_exchange,
                        stock_code=stock_code,
                        action="SELL",
                    )
                else:
                    # First unfilled SELL → resubmit at executable bid (fallback: last * 0.996).
                    try:
                        last_price, _, _ = await broker.get_current_price(
                            stock_code, market_div_code=quote_market_div_code
                        )
                        if last_price <= 0:
                            raise ValueError(f"Invalid price ({last_price}) for {stock_code}")
                        fallback_price = float(kr_round_down(last_price * _SELL_RETRY_MULTIPLIER))
                        _, executable_bid = await _fetch_optional_orderbook_top_levels(
                            obj=broker,
                            method_name="get_orderbook_by_market",
                            kwargs={
                                "stock_code": stock_code,
                                "market_div_code": quote_market_div_code,
                            },
                            log_context=f"domestic orderbook for {stock_code}",
                        )
                        new_price, _, _ = _resolve_retry_price_from_executable_quote(
                            order_type="SELL",
                            stock_code=stock_code,
                            market_code="KR",
                            last_price=float(last_price),
                            fallback_price=fallback_price,
                            executable_quote=executable_bid,
                            settings=settings,
                            enforce_gap_cap=False,
                        )
                        if new_price is None:
                            raise ValueError(f"Failed to resolve SELL retry price for {stock_code}")
                        validate_order_policy(
                            market=MARKETS["KR"],
                            order_type="SELL",
                            price=float(new_price),
                        )
                        result = await broker.send_order(
                            stock_code=stock_code,
                            order_type="SELL",
                            quantity=psbl_qty,
                            price=new_price,
                            session_id=classify_session_id(MARKETS["KR"]),
                        )
                        _require_order_acceptance(
                            result,
                            order_type="SELL",
                            stock_code=stock_code,
                            exchange_code=order_exchange,
                        )
                        sell_resubmit_counts[key] = sell_resubmit_counts.get(key, 0) + 1
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
                            logger.warning("notify_unfilled_order failed: %s", notify_exc)
                    except Exception as exc:
                        logger.error(
                            "SELL resubmit failed for KR %s: %s",
                            stock_code,
                            exc,
                        )
                        reconciled_qty: int | None = None
                        try:
                            await telegram.notify_unfilled_order(
                                stock_code=stock_code,
                                market="KR",
                                action="SELL",
                                quantity=psbl_qty,
                                outcome="cancelled",
                            )
                        except Exception as notify_exc:
                            logger.warning("notify_unfilled_order failed: %s", notify_exc)
                        if _is_ambiguous_submit_error(exc):
                            reconcile_state, reconciled_qty = (
                                await _reconcile_domestic_ambiguous_submit(
                                broker,
                                stock_code=stock_code,
                                action="SELL",
                                exchange_code=order_exchange,
                            ))
                            if reconcile_state == "pending":
                                sell_resubmit_counts[key] = sell_resubmit_counts.get(key, 0) + 1
                                logger.warning(
                                    "Restore SELL position for KR %s: "
                                    "broker confirms pending replacement",
                                    stock_code,
                                )
                            elif reconcile_state == "held":
                                logger.warning(
                                    "Restore SELL position for KR %s: "
                                    "broker still reports holdings",
                                    stock_code,
                                )
                            elif reconcile_state == "absent":
                                logger.warning(
                                    "Keep SELL position closed for KR %s: "
                                    "broker confirms no holding",
                                    stock_code,
                                )
                                continue
                            else:
                                logger.warning(
                                    "Skip SELL rollback for KR %s: resubmit submit status unknown",
                                    stock_code,
                                )
                                continue
                        _rollback_pending_position(
                            rollback_open_position=rollback_open_position,
                            market_code="KR",
                            exchange_code=order_exchange,
                            stock_code=stock_code,
                            action="SELL",
                            quantity=reconciled_qty,
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
    rollback_open_position: RollbackOpenPosition | None = None,
) -> None:
    """Check and handle unfilled (pending) overseas limit orders.

    Called once per market loop iteration before new orders are considered.
    In paper mode the KIS pending-orders API (TTTS3018R) is unsupported, so
    this function returns immediately without making any API calls.

    BUY pending  → cancel then resubmit at +0.4% from last price (chase buy)
                   at most once per key per session. On subsequent unfilled BUY,
                   only cancel + set cooldown.
    SELL pending → cancel then resubmit at a wider spread (-0.4% from last
                   price).  Resubmission is attempted at most once per key
                   per session to avoid infinite retry loops.

    Args:
        overseas_broker: OverseasBroker instance.
        telegram: TelegramClient for notifications.
        settings: Application settings (MODE, ENABLED_MARKETS).
        sell_resubmit_counts: Mutable dict tracking per-key resubmission attempts.
            SELL uses "{exchange_code}:{stock_code}" key.
            BUY uses "BUY:{exchange_code}:{stock_code}" key.
            Passed by reference so counts persist across calls within the same session.
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
            logger.warning("Failed to fetch pending orders for %s: %s", exchange_code, exc)
            continue

        for order in orders:
            try:
                stock_code = order.get("pdno", "")
                odno = order.get("odno", "")
                sll_buy = order.get("sll_buy_dvsn_cd", "")  # "01"=SELL, "02"=BUY
                nccs_qty = int(order.get("nccs_qty", "0") or "0")
                order_exchange = order.get("ovrs_excg_cd") or exchange_code
                key = f"{order_exchange}:{stock_code}"
                market_code = _resolve_overseas_market_code(order_exchange)

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
                    # BUY pending — attempt one chase resubmit, then give up.
                    buy_resubmit_key = f"BUY:{key}"
                    if sell_resubmit_counts.get(buy_resubmit_key, 0) >= 1:
                        if buy_cooldown is not None:
                            buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                        logger.warning(
                            "BUY %s %s already resubmitted once — cancel only + cooldown",
                            order_exchange,
                            stock_code,
                        )
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
                        _rollback_pending_position(
                            rollback_open_position=rollback_open_position,
                            market_code=market_code,
                            exchange_code=order_exchange,
                            stock_code=stock_code,
                            action="BUY",
                        )
                    else:
                        try:
                            price_data = await overseas_broker.get_overseas_price(
                                order_exchange, stock_code
                            )
                            last_price = float(price_data.get("output", {}).get("last", "0") or "0")
                            if last_price <= 0:
                                raise ValueError(f"Invalid price ({last_price}) for {stock_code}")
                            fallback_price = round(
                                last_price * _BUY_RETRY_MULTIPLIER,
                                2 if last_price >= 1 else 4,
                            )
                            executable_ask, _ = await _fetch_optional_orderbook_top_levels(
                                obj=overseas_broker,
                                method_name="get_overseas_orderbook",
                                kwargs={
                                    "exchange_code": order_exchange,
                                    "stock_code": stock_code,
                                },
                                log_context=f"overseas orderbook for {order_exchange} {stock_code}",
                            )
                            new_price, _, gap_rejected = _resolve_retry_price_from_executable_quote(
                                order_type="BUY",
                                stock_code=stock_code,
                                market_code=market_code,
                                last_price=float(last_price),
                                fallback_price=fallback_price,
                                executable_quote=executable_ask,
                                settings=settings,
                                # Intentional policy: BUY retries apply gap-cap in all sessions.
                                enforce_gap_cap=True,
                            )
                            if gap_rejected:
                                if buy_cooldown is not None:
                                    buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                                logger.warning(
                                    "BUY %s %s cancelled after executable ask gap rejection",
                                    order_exchange,
                                    stock_code,
                                )
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
                                _rollback_pending_position(
                                    rollback_open_position=rollback_open_position,
                                    market_code=market_code,
                                    exchange_code=order_exchange,
                                    stock_code=stock_code,
                                    action="BUY",
                                )
                                continue
                            if new_price is None:
                                raise ValueError(
                                    f"Failed to resolve BUY retry price for {stock_code}"
                                )
                            market_info = next(
                                (
                                    m
                                    for m in MARKETS.values()
                                    if m.exchange_code == order_exchange and not m.is_domestic
                                ),
                                None,
                            )
                            if market_info is not None:
                                validate_order_policy(
                                    market=market_info,
                                    order_type="BUY",
                                    price=float(new_price),
                                )
                            result = await overseas_broker.send_overseas_order(
                                exchange_code=order_exchange,
                                stock_code=stock_code,
                                order_type="BUY",
                                quantity=nccs_qty,
                                price=new_price,
                            )
                            _require_order_acceptance(
                                result,
                                order_type="BUY",
                                stock_code=stock_code,
                                exchange_code=order_exchange,
                            )
                            sell_resubmit_counts[buy_resubmit_key] = (
                                sell_resubmit_counts.get(buy_resubmit_key, 0) + 1
                            )
                            try:
                                await telegram.notify_unfilled_order(
                                    stock_code=stock_code,
                                    market=order_exchange,
                                    action="BUY",
                                    quantity=nccs_qty,
                                    outcome="resubmitted",
                                    new_price=new_price,
                                )
                            except Exception as notify_exc:
                                logger.warning("notify_unfilled_order failed: %s", notify_exc)
                        except Exception as exc:
                            logger.error(
                                "BUY resubmit failed for %s %s: %s",
                                order_exchange,
                                stock_code,
                                exc,
                            )
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
                            if _is_ambiguous_submit_error(exc):
                                reconcile_state, _ = await _reconcile_overseas_ambiguous_submit(
                                    overseas_broker,
                                    stock_code=stock_code,
                                    action="BUY",
                                    exchange_code=order_exchange,
                                )
                                if reconcile_state == "pending":
                                    sell_resubmit_counts[buy_resubmit_key] = (
                                        sell_resubmit_counts.get(buy_resubmit_key, 0) + 1
                                    )
                                    logger.warning(
                                        "Confirm BUY resubmit pending for %s %s "
                                        "after ambiguous submit",
                                        order_exchange,
                                        stock_code,
                                    )
                                    continue
                                if reconcile_state == "held":
                                    logger.warning(
                                        "Confirm BUY holding for %s %s after ambiguous submit",
                                        order_exchange,
                                        stock_code,
                                    )
                                    continue
                                if reconcile_state == "unknown":
                                    if buy_cooldown is not None:
                                        buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                                    logger.warning(
                                        "Skip BUY rollback for %s %s: "
                                        "resubmit submit status unknown",
                                        order_exchange,
                                        stock_code,
                                    )
                                    continue
                                if buy_cooldown is not None:
                                    buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                                logger.warning(
                                    "Rollback BUY for %s %s after broker confirms no replacement",
                                    order_exchange,
                                    stock_code,
                                )
                            else:
                                if buy_cooldown is not None:
                                    buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS
                            if _is_ambiguous_submit_error(exc):
                                # Reaching here means broker reconciliation confirmed absence.
                                logger.warning(
                                    "Rollback BUY for %s %s after broker confirms no replacement",
                                    order_exchange,
                                    stock_code,
                                )
                            _rollback_pending_position(
                                rollback_open_position=rollback_open_position,
                                market_code=market_code,
                                exchange_code=order_exchange,
                                stock_code=stock_code,
                                action="BUY",
                            )

                elif sll_buy == "01":
                    # SELL pending — attempt one resubmit at a wider spread.
                    if sell_resubmit_counts.get(key, 0) >= 1:
                        # Already resubmitted once — only cancel (already done above).
                        # Increment to 2 so trading_cycle() can distinguish this
                        # "retry exhausted" state from "first resubmit still live" (count=1).
                        sell_resubmit_counts[key] = sell_resubmit_counts.get(key, 0) + 1
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
                            logger.warning("notify_unfilled_order failed: %s", notify_exc)
                        _rollback_pending_position(
                            rollback_open_position=rollback_open_position,
                            market_code=market_code,
                            exchange_code=order_exchange,
                            stock_code=stock_code,
                            action="SELL",
                        )
                    else:
                        # First unfilled SELL → resubmit at executable bid (fallback: last * 0.996).
                        try:
                            price_data = await overseas_broker.get_overseas_price(
                                order_exchange, stock_code
                            )
                            last_price = float(price_data.get("output", {}).get("last", "0") or "0")
                            if last_price <= 0:
                                raise ValueError(f"Invalid price ({last_price}) for {stock_code}")
                            fallback_price = round(
                                last_price * _SELL_RETRY_MULTIPLIER,
                                2 if last_price >= 1 else 4,
                            )
                            _, executable_bid = await _fetch_optional_orderbook_top_levels(
                                obj=overseas_broker,
                                method_name="get_overseas_orderbook",
                                kwargs={
                                    "exchange_code": order_exchange,
                                    "stock_code": stock_code,
                                },
                                log_context=f"overseas orderbook for {order_exchange} {stock_code}",
                            )
                            new_price, _, _ = _resolve_retry_price_from_executable_quote(
                                order_type="SELL",
                                stock_code=stock_code,
                                market_code=market_code,
                                last_price=float(last_price),
                                fallback_price=fallback_price,
                                executable_quote=executable_bid,
                                settings=settings,
                                enforce_gap_cap=False,
                            )
                            if new_price is None:
                                raise ValueError(
                                    f"Failed to resolve SELL retry price for {stock_code}"
                                )
                            market_info = next(
                                (
                                    m
                                    for m in MARKETS.values()
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
                            result = await overseas_broker.send_overseas_order(
                                exchange_code=order_exchange,
                                stock_code=stock_code,
                                order_type="SELL",
                                quantity=nccs_qty,
                                price=new_price,
                            )
                            _require_order_acceptance(
                                result,
                                order_type="SELL",
                                stock_code=stock_code,
                                exchange_code=order_exchange,
                            )
                            sell_resubmit_counts[key] = sell_resubmit_counts.get(key, 0) + 1
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
                                logger.warning("notify_unfilled_order failed: %s", notify_exc)
                        except Exception as exc:
                            logger.error(
                                "SELL resubmit failed for %s %s: %s",
                                order_exchange,
                                stock_code,
                                exc,
                            )
                            reconciled_qty: int | None = None
                            try:
                                await telegram.notify_unfilled_order(
                                    stock_code=stock_code,
                                    market=order_exchange,
                                    action="SELL",
                                    quantity=nccs_qty,
                                    outcome="cancelled",
                                )
                            except Exception as notify_exc:
                                logger.warning("notify_unfilled_order failed: %s", notify_exc)
                            if _is_ambiguous_submit_error(exc):
                                reconcile_state, reconciled_qty = (
                                    await _reconcile_overseas_ambiguous_submit(
                                    overseas_broker,
                                    stock_code=stock_code,
                                    action="SELL",
                                    exchange_code=order_exchange,
                                ))
                                if reconcile_state == "pending":
                                    sell_resubmit_counts[key] = sell_resubmit_counts.get(key, 0) + 1
                                    logger.warning(
                                        "Restore SELL position for %s %s: "
                                        "broker confirms pending replacement",
                                        order_exchange,
                                        stock_code,
                                    )
                                elif reconcile_state == "held":
                                    logger.warning(
                                        "Restore SELL position for %s %s: "
                                        "broker still reports holdings",
                                        order_exchange,
                                        stock_code,
                                    )
                                elif reconcile_state == "absent":
                                    logger.warning(
                                        "Keep SELL position closed for %s %s: "
                                        "broker confirms no holding",
                                        order_exchange,
                                        stock_code,
                                    )
                                    continue
                                else:
                                    logger.warning(
                                        "Skip SELL rollback for %s %s: "
                                        "resubmit submit status unknown",
                                        order_exchange,
                                        stock_code,
                                    )
                                    continue
                            _rollback_pending_position(
                                rollback_open_position=rollback_open_position,
                                market_code=market_code,
                                exchange_code=order_exchange,
                                stock_code=stock_code,
                                action="SELL",
                                quantity=reconciled_qty,
                            )

            except Exception as exc:
                logger.error(
                    "Error handling pending order for %s: %s",
                    order.get("pdno", "?"),
                    exc,
                )
