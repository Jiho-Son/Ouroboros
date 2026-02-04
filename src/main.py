"""The Ouroboros — main trading loop.

Orchestrates the broker, brain, and risk manager into a continuous
trading cycle with configurable intervals.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from datetime import UTC, datetime
from typing import Any

from src.brain.gemini_client import GeminiClient
from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker
from src.config import Settings
from src.core.risk_manager import CircuitBreakerTripped, RiskManager
from src.db import init_db, log_trade
from src.logging.decision_logger import DecisionLogger
from src.logging_config import setup_logging
from src.markets.schedule import MarketInfo, get_next_market_open, get_open_markets

logger = logging.getLogger(__name__)

# Target stock codes to monitor per market
WATCHLISTS = {
    "KR": ["005930", "000660", "035420"],  # Samsung, SK Hynix, NAVER
    "US_NASDAQ": ["AAPL", "MSFT", "GOOGL"],  # Example US stocks
    "US_NYSE": ["JPM", "BAC"],  # Example NYSE stocks
    "JP": ["7203", "6758"],  # Toyota, Sony
}

TRADE_INTERVAL_SECONDS = 60
MAX_CONNECTION_RETRIES = 3


async def trading_cycle(
    broker: KISBroker,
    overseas_broker: OverseasBroker,
    brain: GeminiClient,
    risk: RiskManager,
    db_conn: Any,
    decision_logger: DecisionLogger,
    market: MarketInfo,
    stock_code: str,
) -> None:
    """Execute one trading cycle for a single stock."""
    # 1. Fetch market data
    if market.is_domestic:
        orderbook = await broker.get_orderbook(stock_code)
        balance_data = await broker.get_balance()

        output2 = balance_data.get("output2", [{}])
        total_eval = float(output2[0].get("tot_evlu_amt", "0")) if output2 else 0
        total_cash = float(
            balance_data.get("output2", [{}])[0].get("dnca_tot_amt", "0")
            if output2
            else "0"
        )
        purchase_total = float(output2[0].get("pchs_amt_smtl_amt", "0")) if output2 else 0

        current_price = float(orderbook.get("output1", {}).get("stck_prpr", "0"))
        foreigner_net = float(orderbook.get("output1", {}).get("frgn_ntby_qty", "0"))
    else:
        # Overseas market
        price_data = await overseas_broker.get_overseas_price(
            market.exchange_code, stock_code
        )
        balance_data = await overseas_broker.get_overseas_balance(market.exchange_code)

        output2 = balance_data.get("output2", [{}])
        total_eval = float(output2[0].get("frcr_evlu_tota", "0")) if output2 else 0
        total_cash = float(output2[0].get("frcr_dncl_amt_2", "0")) if output2 else 0
        purchase_total = float(output2[0].get("frcr_buy_amt_smtl", "0")) if output2 else 0

        current_price = float(price_data.get("output", {}).get("last", "0"))
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
        risk.validate_order(
            current_pnl_pct=pnl_pct,
            order_amount=order_amount,
            total_cash=total_cash,
        )

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


async def run(settings: Settings) -> None:
    """Main async loop — iterate over open markets on a timer."""
    broker = KISBroker(settings)
    overseas_broker = OverseasBroker(broker)
    brain = GeminiClient(settings)
    risk = RiskManager(settings)
    db_conn = init_db(settings.DB_PATH)
    decision_logger = DecisionLogger(db_conn)

    shutdown = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("The Ouroboros is alive. Mode: %s", settings.MODE)
    logger.info("Enabled markets: %s", settings.enabled_market_list)

    try:
        while not shutdown.is_set():
            # Get currently open markets
            open_markets = get_open_markets(settings.enabled_market_list)

            if not open_markets:
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
                                market,
                                stock_code,
                            )
                            break  # Success — exit retry loop
                        except CircuitBreakerTripped:
                            logger.critical("Circuit breaker tripped — shutting down")
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

            # Wait for next cycle or shutdown
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=TRADE_INTERVAL_SECONDS)
            except TimeoutError:
                pass  # Normal — timeout means it's time for next cycle
    finally:
        await broker.close()
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
