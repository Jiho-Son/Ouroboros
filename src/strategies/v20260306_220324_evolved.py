"""Auto-generated strategy: v20260306_220324

Generated at: 2026-03-06T22:03:24.887878+00:00
Rationale: Auto-evolved from 16 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -21963.33
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260306_220324(BaseStrategy):
    """Strategy: v20260306_220324"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            import datetime

            def evaluate(self, market_data: dict) -> dict:
                action = "HOLD"
                confidence = 50
                rationale = "No strong signal to act."

                market = market_data.get("market")
                timestamp_str = market_data.get("timestamp")
                current_price = market_data.get("current_price") # Not directly used for decision but good to have
                price_change_pct = market_data.get("price_change_pct")

                # Extract indicators from scenario_match
                rsi = market_data.get("scenario_match", {}).get("rsi")
                volume_ratio = market_data.get("scenario_match", {}).get("volume_ratio")

                # --- Pre-computation and Data Validation ---
                if timestamp_str:
                    dt_object = datetime.datetime.fromisoformat(timestamp_str)
                    utc_hour = dt_object.hour
                else:
                    utc_hour = -1 # Indicate unknown hour, will not trigger hour-specific rules

                # Ensure critical indicators are present
                if rsi is None or volume_ratio is None or price_change_pct is None:
                    return {"action": "HOLD", "confidence": 50, "rationale": "Missing critical indicators for evaluation."}

                # --- Strategy Rules for BUY actions ---
                # Primary goal: Avoid buying into extended, overbought momentum in KR market.

                # Initial filter for a potential BUY: positive but not excessive price change, and good volume.
                # We are avoiding price_change_pct < 1.0 (insufficient momentum) and price_change_pct >= 15.0 (too extended).
                # Volume ratio should indicate genuine interest.
                if 1.0 < price_change_pct < 15.0 and volume_ratio > 3.0:
                    # Default BUY conditions and rationale
                    action = "BUY"
                    confidence = 70 # Base confidence for a healthy momentum play
                    rationale = "Healthy momentum with good volume, considering a BUY."

                    # Adjustments based on RSI
                    if rsi < 30: # Potentially oversold bounce
                        # Past failures included a mislabeled "oversold" signal (RSI 48.5).
                        # True oversold bounce needs strong confirmation; treat with more caution.
                        confidence = 55 # Lower confidence due to higher uncertainty and past issues
                        rationale = "Potential rebound from oversold conditions (RSI < 30) with good volume, but caution advised."
                    elif 30 <= rsi < 60: # Ideal range for momentum entry: growing but not overbought
                        confidence = 80
                        rationale = "Strong buying signal: healthy momentum, robust volume, and RSI in optimal range."
                    elif 60 <= rsi < 70: # Approaching overbought, some room for continuation
                        confidence = 65
                        rationale = "Momentum continuation with good volume, but RSI is getting elevated. Monitoring required."
                    else: # rsi >= 70 - Overbought territory (where most past failures occurred)
                        # Crucial: Avoid buying in clearly overbought conditions to prevent buying the top.
                        action = "HOLD"
                        confidence = 40
                        rationale = f"RSI ({rsi:.2f}) indicates overbought conditions; avoiding new BUY entries to prevent buying the top."

                    # Market-specific adjustments for KR
                    if market == "KR":
                        # Apply stricter conditions and lower confidence for KR due to observed high failure rate
                        if action == "BUY": # Only apply if we're still considering a BUY
                            # Check for problematic early market hours (0-3 UTC / 9-12 KST) and just before open (23 UTC / 8 KST)
                            if 0 <= utc_hour <= 3 or utc_hour == 23:
                                # During these volatile hours, apply even stricter filters on momentum and RSI
                                if rsi >= 65 or price_change_pct >= 8.0: # Lower thresholds for 'too high' during these hours
                                    action = "HOLD"
                                    confidence = 45 # Very low confidence for such a risky setup
                                    rationale = f"KR market: Avoiding high momentum (RSI {rsi:.2f}, Price Change {price_change_pct:.2f}%) entry during volatile early hours ({utc_hour} UTC) due to high failure rate."
                                else: # If conditions are milder, still reduce confidence for KR during these hours
                                    confidence = max(50, confidence - 15) # Significantly reduce confidence
                                    rationale = f"KR market: Caution during early volatile hours ({utc_hour} UTC), reduced confidence for BUY. {rationale}"

                            # General confidence adjustment for KR market trades, even outside peak failure hours
                            if action == "BUY": # If still a BUY after time-based check
                                confidence = max(50, confidence - 10) # Generally reduce confidence for KR market buys
                                rationale = f"KR market: {rationale}" # Prefix rationale for clarity

                else: # If initial price change or volume conditions are not met
                    if price_change_pct <= 1.0:
                        rationale = "Price change too low for a momentum buy, indicating insufficient interest."
                    elif price_change_pct >= 15.0:
                        rationale = "Price change too high, stock is likely extended; avoiding chasing a parabolic move."
                    elif volume_ratio <= 3.0:
                        rationale = "Volume too low for a confident momentum play, indicating weak conviction."

                # For US markets (AMEX, NASDAQ), the failure rate was low. The general RSI-based filtering should be sufficient,
                # and no specific time-based restrictions are applied beyond the general rules.

                # Ensure confidence stays within valid bounds (0-100)
                confidence = max(0, min(100, confidence))

                return {"action": action, "confidence": confidence, "rationale": rationale}
