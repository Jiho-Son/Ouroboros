"""Auto-generated strategy: v20260305_220300

Generated at: 2026-03-05T22:03:00.632149+00:00
Rationale: Auto-evolved from 12 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -20176.1
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260305_220300(BaseStrategy):
    """Strategy: v20260305_220300"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            import datetime

            # This is the method body for the evaluate function.
            # It assumes existence of 'self' (e.g., inheriting from BaseStrategy).
            def evaluate(self, market_data: dict) -> dict:
                # --- Extract relevant data with robust error handling ---
                market = market_data.get('market')

                input_data = market_data.get('input_data', {})
                current_price = input_data.get('current_price')
                price_change_pct = input_data.get('price_change_pct')

                scenario_match = market_data.get('scenario_match', {})
                rsi = scenario_match.get('rsi')
                volume_ratio = scenario_match.get('volume_ratio')

                timestamp_str = market_data.get('timestamp')
                hour_utc = None
                if timestamp_str:
                    try:
                        dt_object = datetime.datetime.fromisoformat(timestamp_str)
                        hour_utc = dt_object.hour
                    except ValueError:
                        # Log error if timestamp format is unexpected, but proceed
                        # In a real system, logging would go here.
                        pass

                # --- Define thresholds and rules based on failure analysis ---
                # RSI thresholds to prevent buying into overbought and correctly identify oversold
                RSI_OVERBOUGHT_VERY_HIGH = 80  # Critical level to avoid (e.g., original trade with RSI 84.68)
                RSI_OVERBOUGHT_HIGH = 70      # General overbought, caution needed
                RSI_OVERSOLD_LOW = 30         # Genuinely oversold (original strategy misidentified 48-50 as oversold)
                RSI_NEUTRAL_UPPER = 60        # Healthy upper range for momentum
                RSI_NEUTRAL_LOWER = 40        # Healthy lower range for momentum

                # Volume ratio thresholds
                MIN_VOLUME_RATIO_MOMENTUM = 3.0 # Higher volume for confirmed momentum
                MIN_VOLUME_RATIO_REBOUND = 2.0  # Still requires decent volume for rebound

                # Price change percentage thresholds
                MIN_MOMENTUM_PCT_ENTRY = 0.5    # Minimum positive movement for momentum buy
                MAX_MOMENTUM_PCT_CHASE = 10.0   # Avoid chasing extreme spikes (e.g., 17%, 25% surges)
                # For rebound, allow slight negative to slight positive change, indicating stabilization or reversal start
                MAX_PRICE_CHANGE_FOR_REBOUND_ENTRY = 1.0 
                MIN_PRICE_CHANGE_FOR_REBOUND_ENTRY = -1.0 

                # KR Market Specifics: identified as a high-failure market, especially at open
                KR_MARKET_OPEN_UTC_START = 0 # 00:00 UTC = 09:00 KST
                KR_MARKET_OPEN_UTC_END = 2   # 02:00 UTC = 11:00 KST (initial volatile period)

                # --- Default Action ---
                action = 'HOLD'
                confidence = 0
                rationale = "No actionable signal based on current strategy rules."

                # --- Initial Filters & Safeguards (Avoiding known failure patterns) ---

                # 1. Avoid KR market during its volatile opening hours (09:00 - 11:00 KST)
                # Many failures occurred in KR around 00:00-02:00 UTC.
                if market == "KR" and hour_utc is not None and KR_MARKET_OPEN_UTC_START <= hour_utc <= KR_MARKET_OPEN_UTC_END:
                    return {'action': 'HOLD', 'confidence': 0, 'rationale': f"Avoiding KR market during volatile opening hours (UTC {hour_utc}:00)."}

                # 2. Insufficient data check
                if current_price is None or price_change_pct is None or rsi is None or volume_ratio is None:
                    return {'action': 'HOLD', 'confidence': 0, 'rationale': "Insufficient data points (price, price_change_pct, RSI, or volume_ratio) for a decision."}

                # 3. Avoid buying into extremely overbought conditions and massive price surges (Failure Trades 1 & 3)
                # The strategy previously bought into 17% and 25% surges with RSI 84.68.
                if rsi >= RSI_OVERBOUGHT_VERY_HIGH and price_change_pct > MAX_MOMENTUM_PCT_CHASE:
                    return {'action': 'HOLD', 'confidence': 0, 'rationale': f"Avoiding extremely overbought stock (RSI {rsi:.2f}) with significant prior price surge ({price_change_pct:.2f}%). High risk of reversal."}

                # --- Core BUY Signals ---

                # Momentum Buy Strategy (Refined to avoid chasing peaks and ensure healthy entry)
                # This addresses issues from Trade 1 (too high RSI/surge) and Trade 4 (negative price change for momentum).
                if (price_change_pct >= MIN_MOMENTUM_PCT_ENTRY and         # Requires positive price movement
                    price_change_pct < MAX_MOMENTUM_PCT_CHASE and          # Avoids chasing overly extended rallies
                    rsi >= RSI_NEUTRAL_LOWER and rsi < RSI_OVERBOUGHT_HIGH and # RSI in a healthy, not overbought range (40-70)
                    volume_ratio >= MIN_VOLUME_RATIO_MOMENTUM):            # Strong volume confirms momentum

                    action = 'BUY'
                    confidence = 80 # Base confidence for a strong, confirmed momentum signal
                    rationale = f"Strong upward momentum (price change {price_change_pct:.2f}%) with healthy RSI ({rsi:.2f}) and high volume ({volume_ratio:.2f}x)."

                    # Adjust confidence based on market: KR market had high failure rate.
                    if market == "KR":
                        # Significantly reduce confidence for KR market buys, but keep it above a floor for actionability
                        confidence = max(50, confidence - 20) 
                        rationale += " (Confidence adjusted for KR market risk)."

                    return {'action': action, 'confidence': confidence, 'rationale': rationale}

                # Oversold Rebound Strategy (Corrected interpretation and entry criteria)
                # This addresses issues from Trade 2 & 5 (misidentified neutral RSI as oversold, bought falling knives).
                if (rsi <= RSI_OVERSOLD_LOW and                             # Genuinely oversold RSI (<=30)
                    volume_ratio >= MIN_VOLUME_RATIO_REBOUND and           # Still requires decent volume for a potential bounce
                    price_change_pct >= MIN_PRICE_CHANGE_FOR_REBOUND_ENTRY and  # Price has stabilized or is showing slight reversal
                    price_change_pct <= MAX_PRICE_CHANGE_FOR_REBOUND_ENTRY):    # Not falling rapidly, or just started to tick up

                    action = 'BUY'
                    confidence = 70 # Base confidence for a rebound play (generally riskier than confirmed momentum)
                    rationale = f"Oversold condition (RSI {rsi:.2f}) with signs of stabilization/reversal (price change {price_change_pct:.2f}%) and solid volume ({volume_ratio:.2f}x)."

                    # Adjust confidence based on market
                    if market == "KR":
                        confidence = max(40, confidence - 20) # Further reduce confidence for KR market rebound plays
                        rationale += " (Confidence adjusted for KR market risk)."

                    return {'action': action, 'confidence': confidence, 'rationale': rationale}

                return {'action': action, 'confidence': confidence, 'rationale': rationale}
