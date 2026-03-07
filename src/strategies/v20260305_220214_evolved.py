"""Auto-generated strategy: v20260305_220214

Generated at: 2026-03-05T22:02:14.639187+00:00
Rationale: Auto-evolved from 12 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -20176.1
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260305_220214(BaseStrategy):
    """Strategy: v20260305_220214"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            import datetime

            def evaluate(self, market_data: dict) -> dict:
                # Default to no action
                action = "HOLD"
                confidence = 0
                rationale = "No suitable trading opportunity found based on current strategy rules."

                market = market_data.get("market")
                timestamp_str = market_data.get("timestamp")
                stock_code = market_data.get("stock_code", "UNKNOWN")

                # Safely extract data, providing defaults
                input_data = market_data.get("input_data", {})
                current_price = input_data.get("current_price")
                price_change_pct = input_data.get("price_change_pct")

                scenario_match = market_data.get("context_snapshot", {}).get("scenario_match", {})
                rsi = scenario_match.get("rsi")
                volume_ratio = scenario_match.get("volume_ratio")

                # --- Strategy Constants and Thresholds ---
                # These constants are derived from analyzing the failure patterns.

                # 1. Avoid Chasing Extreme Momentum (Pattern 1: "Buying the Top")
                MAX_PRICE_CHANGE_FOR_BUY = 12.0  # Avoid buying stocks that have already surged >12%
                                                 # (failures at 17.34% and 25.72% were observed)
                MAX_RSI_FOR_BUY = 70.0           # Avoid buying overbought stocks (failures at RSI 84.68, so 70 is safer)

                # 2. Rethink "Oversold" Criteria (Pattern 2: "Buying Falling Knives / Misidentified Oversold")
                MIN_RSI_FOR_GENUINE_OVERSOLD = 30.0 # Truly oversold condition (failures at RSI ~48-50 were mislabeled)
                NEUTRAL_RSI_LOWER_BOUND = 35.0      # RSI above this and below 65 is generally considered neutral, not oversold

                # 3. Minimum Volume Requirement
                MIN_VOLUME_RATIO_FOR_BUY = 3.0   # Require decent volume for any trade to indicate liquidity/interest

                # 4. Confidence Adjustments based on historical performance (Pattern 3: High Confidence, Low Accuracy)
                BASE_BUY_CONFIDENCE = 70         # Adjusted lower than average failure confidence (84)
                KR_MARKET_PENALTY = 15           # Significant penalty for KR market (10/12 failures in KR)

                EARLY_KR_HOURS_START_UTC = 0     # KRX market open (00:00 UTC)
                EARLY_KR_HOURS_END_UTC = 3       # Covers 0, 1, 2 UTC (majority of KR failures occurred here)
                EARLY_KR_HOURS_PENALTY = 20      # Strong penalty for early KR market hours (7/12 failures at 0 UTC)

                RISKY_MOMENTUM_CONFIDENCE_REDUCTION = 10 # If a momentum trade is near its upper bounds
                OVERSOLD_BOUNCE_CONFIDENCE_BONUS = 5 # If genuinely oversold and showing potential for bounce/reversal

                # --- Pre-Checks for basic data availability ---
                if any(v is None for v in [current_price, price_change_pct, rsi, volume_ratio]):
                    return {"action": "HOLD", "confidence": 0, "rationale": f"[{stock_code}] Missing crucial market data (price, price_change_pct, RSI, Volume Ratio)."}

                # --- Parse timestamp for hour-based adjustments ---
                current_utc_hour = None
                try:
                    dt_object = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    current_utc_hour = dt_object.hour
                except (ValueError, TypeError):
                    # In a production system, this would be logged. For now, hour-based adjustment is skipped if parsing fails.
                    pass

                # --- Initial Filters based on Failure Patterns ---

                # Filter 1: Insufficient Volume (common denominator in many trades)
                if volume_ratio < MIN_VOLUME_RATIO_FOR_BUY:
                    return {"action": "HOLD", "confidence": 0, "rationale": f"[{stock_code}] Volume ratio {volume_ratio:.2f} is too low (below {MIN_VOLUME_RATIO_FOR_BUY}) for a confident trade."}

                # Filter 2: Buying the Top (Chasing extreme momentum)
                if price_change_pct > MAX_PRICE_CHANGE_FOR_BUY:
                    return {"action": "HOLD", "confidence": 0, "rationale": f"[{stock_code}] Avoiding BUY: Price already surged {price_change_pct:.2f}% (exceeds {MAX_PRICE_CHANGE_FOR_BUY}% threshold). Potential momentum trap."}

                if rsi > MAX_RSI_FOR_BUY:
                    return {"action": "HOLD", "confidence": 0, "rationale": f"[{stock_code}] Avoiding BUY: RSI {rsi:.2f} indicates overbought conditions (exceeds {MAX_RSI_FOR_BUY} threshold). Potential top."}

                # Filter 3: Buying Falling Knives / Misidentified Oversold
                # If stock is currently down, but not genuinely oversold, it's a "falling knife" or weak rebound attempt.
                if price_change_pct < 0 and rsi > NEUTRAL_RSI_LOWER_BOUND:
                    return {"action": "HOLD", "confidence": 0, "rationale": f"[{stock_code}] Avoiding BUY: Stock is down {price_change_pct:.2f}% but RSI {rsi:.2f} is not genuinely oversold (below {MIN_RSI_FOR_GENUINE_OVERSOLD}). Potential falling knife or misidentified bounce."}

                # --- Determine BUY conditions if initial filters are passed ---
                current_trade_confidence = BASE_BUY_CONFIDENCE
                trade_rationale_parts = []

                is_momentum_buy_candidate = price_change_pct > 0
                is_oversold_bounce_candidate = rsi <= MIN_RSI_FOR_GENUINE_OVERSOLD

                if is_momentum_buy_candidate and not is_oversold_bounce_candidate:
                    # Scenario 1: Momentum Buy (within acceptable limits)
                    action = "BUY"
                    trade_rationale_parts.append(f"Momentum BUY: Price up {price_change_pct:.2f}% with RSI {rsi:.2f}.")

                    # Adjust confidence if momentum is strong but approaching limits
                    if price_change_pct > (MAX_PRICE_CHANGE_FOR_BUY * 0.75) or rsi > (MAX_RSI_FOR_BUY * 0.9):
                         current_trade_confidence -= RISKY_MOMENTUM_CONFIDENCE_REDUCTION
                         trade_rationale_parts.append("Caution: Momentum near upper bounds.")

                elif is_oversold_bounce_candidate:
                    # Scenario 2: Genuine Oversold Bounce/Reversal Buy
                    # This covers cases where RSI is genuinely oversold, regardless of current price change (could be starting to reverse).
                    action = "BUY"
                    current_trade_confidence += OVERSOLD_BOUNCE_CONFIDENCE_BONUS
                    if price_change_pct > 0:
                        trade_rationale_parts.append(f"Oversold Reversal BUY: Price up {price_change_pct:.2f}% from genuinely oversold RSI {rsi:.2f}.")
                    else:
                        trade_rationale_parts.append(f"Oversold Bounce BUY: Price down/flat {price_change_pct:.2f}% with genuinely oversold RSI {rsi:.2f}.")

                else:
                    # If no specific BUY scenario met after filtering
                    return {"action": "HOLD", "confidence": 0, "rationale": f"[{stock_code}] No specific BUY condition met after initial filtering."}

                # --- Market and Time Specific Adjustments (for confirmed BUY actions) ---
                if action == "BUY": # This check ensures adjustments only apply if a BUY was decided
                    if market == "KR":
                        current_trade_confidence -= KR_MARKET_PENALTY
                        trade_rationale_parts.append(f"Adjusted for KR market caution (confidence reduced by {KR_MARKET_PENALTY}).")

                        if current_utc_hour is not None and EARLY_KR_HOURS_START_UTC <= current_utc_hour < EARLY_KR_HOURS_END_UTC:
                            current_trade_confidence -= EARLY_KR_HOURS_PENALTY
                            trade_rationale_parts.append(f"Adjusted for early KR market hours ({current_utc_hour} UTC) (confidence reduced by {EARLY_KR_HOURS_PENALTY}).")

                            # If confidence drops too low due to these specific risky conditions, convert to HOLD
                            # A conservative threshold to prevent risky trades in known failure windows
                            if current_trade_confidence < BASE_BUY_CONFIDENCE - KR_MARKET_PENALTY - EARLY_KR_HOURS_PENALTY + 10: 
                                return {"action": "HOLD", "confidence": 0, "rationale": f"[{stock_code}] High risk (KR market, early hours, very low confidence: {current_trade_confidence}) - opting to HOLD."}

                    # Ensure confidence does not go below 0 or exceed 100
                    current_trade_confidence = max(0, min(100, current_trade_confidence))

                    return {
                        "action": action,
                        "confidence": current_trade_confidence,
                        "rationale": f"[{stock_code}] {' '.join(trade_rationale_parts)} Current Vol Ratio: {volume_ratio:.2f}."
                    }

                # This point should theoretically not be reached if the logic above is exhaustive for BUY/HOLD
                return {"action": "HOLD", "confidence": 0, "rationale": f"[{stock_code}] Undetermined trade scenario fallback."}
