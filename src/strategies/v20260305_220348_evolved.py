"""Auto-generated strategy: v20260305_220348

Generated at: 2026-03-05T22:03:48.287240+00:00
Rationale: Auto-evolved from 12 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -20176.1
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260305_220348(BaseStrategy):
    """Strategy: v20260305_220348"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            action = "HOLD"
            confidence = 0
            rationale = "No strong signal or conditions not met."

            # Extract relevant data with default safe values
            market = market_data.get("market")
            timestamp_str = market_data.get("timestamp")

            # Use context_snapshot for scenario_match indicators like RSI and volume_ratio
            # and L1 for current_price and foreigner_net
            context_snapshot = market_data.get("context_snapshot", {})
            l1_data = context_snapshot.get("L1", {})
            scenario_match_data = context_snapshot.get("scenario_match", {})

            current_price = l1_data.get("current_price")
            foreigner_net = l1_data.get("foreigner_net") # Could be useful for KRX
            rsi = scenario_match_data.get("rsi")
            volume_ratio = scenario_match_data.get("volume_ratio")

            # input_data typically contains immediate price changes
            input_data = market_data.get("input_data", {})
            price_change_pct = input_data.get("price_change_pct")

            # --- Initial Data Validation ---
            if not all([market, timestamp_str, current_price, price_change_pct is not None, rsi is not None, volume_ratio is not None]):
                rationale = "Missing essential market data points for evaluation."
                self.log(f"Skipping trade due to missing data: {rationale}")
                return {"action": "HOLD", "confidence": 0, "rationale": rationale}

            # --- Time-based Filtering for KR market ---
            try:
                # Handle 'Z' suffix for UTC timestamps
                dt_object = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                utc_hour = dt_object.hour
            except ValueError:
                rationale = "Invalid timestamp format encountered."
                self.log(f"Skipping trade due to invalid timestamp: {rationale}")
                return {"action": "HOLD", "confidence": 0, "rationale": rationale}

            # Identified failure pattern: KR market during opening hours (00:00-02:00 UTC / 09:00-11:00 KST)
            if market == "KR" and 0 <= utc_hour <= 2:
                rationale = f"Avoiding KR market during high volatility opening hours ({utc_hour:02d}:00 UTC)."
                self.log(rationale)
                return {"action": "HOLD", "confidence": 0, "rationale": rationale}

            # --- Base Confidence Adjustment based on Market ---
            # KR market showed significantly more failures. US markets (AMEX, NASDAQ) had fewer.
            base_signal_confidence = 60 # Starting point for a potential BUY signal
            if market == "KR":
                base_signal_confidence -= 15 # Apply a penalty for general KR market trades
            elif market in ["US_NASDAQ", "US_AMEX"]:
                base_signal_confidence += 5 # Slight bonus for less problematic markets

            proposed_action = "BUY"
            proposed_confidence = base_signal_confidence
            proposed_rationale_parts = []

            # --- Refined BUY Conditions to avoid identified failure patterns ---

            # Pattern 1: Buying into overbought conditions / extreme momentum traps (e.g., Trade 1, 3)
            if rsi > 70:
                rationale = f"Rejected BUY: RSI ({rsi:.2f}) is overbought. Avoiding momentum traps."
                self.log(rationale)
                return {"action": "HOLD", "confidence": 0, "rationale": rationale}

            if price_change_pct > 10: # Significant single-day surge
                rationale = f"Rejected BUY: Price already surged {price_change_pct:.2f}%. Avoiding buying at local peaks."
                self.log(rationale)
                return {"action": "HOLD", "confidence": 0, "rationale": rationale}

            # Pattern 2: Misinterpretation of "oversold" or weak reversal signals (e.g., Trade 2, 4, 5)
            # Previous strategy bought with neutral RSI, claiming "oversold".

            # Strategy for genuinely oversold rebound
            if rsi < 30: # Genuinely oversold territory
                if volume_ratio > 5 and price_change_pct < -1: # Significant volume on a dip, but not too extreme
                    # We need to see signs of stabilization or very early reversal, not just a plunge
                    # Example failures show negative PNL, suggesting current 'oversold' logic isn't working
                    # A cautious approach here is required.
                    proposed_confidence += 20
                    proposed_rationale_parts.append(f"Cautious BUY: RSI ({rsi:.2f}) indicates oversold, with notable volume ({volume_ratio:.2f}x) on a dip ({price_change_pct:.2f}%).")
                    # To reduce risk, we might want to wait for positive price_change_pct, but for short-term bounce, might enter on dip.
                    # Given past failures, we must be very careful.
                    if foreigner_net is not None and market == "KR" and foreigner_net < 0:
                        # Foreigner selling in KRX can indicate further weakness
                        proposed_confidence -= 10
                        proposed_rationale_parts.append("Foreigner net selling in KR market adds caution.")

                else:
                    rationale = f"Rejected BUY: RSI low ({rsi:.2f}) but lacking strong volume confirmation (>5x) or insufficient price dip (actual: {price_change_pct:.2f}%)."
                    self.log(rationale)
                    return {"action": "HOLD", "confidence": 0, "rationale": rationale}

            # Strategy for balanced momentum/breakout (neutral RSI)
            elif 40 <= rsi <= 60: # Neutral RSI range
                if volume_ratio > 7 and price_change_pct > 0.5 and price_change_pct < 8: # Strong volume with positive, moderate price movement
                    # This suggests a healthy upward trend or breakout confirmation, not an exhausted spike.
                    proposed_confidence += 25
                    proposed_rationale_parts.append(f"Momentum BUY: Neutral RSI ({rsi:.2f}), strong volume ({volume_ratio:.2f}x), moderate positive price action ({price_change_pct:.2f}%).")
                else:
                    rationale = f"Rejected BUY: Neutral RSI ({rsi:.2f}) without sufficient positive price action or strong volume. Lacking clear breakout confirmation (volume={volume_ratio:.2f}x, price_change={price_change_pct:.2f}%)."
                    self.log(rationale)
                    return {"action": "HOLD", "confidence": 0, "rationale": rationale}

            # Catch-all for other RSI ranges or unhandled scenarios
            else: # RSI between 30-40 (lower neutral), or 60-70 (upper neutral)
                rationale = f"Rejected BUY: RSI ({rsi:.2f}) is in a less optimal range or conditions are not clearly met for a BUY signal."
                self.log(rationale)
                return {"action": "HOLD", "confidence": 0, "rationale": rationale}

            # --- Final Decision and Confidence Adjustment ---
            if proposed_rationale_parts:
                # Ensure confidence is within bounds
                confidence = max(0, min(100, proposed_confidence))

                # Additional confidence check: Only execute BUY if confidence is sufficiently high
                # This threshold should be higher than the average confidence of failed trades (84.0).
                # A threshold of 85-90 makes sense to be stricter.
                if confidence >= 85: 
                    action = proposed_action
                    rationale = " ".join(proposed_rationale_parts) + f" (Market: {market}, Hour: {utc_hour:02d}:00 UTC)"
                else:
                    rationale = f"Proposed BUY signal too weak (confidence {confidence:.0f} < 85). Holding for stronger conviction."
                    confidence = 0
                    action = "HOLD"
            else:
                action = "HOLD"
                confidence = 0
                rationale = "No valid BUY signal generated by the strategy after filtering."

            return {"action": action, "confidence": confidence, "rationale": rationale}
