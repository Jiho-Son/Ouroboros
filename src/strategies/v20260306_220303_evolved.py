"""Auto-generated strategy: v20260306_220303

Generated at: 2026-03-06T22:03:03.151453+00:00
Rationale: Auto-evolved from 16 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -21963.33
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260306_220303(BaseStrategy):
    """Strategy: v20260306_220303"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            action = "HOLD"
            confidence = 0
            rationale = "No strong signal or avoiding known failure patterns."

            # Extract relevant data from market_data
            market = market_data.get("market")
            action_candidate = market_data.get("action", "HOLD") # The action recommended by the underlying model
            confidence_candidate = market_data.get("confidence", 0) # The confidence from the underlying model
            rationale_candidate = market_data.get("rationale", "")

            current_price = market_data.get("input_data", {}).get("current_price")
            price_change_pct = market_data.get("input_data", {}).get("price_change_pct")
            timestamp_str = market_data.get("timestamp")

            # Scenario match details often critical for decision logic
            scenario_match = market_data.get("context_snapshot", {}).get("scenario_match", {})
            rsi = scenario_match.get("rsi")
            volume_ratio = scenario_match.get("volume_ratio")

            # Convert timestamp to datetime object for hour extraction
            trade_hour_utc = None
            if timestamp_str:
                try:
                    # Handle 'Z' suffix for UTC timestamps
                    trade_datetime_utc = datetime.datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    trade_hour_utc = trade_datetime_utc.hour
                except ValueError:
                    # If timestamp parsing fails, proceed without hour-based filtering
                    pass

            # --- Strategy Logic ---

            # 1. Focus on improving BUY trades, as all failures were BUYs.
            if action_candidate == "BUY":

                # --- Failure Pattern Avoidance ---

                # 1.1. KR Market specific restrictions (most failures occurred here)
                if market == "KR":
                    # Avoid BUYing during historically volatile and failure-prone hours for KR market (UTC)
                    # Failures concentrated around 0, 1, 2, 3, 23 UTC.
                    if trade_hour_utc is not None and trade_hour_utc in [0, 1, 2, 3, 23]:
                        rationale = f"Avoiding KR market BUY during historically volatile hours ({trade_hour_utc} UTC)."
                        return {"action": "HOLD", "confidence": 0, "rationale": rationale}

                    # Avoid BUYing into overbought conditions in KR (failed trades had RSI > 70, often > 80)
                    if rsi is not None and rsi > 68: # Set a stricter upper limit for RSI in KR
                        rationale = f"Avoiding KR market BUY due to overbought RSI ({rsi})."
                        return {"action": "HOLD", "confidence": 0, "rationale": rationale}

                    # Filter out "rebound" plays with low RSI and minimal price movement (like the failed 48.5 RSI trade)
                    if rsi is not None and rsi < 50 and price_change_pct is not None and price_change_pct < 1.0:
                        rationale = f"Avoiding KR market BUY for low RSI ({rsi}) and minimal price movement ({price_change_pct}%), indicating a false rebound signal or lack of clear trend."
                        return {"action": "HOLD", "confidence": 0, "rationale": rationale}

                # 1.2. General overbought conditions (applies to all markets)
                # Even for US markets, avoid extreme overbought conditions which often lead to reversals.
                if rsi is not None and rsi > 72: # Slightly higher threshold than KR-specific, but still cautious
                    rationale = f"Avoiding BUY due to general overbought RSI ({rsi})."
                    return {"action": "HOLD", "confidence": 0, "rationale": rationale}

                # 1.3. Avoid buying into extended, high-momentum spikes that might be exhausting
                # Failed trades often showed high price_change_pct (e.g., >10%) and very high volume_ratio (e.g., >8)
                if price_change_pct is not None and volume_ratio is not None and \
                   price_change_pct > 10.0 and volume_ratio > 8.0:
                    rationale = f"Avoiding BUY due to significant price surge ({price_change_pct}%) and high volume ratio ({volume_ratio}), potentially indicating a temporary peak or exhaustion."
                    return {"action": "HOLD", "confidence": 0, "rationale": rationale}

                # --- If candidate BUY action passes filters, use it but adjust confidence ---

                action = action_candidate
                confidence = confidence_candidate
                rationale = rationale_candidate

                # Adjust confidence based on market-specific historical performance
                # Reduce confidence for KR market trades even if they pass initial filters, as they've been riskier.
                if market == "KR":
                    confidence = max(0, confidence - 15) # Apply a more significant confidence reduction for KR
                    rationale += " (Adjusted confidence down for KR market's higher failure rate)."

                # Require a minimum adjusted confidence for execution
                if confidence >= 70: # Set a new minimum confidence for actual execution
                    return {"action": action, "confidence": confidence, "rationale": rationale}
                else:
                    return {"action": "HOLD", "confidence": 0, "rationale": f"Candidate BUY signal had insufficient confidence ({confidence}) after adjustments and filtering, reverting to HOLD."}

            # For non-BUY actions (e.g., SELL, HOLD), pass them through as-is,
            # as the failure analysis was exclusively on BUY trades.
            return {"action": action_candidate, "confidence": confidence_candidate, "rationale": rationale_candidate}
