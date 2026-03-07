"""Auto-generated strategy: v20260306_220248

Generated at: 2026-03-06T22:02:48.290048+00:00
Rationale: Auto-evolved from 16 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -21963.33
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260306_220248(BaseStrategy):
    """Strategy: v20260306_220248"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            import datetime

            def evaluate(self, market_data: dict) -> dict:
                # Default to HOLD with low confidence
                action = "HOLD"
                confidence = 30
                rationale = "No strong signal or conditions are unfavorable."

                # Extract relevant data, handling potential missing keys to prevent errors
                current_price = market_data.get("input_data", {}).get("current_price")
                price_change_pct = market_data.get("input_data", {}).get("price_change_pct")
                market = market_data.get("market")

                # Scenario match data (e.g., from a scanner) provides indicators
                scenario_match = market_data.get("context_snapshot", {}).get("scenario_match", {})
                rsi = scenario_match.get("rsi")
                volume_ratio = scenario_match.get("volume_ratio")

                # Get the hour from the timestamp (UTC) for market timing analysis
                timestamp_str = market_data.get("timestamp")
                trade_hour_utc = None
                if timestamp_str:
                    try:
                        # Handle timestamps with or without 'Z' (Zulu time for UTC)
                        dt_object_utc = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        trade_hour_utc = dt_object_utc.hour
                    except ValueError:
                        # Log error or handle cases where timestamp parsing fails
                        pass # trade_hour_utc will remain None

                # --- Preliminary Data Validation ---
                # Ensure critical indicators are present before making a decision
                if rsi is None or volume_ratio is None or current_price is None or price_change_pct is None:
                    rationale = "Insufficient essential data (RSI, Volume Ratio, Price, Price Change %) for a robust decision."
                    return {"action": "HOLD", "confidence": 20, "rationale": rationale}

                # --- Analyze Failure Patterns & Apply Restrictions ---

                is_kr_market = (market == "KR")

                # Problematic hours for KRX based on failure patterns (00:00-03:00 UTC == 09:00-12:00 KST, plus 23:00 UTC == 08:00 KST pre-market)
                is_problematic_kr_hour = is_kr_market and trade_hour_utc in [0, 1, 2, 3, 23]

                # RSI thresholds based on historical failures (RSI > 70 consistently led to BUY losses)
                is_overbought_rsi = rsi > 70

                # Correct definition for truly oversold (Trade 5 failed with RSI 48.5 being mislabeled as oversold)
                is_truly_oversold = rsi < 30

                # --- Strategy Logic for BUYs ---

                # Base confidence for a strong BUY signal
                base_buy_confidence = 75 

                # Pattern 1 & 4: Avoiding Buying into Overbought Conditions (major failure pattern for all BUYs)
                if is_overbought_rsi: # This covers RSI > 70, which was a consistent failure point.
                    rationale = f"Avoiding BUY: RSI ({rsi:.2f}) is overbought. Past failures show this is a high-risk entry point, especially for BUYs into momentum."
                    return {"action": "HOLD", "confidence": 10, "rationale": rationale}

                # Pattern 5: Handle the "oversold rebound" scenario with corrected RSI definition
                # This addresses the misinterpretation from Trade 5 where RSI 48.5 was called "oversold".
                elif is_truly_oversold and volume_ratio > 3: # Require decent volume for a rebound confirmation
                    action = "BUY"
                    confidence = base_buy_confidence + 10 # Higher confidence for genuine oversold rebound
                    rationale = f"BUY: RSI ({rsi:.2f}) is truly oversold with strong volume ({volume_ratio:.2f}), indicating potential for rebound."

                    # Pattern 2 & 3: Adjust confidence based on market-specific (KR) and timing (problematic hour) risks
                    if is_kr_market:
                        confidence = max(45, confidence - 15) # Reduce confidence for KR market, but keep it actionable if truly oversold
                        rationale += " (Caution: KR market, adjusting confidence for historical risks.)"
                    if is_problematic_kr_hour:
                         confidence = max(40, confidence - 10) # Further reduce confidence if problematic hour for KR
                         rationale += " (Caution: Problematic KR trading hour.)"


                # Momentum BUY with healthy RSI range (50-70), avoiding overbought conditions
                elif 50 <= rsi <= 70 and \
                     volume_ratio > 2 and \
                     price_change_pct > 0: # Positive momentum and decent volume confirmation

                    action = "BUY"
                    confidence = base_buy_confidence
                    rationale = f"BUY: Strong momentum detected with healthy RSI ({rsi:.2f}) and sustained volume ({volume_ratio:.2f})."

                    # Pattern 2 & 3: Adjust confidence based on market-specific (KR) and timing (problematic hour) risks
                    if is_kr_market:
                        confidence = max(40, confidence - 15) # Reduce confidence for KR market
                        rationale += " (Caution: KR market.)"
                    if is_problematic_kr_hour:
                        # Significantly reduce confidence if problematic hour for KR, reflecting high failure rate at market open
                        confidence = max(35, confidence - 25) 
                        rationale += " (High risk due to problematic trading hour for KR market.)"

                    # Further caution if price_change_pct is very high, even if RSI is below 70, it might indicate a short-term peak or excessive volatility
                    if price_change_pct > 10:
                        confidence = max(40, confidence - 10)
                        rationale += " (Warning: High daily price change, potential for quick reversal.)"

                # --- Final Confidence Adjustment and Decision ---

                # If the action is still BUY, ensure it meets a minimum actionable confidence threshold.
                # This addresses Pattern 6: High confidence leading to losses, by requiring a robust confidence level.
                if action == "BUY" and confidence < 50: # Increased minimum confidence for actual BUYs
                    rationale = f"Adjusting to HOLD: Calculated confidence ({confidence}) is too low for a BUY signal given historical risks. Original rationale: {rationale}"
                    action = "HOLD"
                    confidence = 30 # Reset to default HOLD confidence for low-confidence decisions

                return {"action": action, "confidence": confidence, "rationale": rationale}
