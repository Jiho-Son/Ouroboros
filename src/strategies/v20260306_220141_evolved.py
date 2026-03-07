"""Auto-generated strategy: v20260306_220141

Generated at: 2026-03-06T22:01:41.787114+00:00
Rationale: Auto-evolved from 16 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -21963.33
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260306_220141(BaseStrategy):
    """Strategy: v20260306_220141"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            import datetime

            def evaluate(self, market_data: dict) -> dict:
                # Helper function to extract UTC hour from timestamp string
                def get_utc_hour_from_timestamp(timestamp_str: str) -> int:
                    try:
                        # Handle 'Z' for UTC timezone indicator
                        if timestamp_str.endswith('Z'):
                            dt_object = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        else:
                            dt_object = datetime.datetime.fromisoformat(timestamp_str)
                        return dt_object.hour
                    except (ValueError, TypeError):
                        self.logger.error(f"Failed to parse timestamp: {timestamp_str}")
                        return -1 # Indicate an error or invalid hour

                # Default decision - no action
                action = "HOLD"
                confidence = 0
                rationale = "No actionable signal based on current strategy or all signals filtered."

                # Extract common data points
                current_market = market_data.get('market')
                timestamp_str = market_data.get('timestamp')
                utc_hour = get_utc_hour_from_timestamp(timestamp_str)
                stock_code = market_data.get('stock_code', 'UNKNOWN_STOCK')

                # Extract indicators, prioritizing 'scenario_match' as it implies processed insights
                rsi = market_data.get('scenario_match', {}).get('rsi')
                volume_ratio = market_data.get('scenario_match', {}).get('volume_ratio')

                # Fallback to 'input_data' if indicators not found in 'scenario_match'
                if rsi is None:
                    rsi = market_data.get('input_data', {}).get('rsi')
                if volume_ratio is None:
                    volume_ratio = market_data.get('input_data', {}).get('volume_ratio')

                price_change_pct = market_data.get('input_data', {}).get('price_change_pct')

                # --- Hypothetical Original Strategy Logic (Simplified Simulation) ---
                # This section simulates how the strategy *might* have previously generated BUY signals,
                # which we will then evaluate and potentially override based on failure patterns.
                original_action = "HOLD"
                original_confidence = 0
                original_rationale = ""

                if rsi is not None and volume_ratio is not None and price_change_pct is not None:
                    # Pattern 1: Aggressive Momentum Buy (matches first 4 failed trades)
                    if rsi >= 70 and volume_ratio >= 5.0 and price_change_pct >= 5.0:
                        original_action = "BUY"
                        original_confidence = 85 # High confidence, as observed in failures
                        original_rationale = f"Detected strong momentum for {stock_code} (RSI {rsi:.2f}, VolRatio {volume_ratio:.2f}, PriceChange {price_change_pct:.2f}%)."
                    # Pattern 2: "Oversold" with high volume (matches failed Trade 5)
                    elif 40 <= rsi <= 60 and volume_ratio >= 10.0 and price_change_pct < 2.0:
                        original_action = "BUY"
                        original_confidence = 90 # Very high confidence, as observed in Trade 5
                        original_rationale = f"Identified {stock_code} as potential 'oversold' with neutral RSI {rsi:.2f} but extreme volume ratio {volume_ratio:.2f}, signaling a rebound opportunity."
                    # Generic Momentum Buy (if not captured by the above extreme cases)
                    elif 50 < rsi < 70 and volume_ratio > 2.0 and price_change_pct > 1.0:
                        original_action = "BUY"
                        original_confidence = 70
                        original_rationale = f"Detected positive momentum for {stock_code} (RSI {rsi:.2f}, VolRatio {volume_ratio:.2f}, PriceChange {price_change_pct:.2f}%)."

                # Initialize current decision with the hypothetical original strategy's decision
                action = original_action
                confidence = original_confidence
                rationale = original_rationale
                # --- End of Hypothetical Original Strategy Logic ---


                # --- Start of Failure Pattern Avoidance & Improvement Logic ---

                # Only apply these filters if the hypothetical original strategy decided to BUY
                if action == "BUY":
                    # 1. Market Specific Conditions: KR Market Failure Patterns
                    if current_market == "KR":
                        # Problematic Timing for KR Market BUYs (UTC 23:00 - 03:59, which is KST 08:00 - 12:59)
                        # This covers all identified problematic hours: 23, 0, 1, 2, 3 UTC
                        if utc_hour >= 0 and utc_hour <= 3 or utc_hour == 23:
                            action = "HOLD"
                            confidence = max(0, confidence - 80) # Drastically reduce confidence, effectively a HOLD
                            rationale = f"AVOID BUY for {stock_code} in KR market during problematic early/mid-day hours (UTC {utc_hour}:00) due to high failure rate. Original signal: {original_rationale}"
                            if rsi is not None and rsi >= 70: # Further caution if overbought within this risky window
                                confidence = 5
                                rationale += " RSI is also elevated, increasing reversal risk."

                        # Momentum Trap Avoidance for KR (High RSI, High Price Change, High Volume Ratio)
                        # This rule targets the pattern of buying into already extended moves.
                        if rsi is not None and price_change_pct is not None and volume_ratio is not None:
                            # Direct match to the first 4 failed trades (very high overbought momentum)
                            if rsi >= 75 and price_change_pct >= 8.0 and volume_ratio >= 5.0:
                                action = "HOLD"
                                confidence = 5
                                rationale = f"AVOID BUY for {stock_code} in KR market: Detected strong momentum trap (RSI:{rsi:.2f}, PriceChange:{price_change_pct:.2f}%, VolRatio:{volume_ratio:.2f}). Historically, buying into such extended moves in KR has led to significant losses. Original signal: {original_rationale}"
                            # Less extreme but still risky overbought conditions, reduce confidence if still a BUY
                            elif rsi >= 70 and price_change_pct >= 5.0 and volume_ratio >= 3.0:
                                # Only reduce confidence if we still plan to BUY and confidence is high
                                if action == "BUY" and confidence > 40:
                                    confidence = min(confidence, 40) 
                                    rationale = f"REDUCED CONFIDENCE for BUY for {stock_code} in KR market due to elevated RSI ({rsi:.2f}), price change ({price_change_pct:.2f}%) and volume ratio ({volume_ratio:.2f}), indicating potential overextension. Original signal: {original_rationale}"

                            # Neutral RSI with extremely high volume and minimal price gain (matches failed Trade 5)
                            # This pattern indicates distribution or a failed rebound rather than a strong BUY signal.
                            if 40 <= rsi <= 60 and volume_ratio >= 10.0 and price_change_pct < 2.0:
                                action = "HOLD"
                                confidence = 5
                                rationale = f"AVOID BUY for {stock_code} in KR market: RSI ({rsi:.2f}) is neutral but volume ratio ({volume_ratio:.2f}) is extremely high with minimal price gain ({price_change_pct:.2f}%). This pattern previously led to losses, possibly indicating distribution or a failed rebound. Original signal: {original_rationale}"

                    # 2. General RSI Guardrail for BUYs (applies to all markets, acts as a final sanity check)
                    if action == "BUY" and rsi is not None:
                        if rsi > 85: # Extremely overbought, very high risk
                            confidence = min(confidence, 20) # Severely reduce confidence
                            rationale = f"{rationale.strip('. ')}. WARNING: RSI ({rsi:.2f}) is extremely overbought, indicating high risk of immediate reversal. Confidence severely reduced."
                        elif rsi > 75: # Moderately overbought, high risk
                            confidence = min(confidence, 35) # Reduce confidence
                            rationale = f"{rationale.strip('. ')}. CAUTION: RSI ({rsi:.2f}) is significantly overbought. Risk of pullback. Confidence reduced."

                        # For US markets, apply a slightly less aggressive confidence reduction based on the few failures.
                        # While KR is heavily filtered, US only has 1 failure each, so general RSI caution is sufficient.
                        elif current_market in ["US_AMEX", "US_NASDAQ"]:
                            if rsi > 70:
                                confidence = min(confidence, 60) # Modestly reduce confidence
                                rationale = f"{rationale.strip('. ')}. CAUTION: RSI ({rsi:.2f}) is elevated in US market. Reduced confidence due to general overbought risk."

                # Ensure confidence is within valid range [0, 100]
                confidence = max(0, min(100, confidence))

                return {"action": action, "confidence": confidence, "rationale": rationale}
