"""Kill-switch runtime helpers extracted from src/main.py."""

from __future__ import annotations

import logging
from typing import Any

from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker
from src.config import Settings
from src.core.kill_switch import KillSwitchOrchestrator
from src.core.order_policy import get_session_info
from src.markets.schedule import MARKETS, MarketInfo
from src.notifications.telegram_client import TelegramClient

logger = logging.getLogger(__name__)
KILL_SWITCH = KillSwitchOrchestrator()


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
                order_exchange = str(order.get("order_exchange") or "KRX")
                psbl_qty = int(order.get("psbl_qty", "0") or "0")
                if not stock_code or not orgn_odno or psbl_qty <= 0:
                    continue
                cancel_result = await broker.cancel_domestic_order(
                    stock_code=stock_code,
                    orgn_odno=orgn_odno,
                    krx_fwdg_ord_orgno=krx_fwdg_ord_orgno,
                    qty=psbl_qty,
                    order_exchange=order_exchange,
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
        summary = "; ".join(failures[:3])
        if len(failures) > 3:
            summary = f"{summary} (+{len(failures) - 3} more)"
        raise RuntimeError(summary)


async def _refresh_order_state_for_kill_switch(
    *,
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    markets: list[MarketInfo],
) -> None:
    failures: list[str] = []
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
            failures.append(f"{market.code}/{market.exchange_code}: {exc}")
    if failures:
        summary = "; ".join(failures[:3])
        if len(failures) > 3:
            summary = f"{summary} (+{len(failures) - 3} more)"
        raise RuntimeError(summary)


def _reduce_risk_for_kill_switch() -> None:
    # Lazy import: BLACKOUT_ORDER_MANAGER will be extracted in Task 7
    from src.main import BLACKOUT_ORDER_MANAGER

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
