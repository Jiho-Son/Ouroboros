"""Auto-generated strategy: v20260220_210244

Generated at: 2026-02-20T21:02:44.387355+00:00
Rationale: Auto-evolved from 6 failures. Primary failure markets: ['US_AMEX', 'US_NYSE', 'US_NASDAQ']. Average loss: -194.69
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260220_210244(BaseStrategy):
    """Strategy: v20260220_210244"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
        from datetime import datetime

        # Extract required data points safely
        current_price = market_data.get("current_price")
        price_change_pct = market_data.get("price_change_pct")
        volume_ratio = market_data.get("volume_ratio")
        rsi = market_data.get("rsi")
        timestamp_str = market_data.get("timestamp")
        market_name = market_data.get("market")
        stock_code = market_data.get("stock_code", "UNKNOWN")

        # Default action is HOLD with conservative confidence and rationale
        action = "HOLD"
        confidence = 50
        rationale = f"No strong BUY signal for {stock_code} or awaiting more favorable conditions after avoiding known failure patterns."

        # --- 1. Failure Pattern Avoidance Filters ---

        # A. Avoid low-priced (penny) stocks
        if current_price is not None and current_price < 5.0:
            return {
                "action": "HOLD",
                "confidence": 50,
                "rationale": f"AVOID {stock_code}: Stock price (${current_price:.2f}) is below minimum threshold ($5.00) for BUY action. Identified past failures on highly volatile, low-priced stocks."
            }

        # B. Avoid initiating BUY trades during identified high-volatility hours
        if timestamp_str:
            try:
                trade_hour = datetime.fromisoformat(timestamp_str).hour
                if trade_hour in [14, 20]:
                    return {
                        "action": "HOLD",
                        "confidence": 50,
                        "rationale": f"AVOID {stock_code}: Trading during historically volatile hour ({trade_hour} UTC) where previous BUYs resulted in losses. Prefer to observe market stability."
                    }
            except ValueError:
                pass

        # C. Be cautious with extreme momentum spikes
        if volume_ratio is not None and price_change_pct is not None:
            if volume_ratio >= 9.0 and price_change_pct >= 15.0:
                return {
                    "action": "HOLD",
                    "confidence": 50,
                    "rationale": f"AVOID {stock_code}: Extreme short-term momentum detected (price change: +{price_change_pct:.2f}%, volume ratio: {volume_ratio:.1f}x). Historical failures indicate buying into such rapid spikes often leads to reversals."
                }

        # D. Be cautious with "oversold" signals without further confirmation
        if rsi is not None and rsi < 30:
            return {
                "action": "HOLD",
                "confidence": 50,
                "rationale": f"AVOID {stock_code}: Oversold signal (RSI={rsi:.1f}) detected. While often a BUY signal, historical failures on similar 'oversold' trades suggest waiting for stronger confirmation."
            }

        # --- 2. Improved BUY Signal Generation ---
        if volume_ratio is not None and 2.0 <= volume_ratio < 9.0 and \
           price_change_pct is not None and 2.0 <= price_change_pct < 15.0:

            action = "BUY"
            confidence = 70
            rationale = f"BUY {stock_code}: Moderate momentum detected (price change: +{price_change_pct:.2f}%, volume ratio: {volume_ratio:.1f}x). Passed filters for price and extreme momentum, avoiding past failure patterns."

            if market_name in ["US_AMEX", "US_NASDAQ"]:
                confidence = max(60, confidence - 5)
                rationale += f" Adjusted confidence for {market_name} market characteristics."
            elif market_name == "US_NYSE":
                confidence = max(65, confidence)

            confidence = max(50, min(85, confidence))

        return {"action": action, "confidence": confidence, "rationale": rationale}
