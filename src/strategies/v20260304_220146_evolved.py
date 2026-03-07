"""Auto-generated strategy: v20260304_220146

Generated at: 2026-03-04T22:01:46.182617+00:00
Rationale: Auto-evolved from 10 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -16219.82
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260304_220146(BaseStrategy):
    """Strategy: v20260304_220146"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            action = "HOLD"
            confidence = 50  # Default neutral confidence
            rationale = "No strong signal or avoiding known failure patterns."

            # Extract relevant data from market_data
            market = market_data.get("market")
            timestamp_str = market_data.get("timestamp")

            # Parse timestamp to get the hour in UTC
            try:
                dt_object = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                hour = dt_object.hour
            except (ValueError, TypeError):
                # Fallback if timestamp is malformed, treat as non-peak hour to be safe
                hour = -1 

            price_change_pct = market_data.get("input_data", {}).get("price_change_pct")

            scenario_match = market_data.get("context_snapshot", {}).get("scenario_match", {})
            rsi = scenario_match.get("rsi")
            volume_ratio = scenario_match.get("volume_ratio")

            # Ensure all necessary data points are available for analysis
            if any(x is None for x in [price_change_pct, rsi, volume_ratio]):
                return {"action": action, "confidence": confidence, "rationale": "Insufficient data for evaluation."}

            # --- Failure Pattern Avoidance Rules ---

            # 1. Avoid KRX Market Open Volatility (UTC hour 0-1 corresponds to 9 AM - 11 AM KST)
            # This period showed the highest number of failures in the KR market.
            if market == "KR" and hour in [0, 1]:
                rationale = "Avoiding high-volatility KRX market open period (UTC hours 0-1) due to historical poor performance."
                return {"action": action, "confidence": 40, "rationale": rationale} # Lower confidence for active avoidance

            # 2. Avoid Chasing Extreme Momentum
            # Many failures involved buying into stocks that had already surged significantly (e.g., >20%)
            # with high volume, indicating potential for immediate reversals or profit-taking.
            if price_change_pct > 15.0 and volume_ratio > 10.0:
                rationale = "Avoiding chasing extreme momentum (large price surge with high volume), likely buying the top."
                return {"action": action, "confidence": 30, "rationale": rationale}

            # 3. Avoid False 'Oversold' Rebound Attempts
            # Sample failures show 'oversold' rationales for RSI values in the 40s-50s (neutral range),
            # combined with negative price changes, suggesting attempts to catch falling knives.
            if 30 <= rsi <= 50 and volume_ratio > 4.0 and price_change_pct < 0:
                rationale = "Avoiding false 'oversold' signals (RSI in neutral range) with negative price change; potential falling knife."
                return {"action": action, "confidence": 35, "rationale": rationale}

            # --- Improved BUY Conditions (after passing failure pattern checks) ---

            # Condition 1: Genuine Oversold Rebound (RSI < 30)
            # This aims to capture actual oversold conditions, unlike the previously misidentified ones.
            if rsi < 30:
                if volume_ratio > 2.0 and price_change_pct < 0: # Needs some trading interest and still declining slightly
                    action = "BUY"
                    confidence = 85
                    rationale = f"BUY: Genuine oversold condition (RSI < 30) with supportive volume and potential for rebound. RSI={rsi:.2f}, VolumeRatio={volume_ratio:.2f}."

                    if market == "KR":
                        confidence = 70 # Adjust confidence for KR market due to higher uncertainty
                        rationale += " (Confidence adjusted for KR market due to historical patterns)."
                    return {"action": action, "confidence": confidence, "rationale": rationale}

            # Condition 2: Strong Momentum Continuation (not initial spike)
            # This targets stocks showing healthy positive momentum that is likely to continue,
            # avoiding the trap of buying into unsustainable, extreme spikes.
            if 50 < rsi < 70: # RSI in bullish to moderately overbought zone, suggesting strength
                if 2.0 < price_change_pct < 10.0: # Significant positive change but not extreme
                    if volume_ratio > 5.0: # Strong volume to confirm momentum
                        action = "BUY"
                        confidence = 90
                        rationale = f"BUY: Strong momentum continuation with healthy RSI, moderate price increase, and high volume. RSI={rsi:.2f}, PriceChange={price_change_pct:.2f}%, VolumeRatio={volume_ratio:.2f}."

                        if market == "KR":
                            confidence = 75 # Adjust confidence for KR market
                            rationale += " (Confidence adjusted for KR market due to historical patterns)."
                        return {"action": action, "confidence": confidence, "rationale": rationale}

            # Condition 3: General Positive Momentum (for other markets or less extreme conditions)
            # A more general signal, but still requiring positive indicators, with lower confidence.
            if price_change_pct > 0.5 and volume_ratio > 3.0 and rsi > 40:
                action = "BUY"
                confidence = 65
                rationale = f"BUY: General positive momentum with price increase and volume. RSI={rsi:.2f}, PriceChange={price_change_pct:.2f}%, VolumeRatio={volume_ratio:.2f}."
                # No specific KR adjustment here as it passed initial KR timing filter.
                return {"action": action, "confidence": confidence, "rationale": rationale}

            # If no BUY conditions are met, and no explicit failure pattern was hit, default to HOLD
            return {"action": action, "confidence": confidence, "rationale": rationale}
