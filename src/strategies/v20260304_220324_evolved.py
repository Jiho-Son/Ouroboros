"""Auto-generated strategy: v20260304_220324

Generated at: 2026-03-04T22:03:24.817798+00:00
Rationale: Auto-evolved from 10 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -16219.82
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260304_220324(BaseStrategy):
    """Strategy: v20260304_220324"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            import datetime

            # --- Strategy Constants ---
            # Standard RSI thresholds
            OVERSOLD_RSI_THRESHOLD = 30  # Truly oversold condition (e.g., RSI < 30)
            NEUTRAL_RSI_LOWER = 40       # RSI below this might be 'weak' but not 'oversold'
            NEUTRAL_RSI_UPPER = 60       # RSI above this might be 'strong' but not 'overbought'
            OVERBOUGHT_RSI_THRESHOLD = 70 # Avoid buying into overbought conditions

            # Momentum thresholds
            MOMENTUM_CHASING_PRICE_CHANGE_PCT_MAX = 7.0 # Max positive price change to consider buying
            MIN_PRICE_CHANGE_FOR_MOMENTUM = 0.5         # Min positive price change for a momentum buy

            # Volume thresholds
            REQUIRED_VOLUME_RATIO_MIN = 3.0 # Minimum volume ratio for any entry

            # Market-specific adjustments
            # KRX market open (0 UTC) often sees initial volatility. Avoid entering during this window.
            KRX_MARKET_OPEN_VOLATILITY_WINDOW_MINUTES = 15 

            # Confidence adjustments
            BASE_CONFIDENCE = 60 # Starting confidence for a potential BUY
            CONFIDENCE_BOOST_STRONG_SIGNAL = 20 # Boost for clear, strong indicator signals
            CONFIDENCE_BOOST_CONFIRMED_SIGNAL = 10 # Boost for secondary confirming indicators (e.g., foreigner net)
            CONFIDENCE_PENALTY_KR_HIGH_FAILURE = 15 # Penalty due to high historical failure rate in KR market
            CONFIDENCE_PENALTY_MISLEADING_RSI = 10 # Penalty if "oversold" claim but RSI is neutral/high

            # Extract relevant data from market_data
            market = market_data.get("market")
            timestamp_str = market_data.get("timestamp")

            # Default action, confidence, and rationale
            action = "HOLD"
            confidence = 0
            rationale_parts = [] # Use a list to build rationale for clarity

            # Parse timestamp to check for market open timing
            timestamp = datetime.datetime.fromisoformat(timestamp_str)
            current_hour_utc = timestamp.hour
            current_minute_utc = timestamp.minute

            # Extract context_snapshot and input_data
            context_snapshot = market_data.get("context_snapshot", {})
            input_data = market_data.get("input_data", {})

            # Extract key indicators
            rsi = context_snapshot.get("scenario_match", {}).get("rsi")
            volume_ratio = context_snapshot.get("scenario_match", {}).get("volume_ratio")
            price_change_pct = input_data.get("price_change_pct")
            foreigner_net = input_data.get("foreigner_net") # Positive foreigner_net suggests institutional interest

            # --- Failure Pattern Avoidance Rules ---

            # 1. Avoid KR market open volatility
            # KRX market open is 00:00 UTC (9 AM KST). The strategy failed repeatedly around this time.
            if market == "KR" and current_hour_utc == 0 and current_minute_utc < KRX_MARKET_OPEN_VOLATILITY_WINDOW_MINUTES:
                return {
                    "action": "HOLD",
                    "confidence": 0,
                    "rationale": (f"Avoiding KRX market open volatility during the first "
                                  f"{KRX_MARKET_OPEN_VOLATILITY_WINDOW_MINUTES} minutes (0 UTC).")
                }

            # 2. Avoid chasing extreme momentum
            # Previous failures included buying stocks already up significantly (>20%).
            if price_change_pct is not None and price_change_pct > MOMENTUM_CHASING_PRICE_CHANGE_PCT_MAX:
                return {
                    "action": "HOLD",
                    "confidence": 0,
                    "rationale": (f"Avoiding chase of extreme momentum: stock already up {price_change_pct:.2f}% "
                                  f"which is above the {MOMENTUM_CHASING_PRICE_CHANGE_PCT_MAX:.1f}% threshold.")
                }

            # 3. Ensure sufficient volume for liquidity and signal strength
            if volume_ratio is None or volume_ratio < REQUIRED_VOLUME_RATIO_MIN:
                return {
                    "action": "HOLD",
                    "confidence": 0,
                    "rationale": (f"Insufficient volume ratio ({volume_ratio if volume_ratio is not None else 'N/A'}). "
                                  f"Minimum required: {REQUIRED_VOLUME_RATIO_MIN:.1f}x.")
                }

            # --- Improved BUY Signal Evaluation ---

            current_confidence = BASE_CONFIDENCE

            # Adjust confidence for KR market due to historical high failure rate
            if market == "KR":
                current_confidence -= CONFIDENCE_PENALTY_KR_HIGH_FAILURE
                rationale_parts.append("KR market buy, confidence adjusted for historical high failure rate. ")

            is_oversold_buy_candidate = False
            is_momentum_buy_candidate = False

            # Evaluate "Oversold" opportunity (correct RSI definition)
            # Previous strategy incorrectly labeled RSI ~50 as oversold.
            if rsi is not None and rsi <= OVERSOLD_RSI_THRESHOLD:
                is_oversold_buy_candidate = True
                current_confidence += CONFIDENCE_BOOST_STRONG_SIGNAL
                rationale_parts.append(f"Identified as truly oversold (RSI: {rsi:.2f}). ")

                # Additional confirmation for KR: positive foreigner net buying
                if market == "KR" and foreigner_net is not None and foreigner_net > 0:
                    current_confidence += CONFIDENCE_BOOST_CONFIRMED_SIGNAL
                    rationale_parts.append("Confirmed by positive foreigner net buying. ")

            # Evaluate "Controlled Momentum" opportunity
            # Avoids chasing overly extended stocks but capitalizes on healthy upward movement.
            elif price_change_pct is not None and MIN_PRICE_CHANGE_FOR_MOMENTUM <= price_change_pct <= MOMENTUM_CHASING_PRICE_CHANGE_PCT_MAX:
                # Check RSI is in a healthy, not overbought, range for a momentum entry.
                if rsi is None or (NEUTRAL_RSI_LOWER <= rsi <= OVERBOUGHT_RSI_THRESHOLD):
                    is_momentum_buy_candidate = True
                    current_confidence += CONFIDENCE_BOOST_STRONG_SIGNAL
                    rationale_parts.append(f"Positive, controlled momentum detected (Price Change: {price_change_pct:.2f}%). ")

                    # Additional confirmation for KR: positive foreigner net buying
                    if market == "KR" and foreigner_net is not None and foreigner_net > 0:
                        current_confidence += CONFIDENCE_BOOST_CONFIRMED_SIGNAL
                        rationale_parts.append("Confirmed by positive foreigner net buying. ")
                else:
                    # Momentum detected, but RSI is problematic (e.g., too high for fresh entry, or too low for pure momentum)
                    rationale_parts.append(f"Momentum detected but RSI ({rsi:.2f}) is not ideal (e.g., overbought or too low). ")
                    current_confidence -= CONFIDENCE_PENALTY_MISLEADING_RSI # Penalize for ambiguous RSI
            else:
                # No strong oversold or controlled momentum signals met.
                rationale_parts.append(f"No strong oversold (RSI <= {OVERSOLD_RSI_THRESHOLD}) or controlled momentum "
                                       f"({MIN_PRICE_CHANGE_FOR_MOMENTUM:.1f}% <= price change <= {MOMENTUM_CHASING_PRICE_CHANGE_PCT_MAX:.1f}%) "
                                       f"signal. (RSI: {rsi if rsi is not None else 'N/A':.2f}, Price Change: {price_change_pct if price_change_pct is not None else 'N/A':.2f}). ")

            # Final decision based on refined signals
            if is_oversold_buy_candidate or is_momentum_buy_candidate:
                action = "BUY"
                # Cap confidence at 100 and ensure it's not below 0
                confidence = min(100, max(0, current_confidence))
                rationale = "BUY decision based on improved strategy: " + "".join(rationale_parts).strip()
            else:
                action = "HOLD"
                confidence = 0
                rationale = "HOLD decision: " + "".join(rationale_parts).strip() + " Conditions for a BUY not met or unfavorable."

            return {
                "action": action,
                "confidence": int(confidence),
                "rationale": rationale
            }
