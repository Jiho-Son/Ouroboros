"""Auto-generated strategy: v20260220_210159

Generated at: 2026-02-20T21:01:59.391523+00:00
Rationale: Auto-evolved from 6 failures. Primary failure markets: ['US_AMEX', 'US_NYSE', 'US_NASDAQ']. Average loss: -194.69
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260220_210159(BaseStrategy):
    """Strategy: v20260220_210159"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
        import datetime

        current_price = market_data.get('current_price')
        price_change_pct = market_data.get('price_change_pct')
        volume_ratio = market_data.get('volume_ratio')
        rsi = market_data.get('rsi')
        timestamp_str = market_data.get('timestamp')
        market_name = market_data.get('market')

        # Default action
        action = "HOLD"
        confidence = 0
        rationale = "No strong signal or conditions not met."

        # --- FAILURE PATTERN AVOIDANCE ---

        # 1. Avoid low-priced/penny stocks
        MIN_PRICE_THRESHOLD = 5.0  # USD
        if current_price is not None and current_price < MIN_PRICE_THRESHOLD:
            rationale = (
                f"HOLD: Stock price (${current_price:.2f}) is below minimum threshold "
                f"(${MIN_PRICE_THRESHOLD:.2f}). Past failures consistently involved low-priced stocks."
            )
            return {"action": action, "confidence": confidence, "rationale": rationale}

        # 2. Avoid early market hour volatility
        if timestamp_str:
            try:
                dt_obj = datetime.datetime.fromisoformat(timestamp_str)
                utc_hour = dt_obj.hour
                utc_minute = dt_obj.minute

                if (utc_hour == 14 and utc_minute < 45) or (utc_hour == 13 and utc_minute >= 30):
                    rationale = (
                        f"HOLD: Trading during early market hours (UTC {utc_hour}:{utc_minute}), "
                        f"a period identified with past failures due to high volatility."
                    )
                    return {"action": action, "confidence": confidence, "rationale": rationale}
            except ValueError:
                pass

        # --- IMPROVED BUY STRATEGY ---

        # Momentum BUY signal
        if volume_ratio is not None and price_change_pct is not None:
            if price_change_pct > 7.0 and volume_ratio > 3.0:
                action = "BUY"
                confidence = 70
                rationale = "Improved BUY: Momentum signal with high volume and above price threshold."

                if market_name == 'US_AMEX':
                    confidence = max(55, confidence - 5)
                    rationale += " (Adjusted lower for AMEX market's higher risk profile)."
                elif market_name == 'US_NASDAQ' and price_change_pct > 20:
                    confidence = max(50, confidence - 10)
                    rationale += " (Adjusted lower for aggressive NASDAQ momentum volatility)."

                if price_change_pct > 15.0:
                    confidence = max(50, confidence - 5)
                    rationale += " (Caution: Very high daily price change, potential for reversal)."

                return {"action": action, "confidence": confidence, "rationale": rationale}

        # Oversold BUY signal
        if rsi is not None and price_change_pct is not None:
            if rsi < 30 and price_change_pct < -3.0:
                action = "BUY"
                confidence = 65
                rationale = "Improved BUY: Oversold signal with recent decline and above price threshold."

                if market_name == 'US_AMEX':
                    confidence = max(50, confidence - 5)
                    rationale += " (Adjusted lower for AMEX market's higher risk on oversold assets)."

                if price_change_pct < -10.0:
                    confidence = max(45, confidence - 10)
                    rationale += " (Caution: Very steep decline, potential falling knife)."

                return {"action": action, "confidence": confidence, "rationale": rationale}

        # If no specific BUY signal, default to HOLD
        return {"action": action, "confidence": confidence, "rationale": rationale}
