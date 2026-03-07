"""Auto-generated strategy: v20260303_220407

Generated at: 2026-03-03T22:04:07.709924+00:00
Rationale: Auto-evolved from 9 failures. Primary failure markets: ['KR', 'US_NASDAQ']. Average loss: -17998.5
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260303_220407(BaseStrategy):
    """Strategy: v20260303_220407"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            # Default to no action or original proposal, to be modified based on analysis
            proposed_action = market_data.get("action", "HOLD")
            proposed_confidence = market_data.get("confidence", 0)
            proposed_rationale = market_data.get("rationale", "No specific rationale for this trade.")

            # Extract relevant data points for analysis
            market = market_data.get("market")
            timestamp_str = market_data.get("timestamp")

            # Parse timestamp to get hour in UTC
            try:
                timestamp_dt = datetime.datetime.fromisoformat(timestamp_str)
                utc_hour = timestamp_dt.hour
            except (ValueError, TypeError):
                # Handle cases where timestamp might be missing or malformed
                utc_hour = -1 # Indicate unknown hour, apply general caution

            # Indicators from context_snapshot and input_data
            rsi = market_data.get("context_snapshot", {}).get("scenario_match", {}).get("rsi")
            volume_ratio = market_data.get("context_snapshot", {}).get("scenario_match", {}).get("volume_ratio")
            price_change_pct = market_data.get("input_data", {}).get("price_change_pct")
            foreigner_net = market_data.get("input_data", {}).get("foreigner_net")

            # --- Strategy Adjustments Based on Failure Patterns ---

            # 1. High failure rate for BUY actions in KR market, especially at market open (00 UTC / 09 KST)
            if market == "KR" and proposed_action == "BUY":
                # Start with a slight general reduction for KR BUYs given their high failure rate
                adjusted_confidence = proposed_confidence * 0.9 
                proposed_rationale_updates = ["(Strategy adjusted for KR BUY patterns.)"]

                # Specific checks for problematic hours
                if utc_hour == 0: # 09:00 KST - Highest failure hour
                    adjusted_confidence *= 0.15 # Drastically reduce confidence for market open BUYs
                    proposed_rationale_updates.append("Significant penalty: KR BUY at market open (00 UTC/09 KST) has highest failure rate.")

                    # If rationale claims "oversold" but RSI is not truly oversold (RSI >= 30)
                    if rsi is not None and rsi >= 30 and "oversold" in proposed_rationale.lower():
                        adjusted_confidence = min(adjusted_confidence, 15) # Cap confidence very low
                        proposed_rationale_updates.append("'Oversold' rationale invalid with neutral RSI.")

                    # If high volume ratio is present with momentum-like signals at open
                    if volume_ratio is not None and volume_ratio >= 10: # Based on sample values (10.53, 11.085, 11.975)
                        # If price is already up significantly, it might be an exhaustion gap or chasing
                        if price_change_pct is not None and price_change_pct >= 5: 
                            adjusted_confidence = min(adjusted_confidence, 10) # Cap confidence extremely low
                            proposed_rationale_updates.append("High volume and significant price surge at KR open may indicate a bull trap.")
                        else: # Even without a huge price surge, high volume at open is risky
                             adjusted_confidence *= 0.3 # Further reduce
                             proposed_rationale_updates.append("High volume ratio detected at KR market open.")

                elif utc_hour == 2: # 11:00 KST - Another failure hour
                    adjusted_confidence *= 0.4 # Reduce confidence for this hour
                    proposed_rationale_updates.append("Reduced confidence: KR BUY at 11:00 KST (02 UTC) showed elevated failure rate.")
                    if rsi is not None and rsi >= 30 and "oversold" in proposed_rationale.lower():
                        adjusted_confidence = min(adjusted_confidence, 25)
                        proposed_rationale_updates.append("'Oversold' rationale invalid with neutral RSI.")

                elif utc_hour == 14: # 23:00 KST - Market closed. This hour led to a failure for KR.
                    adjusted_confidence = 0 # Prevent trades at this hour for KR
                    proposed_action = "HOLD"
                    proposed_rationale_updates.append("Action changed to HOLD: KR trading decisions at 23:00 KST (14 UTC) identified as a failure pattern.")

                # General filters for KR BUYs, regardless of hour (less severe than specific hours)
                if rsi is not None and rsi >= 30 and "oversold" in proposed_rationale.lower():
                    # If 'oversold' is claimed but RSI is not truly < 30 (e.g., <30 for oversold), it's a weak signal.
                    adjusted_confidence = min(adjusted_confidence, proposed_confidence * 0.5) 
                    proposed_rationale_updates.append("'Oversold' claim disputed by neutral RSI.")

                if volume_ratio is not None and volume_ratio >= 5 and price_change_pct is not None and price_change_pct > 5:
                    # Catching strong momentum moves that might be extended or near reversal.
                    adjusted_confidence = min(adjusted_confidence, proposed_confidence * 0.4) 
                    proposed_rationale_updates.append("High volume ratio with significant price increase may indicate late entry risk.")

                # If foreigner_net is neutral (0.0) and it's a momentum play
                # (lack of foreign institutional conviction can make speculative plays riskier)
                if foreigner_net == 0.0 and (volume_ratio is not None and volume_ratio >= 5) and (price_change_pct is not None and price_change_pct > 0):
                    adjusted_confidence = min(adjusted_confidence, proposed_confidence * 0.6)
                    proposed_rationale_updates.append("Neutral foreigner net with speculative momentum BUY; added caution.")

                # If adjusted_confidence drops below a trading threshold (e.g., 40), change action to HOLD.
                if adjusted_confidence < 40 and proposed_action != "HOLD":
                    proposed_action = "HOLD"
                    proposed_confidence = 0 # No confidence for a HOLD
                    proposed_rationale_updates.append("Confidence too low for BUY, adjusted to HOLD.")
                else:
                    proposed_confidence = adjusted_confidence
                    # Cap max confidence to acknowledge past issues, even if original was very high
                    # This prevents overconfidence in potentially risky KR BUY scenarios.
                    proposed_confidence = min(proposed_confidence, 70) 

            # 2. US_NASDAQ failure (only one recorded, but apply caution if similar conditions apply)
            elif market == "US_NASDAQ" and proposed_action == "BUY":
                adjusted_confidence = proposed_confidence # Start with original confidence
                proposed_rationale_updates = []

                # Check for problematic US market open (14 UTC is 9 AM EST)
                if utc_hour == 14: 
                     adjusted_confidence *= 0.7 # Slight reduction for market open in US, less severe than KR
                     proposed_rationale_updates.append("Minor caution: US_NASDAQ BUY at market open (14 UTC) had one past failure.")

                     if rsi is not None and rsi >= 30 and "oversold" in proposed_rationale.lower():
                        adjusted_confidence = min(adjusted_confidence, 40)
                        proposed_rationale_updates.append("'Oversold' rationale invalid with neutral RSI for US market.")

                     if volume_ratio is not None and volume_ratio >= 10 and price_change_pct is not None and price_change_pct >= 5:
                        adjusted_confidence = min(adjusted_confidence, 30)
                        proposed_rationale_updates.append("High volume ratio and significant price surge at US open detected.")

                     if adjusted_confidence < 40 and proposed_action != "HOLD":
                        proposed_action = "HOLD"
                        proposed_confidence = 0
                        proposed_rationale_updates.append("Confidence too low for US BUY, adjusted to HOLD.")
                     else:
                        proposed_confidence = adjusted_confidence
                        # Cap US confidence slightly higher than KR, but still cap to prevent overconfidence
                        proposed_confidence = min(proposed_confidence, 85)

                # If there were updates, append them
                if proposed_rationale_updates:
                    proposed_rationale += " " + " ".join(proposed_rationale_updates)

            # Ensure confidence is within a valid range [0, 100] and round it
            proposed_confidence = max(0, min(100, round(proposed_confidence)))

            return {
                "action": proposed_action,
                "confidence": proposed_confidence,
                "rationale": proposed_rationale,
            }
