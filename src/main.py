"""The Ouroboros — main trading loop.

Orchestrates the broker, brain, and risk manager into a continuous
trading cycle with configurable intervals.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from typing import Any

from src.brain.gemini_client import GeminiClient
from src.broker.kis_api import KISBroker
from src.config import Settings
from src.core.risk_manager import CircuitBreakerTripped, RiskManager
from src.db import init_db, log_trade
from src.logging_config import setup_logging

logger = logging.getLogger(__name__)

# Target stock codes to monitor
WATCHLIST = ["005930", "000660", "035420"]  # Samsung, SK Hynix, NAVER

TRADE_INTERVAL_SECONDS = 60


async def trading_cycle(
    broker: KISBroker,
    brain: GeminiClient,
    risk: RiskManager,
    db_conn: Any,
    stock_code: str,
) -> None:
    """Execute one trading cycle for a single stock."""
    # 1. Fetch market data
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

    # Calculate daily P&L %
    pnl_pct = ((total_eval - purchase_total) / purchase_total * 100) if purchase_total > 0 else 0.0

    current_price = float(
        orderbook.get("output1", {}).get("stck_prpr", "0")
    )

    market_data = {
        "stock_code": stock_code,
        "current_price": current_price,
        "orderbook": orderbook.get("output1", {}),
        "foreigner_net": float(
            orderbook.get("output1", {}).get("frgn_ntby_qty", "0")
        ),
    }

    # 2. Ask the brain for a decision
    decision = await brain.decide(market_data)
    logger.info(
        "Decision for %s: %s (confidence=%d)",
        stock_code,
        decision.action,
        decision.confidence,
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
        result = await broker.send_order(
            stock_code=stock_code,
            order_type=decision.action,
            quantity=quantity,
            price=0,  # market order
        )
        logger.info("Order result: %s", result.get("msg1", "OK"))

    # 6. Log trade
    log_trade(
        conn=db_conn,
        stock_code=stock_code,
        action=decision.action,
        confidence=decision.confidence,
        rationale=decision.rationale,
    )


async def run(settings: Settings) -> None:
    """Main async loop — iterate over watchlist on a timer."""
    broker = KISBroker(settings)
    brain = GeminiClient(settings)
    risk = RiskManager(settings)
    db_conn = init_db(settings.DB_PATH)

    shutdown = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("The Ouroboros is alive. Mode: %s", settings.MODE)
    logger.info("Watchlist: %s", WATCHLIST)

    try:
        while not shutdown.is_set():
            for code in WATCHLIST:
                if shutdown.is_set():
                    break
                try:
                    await trading_cycle(broker, brain, risk, db_conn, code)
                except CircuitBreakerTripped:
                    logger.critical("Circuit breaker tripped — shutting down")
                    raise
                except ConnectionError as exc:
                    logger.error("Connection error for %s: %s", code, exc)
                except Exception as exc:
                    logger.exception("Unexpected error for %s: %s", code, exc)

            # Wait for next cycle or shutdown
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=TRADE_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
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
