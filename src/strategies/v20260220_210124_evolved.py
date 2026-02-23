"""Auto-generated strategy: v20260220_210124

Generated at: 2026-02-20T21:01:24.706847+00:00
Rationale: Auto-evolved from 6 failures. Primary failure markets: ['US_AMEX', 'US_NYSE', 'US_NASDAQ']. Average loss: -194.69
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260220_210124(BaseStrategy):
    """Strategy: v20260220_210124"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
        import datetime

        # --- Strategy Constants ---
        # Minimum price for a stock to be considered for trading (avoids penny stocks)
        MIN_PRICE = 5.0

        # Momentum signal thresholds (stricter than previous failures)
        MOMENTUM_PRICE_CHANGE_THRESHOLD = 7.0  # % price change
        MOMENTUM_VOLUME_RATIO_THRESHOLD = 4.0  # X times average volume

        # Oversold signal thresholds (more conservative)
        OVERSOLD_RSI_THRESHOLD = 25.0  # RSI value (lower means more oversold)

        # Confidence levels
        CONFIDENCE_HOLD = 30
        CONFIDENCE_BUY_OVERSOLD = 65
        CONFIDENCE_BUY_MOMENTUM = 85
        CONFIDENCE_BUY_STRONG_MOMENTUM = 90  # For higher-priced stocks with strong momentum

        # Market hours in UTC (9:30 AM ET to 4:00 PM ET)
        MARKET_OPEN_UTC = datetime.time(14, 30)
        MARKET_CLOSE_UTC = datetime.time(21, 0)

        # Volatile periods within market hours (UTC) to avoid
        # First hour after open (14:30 UTC - 15:30 UTC)
        VOLATILE_OPEN_END_UTC = datetime.time(15, 30)
        # Last 30 minutes before close (20:30 UTC - 21:00 UTC)
        VOLATILE_CLOSE_START_UTC = datetime.time(20, 30)

        current_price = market_data.get('current_price')
        price_change_pct = market_data.get('price_change_pct')
        volume_ratio = market_data.get('volume_ratio')  # Assumed pre-computed indicator
        rsi = market_data.get('rsi')                    # Assumed pre-computed indicator
        timestamp_str = market_data.get('timestamp')

        action = "HOLD"
        confidence = CONFIDENCE_HOLD
        rationale = "Initial HOLD: No clear signal or conditions not met."

        # --- 1. Basic Data Validation ---
        if current_price is None or price_change_pct is None:
            return {"action": "HOLD", "confidence": CONFIDENCE_HOLD,
                    "rationale": "Insufficient core data (price or price change) to evaluate."}

        # --- 2. Price Filter: Avoid low-priced/penny stocks ---
        if current_price < MIN_PRICE:
            return {"action": "HOLD", "confidence": CONFIDENCE_HOLD,
                    "rationale": f"Avoiding low-priced stock (${current_price:.2f} < ${MIN_PRICE:.2f})."}

        # --- 3. Time Filter: Only trade during core market hours ---
        if timestamp_str:
            try:
                dt_object = datetime.datetime.fromisoformat(timestamp_str)
                current_time_utc = dt_object.time()

                if not (MARKET_OPEN_UTC <= current_time_utc < MARKET_CLOSE_UTC):
                    return {"action": "HOLD", "confidence": CONFIDENCE_HOLD,
                            "rationale": f"Avoiding trade outside core market hours ({current_time_utc} UTC)."}

                if (MARKET_OPEN_UTC <= current_time_utc < VOLATILE_OPEN_END_UTC) or \
                   (VOLATILE_CLOSE_START_UTC <= current_time_utc < MARKET_CLOSE_UTC):
                    return {"action": "HOLD", "confidence": CONFIDENCE_HOLD,
                            "rationale": f"Avoiding trade during volatile market open/close periods ({current_time_utc} UTC)."}

            except ValueError:
                rationale += " (Warning: Malformed timestamp, time filters skipped)"

        # --- Initialize signal states ---
        has_momentum_buy_signal = False
        has_oversold_buy_signal = False

        # --- 4. Evaluate Enhanced Buy Signals ---

        # Momentum Buy Signal
        if volume_ratio is not None and \
           price_change_pct > MOMENTUM_PRICE_CHANGE_THRESHOLD and \
           volume_ratio > MOMENTUM_VOLUME_RATIO_THRESHOLD:
            has_momentum_buy_signal = True
            rationale = f"Momentum BUY: Price change {price_change_pct:.2f}%, Volume {volume_ratio:.2f}x."
            confidence = CONFIDENCE_BUY_MOMENTUM
            if current_price >= 10.0:
                confidence = CONFIDENCE_BUY_STRONG_MOMENTUM

        # Oversold Buy Signal
        if rsi is not None and rsi < OVERSOLD_RSI_THRESHOLD:
            has_oversold_buy_signal = True
            if not has_momentum_buy_signal:
                rationale = f"Oversold BUY: RSI {rsi:.2f}."
                confidence = CONFIDENCE_BUY_OVERSOLD
                if current_price >= 10.0:
                    confidence = min(CONFIDENCE_BUY_OVERSOLD + 5, 80)

        # --- 5. Decision Logic ---
        if has_momentum_buy_signal:
            action = "BUY"
        elif has_oversold_buy_signal:
            action = "BUY"

        return {"action": action, "confidence": confidence, "rationale": rationale}
