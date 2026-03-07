"""Auto-generated strategy: v20260306_220226

Generated at: 2026-03-06T22:02:26.600155+00:00
Rationale: Auto-evolved from 16 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -21963.33
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260306_220226(BaseStrategy):
    """Strategy: v20260306_220226"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            # Helper function to parse UTC hour from timestamp
            def _get_utc_hour(timestamp_str: str) -> int:
                try:
                    # Handle 'Z' for UTC timezone
                    dt_object = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    return dt_object.hour
                except ValueError:
                    self.logger.error(f"Failed to parse timestamp: {timestamp_str}")
                    return -1 # Indicate an error or unknown hour

            # Default action, confidence, and rationale
            action = "HOLD"
            confidence = 0
            rationale = "No actionable pattern identified, or filtered due to risk."

            # Extract relevant data from market_data
            market = market_data.get("market")
            timestamp_str = market_data.get("timestamp")

            # Access indicators from context_snapshot as they were crucial in failure rationales
            scenario_match = market_data["context_snapshot"].get("scenario_match", {})
            rsi = scenario_match.get("rsi", 0.0)
            volume_ratio = scenario_match.get("volume_ratio", 0.0)

            # Access other data from input_data
            input_data = market_data.get("input_data", {})
            current_price = input_data.get("current_price", 0.0)
            price_change_pct = input_data.get("price_change_pct", 0.0)
            foreigner_net = input_data.get("foreigner_net", 0.0) # Not directly in failure patterns, but useful context

            utc_hour = _get_utc_hour(timestamp_str) if timestamp_str else -1

            # --- 1. Avoid identified failure patterns ---

            # Pattern A: High failure rate for KR market during specific early/late UTC hours.
            # These hours (0-3 UTC, 23 UTC) correspond to the very early KRX trading session
            # or just before market open, where momentum buys consistently failed.
            # This is the strongest pattern, so it's a strict filter.
            if market == "KR" and utc_hour in [0, 1, 2, 3, 23]:
                rationale = (f"AVOIDING BUY: KR market trade at UTC hour {utc_hour}. "
                             f"Historical data shows high failure rate for momentum buys during these volatile periods.")
                return {"action": "HOLD", "confidence": 10, "rationale": rationale}

            # Pattern B: All failures were BUY actions, primarily in scenarios of buying into
            # already extended, often overbought, momentum. The `avg_confidence` for these
            # failing trades was high (84.69), indicating a need to re-calibrate confidence
            # and introduce stricter overbought filters.

            # Strict filter for extremely overbought conditions:
            # Combining high RSI, significant price jump, and very high volume ratio often led to reversals.
            # The failures showed RSI often > 75, price_change_pct > 10%, volume_ratio > 8.
            if rsi > 78 and price_change_pct > 10.0 and volume_ratio > 10.0:
                rationale = (f"AVOIDING BUY: Extreme overbought conditions (RSI: {rsi:.2f}, "
                             f"PriceChange: {price_change_pct:.2f}%, VolRatio: {volume_ratio:.2f}) "
                             f"mirroring past failures. High likelihood of reversal.")
                return {"action": "HOLD", "confidence": 15, "rationale": rationale}

            # Also, filter aggressive momentum even if not extremely overbought, but still risky
            if rsi > 70 and price_change_pct > 15.0: # Very high price change combined with high RSI
                 rationale = (f"AVOIDING BUY: High RSI ({rsi:.2f}) and very high price surge ({price_change_pct:.2f}%) "
                              f"suggests exhaustion. High risk of reversal.")
                 return {"action": "HOLD", "confidence": 20, "rationale": rationale}

            # The "oversold" rationale in Sample 5 with RSI 48.5 also failed, combined with high volume ratio.
            # This suggests buying on "high volume after perceived dip" is problematic if RSI isn't truly oversold.
            if rsi < 50 and rsi > 35 and volume_ratio > 8.0 and price_change_pct > 0.0:
                rationale = (f"AVOIDING BUY: Potential 'false oversold' scenario (RSI: {rsi:.2f} not truly low) "
                             f"with high volume ({volume_ratio:.2f}) and positive price change. "
                             f"Similar patterns led to losses.")
                return {"action": "HOLD", "confidence": 25, "rationale": rationale}


            # --- 2. Generate an improved BUY strategy with adjusted confidence ---
            # Assume the core strategy is still looking for momentum buys, but with much stricter entry criteria.
            # The average confidence of failed trades was high, so we need to be more conservative.

            # Base confidence for a potentially valid BUY signal.
            # Start lower than the previous average failure confidence and adjust upwards for strong signals.
            proposed_confidence = 60
            proposed_rationale = ""
            is_buy_candidate = False

            # Condition 1: Healthy momentum (strong but not overbought)
            # RSI in a good uptrend range, good volume, reasonable price increase.
            if 50 <= rsi <= 70 and 2.0 <= volume_ratio <= 8.0 and 2.0 <= price_change_pct <= 10.0:
                is_buy_candidate = True
                proposed_confidence += 15 # Boost for safer conditions
                proposed_rationale = (f"BUY: Healthy momentum confirmed (RSI: {rsi:.2f}, VolRatio: {volume_ratio:.2f}, "
                                      f"PriceChange: {price_change_pct:.2f}).")

                # Market-specific adjustment: US markets had fewer failures, allow slightly higher confidence here.
                if market in ["US_NASDAQ", "US_AMEX"]:
                    proposed_confidence += 5
                    proposed_rationale += " US market showing strong continued momentum."

            # Condition 2: Strong momentum, but approaching overbought (requires higher confidence threshold)
            # This is riskier, so base confidence should be lower, and it's less preferred.
            elif 70 < rsi <= 75 and 8.0 < volume_ratio <= 12.0 and 8.0 < price_change_pct <= 12.0:
                is_buy_candidate = True
                proposed_confidence = max(50, proposed_confidence - 10) # Reduce confidence for riskier entry
                proposed_rationale = (f"BUY: Strong momentum (RSI: {rsi:.2f}, VolRatio: {volume_ratio:.2f}, "
                                      f"PriceChange: {price_change_pct:.2f}). Approaching overbought, proceed with caution.")

                # Further penalize KR market for this riskier range due to its high failure rate.
                if market == "KR":
                    proposed_confidence = max(35, proposed_confidence - 15)
                    proposed_rationale += " (Further caution for KR market due to historical volatility in this range)."
                    # If confidence drops too low for KR in this range, revert to HOLD
                    if proposed_confidence < 45:
                        is_buy_candidate = False
                        proposed_rationale = (f"HOLD: Filtered out risky KR momentum buy (RSI: {rsi:.2f}) "
                                              f"due to historical KR market failures.")

            # Condition 3: Genuine oversold rebound (RSI truly low) with volume confirmation.
            # This addresses the sample 5 failure by being stricter on 'oversold'.
            elif rsi < 30 and volume_ratio > 3.0: # Ensure RSI is genuinely low
                is_buy_candidate = True
                proposed_confidence += 20 # Higher confidence for a true oversold rebound
                proposed_rationale = (f"BUY: Genuine oversold rebound signal (RSI: {rsi:.2f}) "
                                      f"with confirming volume ({volume_ratio:.2f}).")
                if market == "KR":
                    proposed_confidence = max(40, proposed_confidence - 10)
                    proposed_rationale += " (Caution for KR market, even on oversold plays)."


            # --- Final Decision & Confidence Adjustment ---
            if is_buy_candidate:
                action = "BUY"
                confidence = proposed_confidence
                rationale = proposed_rationale

            # Ensure confidence is within a reasonable range (e.g., 0-100)
            confidence = max(0, min(100, confidence))

            # If a BUY action was decided but confidence is still too low after all adjustments, revert to HOLD.
            # A threshold around 50-60 is typically minimum for actual execution.
            if action == "BUY" and confidence < 55:
                action = "HOLD"
                rationale = (f"HOLD: BUY signal was generated but confidence ({confidence}) is too low "
                             f"after risk adjustments and historical performance considerations.")
                confidence = 40 # Indicate it was a near miss for a BUY

            # If no BUY action, or explicitly held, ensure confidence reflects that
            if action == "HOLD" and confidence == 0:
                confidence = 10 # Minimal confidence for a neutral action

            return {"action": action, "confidence": confidence, "rationale": rationale}
