"""Handle unfilled (pending) domestic and overseas limit orders.

Extracted from ``src/main.py`` to reduce module size.
"""

from __future__ import annotations

import asyncio
import logging

from src.broker.kis_api import KISBroker, kr_round_down
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


async def handle_domestic_pending_orders(
    broker: KISBroker,
    telegram: TelegramClient,
    settings: Settings,
    sell_resubmit_counts: dict[str, int],
    buy_cooldown: dict[str, float] | None = None,
    quote_market_div_code: str = "J",
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
                else:
                    try:
                        last_price, _, _ = await broker.get_current_price(
                            stock_code, market_div_code=quote_market_div_code
                        )
                        if last_price <= 0:
                            raise ValueError(f"Invalid price ({last_price}) for {stock_code}")
                        new_price = kr_round_down(last_price * 1.004)
                        validate_order_policy(
                            market=MARKETS["KR"],
                            order_type="BUY",
                            price=float(new_price),
                        )
                        await broker.send_order(
                            stock_code=stock_code,
                            order_type="BUY",
                            quantity=psbl_qty,
                            price=new_price,
                            session_id=classify_session_id(MARKETS["KR"]),
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
                        if buy_cooldown is not None:
                            buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS

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
                        logger.warning("notify_unfilled_order failed: %s", notify_exc)
                else:
                    # First unfilled SELL → resubmit at last * 0.996 (-0.4%).
                    try:
                        last_price, _, _ = await broker.get_current_price(stock_code)
                        if last_price <= 0:
                            raise ValueError(f"Invalid price ({last_price}) for {stock_code}")
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
                            session_id=classify_session_id(MARKETS["KR"]),
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
                    else:
                        try:
                            price_data = await overseas_broker.get_overseas_price(
                                order_exchange, stock_code
                            )
                            last_price = float(price_data.get("output", {}).get("last", "0") or "0")
                            if last_price <= 0:
                                raise ValueError(f"Invalid price ({last_price}) for {stock_code}")
                            new_price = round(last_price * 1.004, 4)
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
                            await overseas_broker.send_overseas_order(
                                exchange_code=order_exchange,
                                stock_code=stock_code,
                                order_type="BUY",
                                quantity=nccs_qty,
                                price=new_price,
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
                            if buy_cooldown is not None:
                                buy_cooldown[key] = now + _BUY_COOLDOWN_SECONDS

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
                            logger.warning("notify_unfilled_order failed: %s", notify_exc)
                    else:
                        # First unfilled SELL → resubmit at last * 0.996 (-0.4%).
                        try:
                            price_data = await overseas_broker.get_overseas_price(
                                order_exchange, stock_code
                            )
                            last_price = float(price_data.get("output", {}).get("last", "0") or "0")
                            if last_price <= 0:
                                raise ValueError(f"Invalid price ({last_price}) for {stock_code}")
                            new_price = round(last_price * 0.996, 4)
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
                            await overseas_broker.send_overseas_order(
                                exchange_code=order_exchange,
                                stock_code=stock_code,
                                order_type="SELL",
                                quantity=nccs_qty,
                                price=new_price,
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

            except Exception as exc:
                logger.error(
                    "Error handling pending order for %s: %s",
                    order.get("pdno", "?"),
                    exc,
                )
