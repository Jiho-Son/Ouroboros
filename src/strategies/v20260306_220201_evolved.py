"""Auto-generated strategy: v20260306_220201

Generated at: 2026-03-06T22:02:01.968965+00:00
Rationale: Auto-evolved from 16 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -21963.33
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260306_220201(BaseStrategy):
    """Strategy: v20260306_220201"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            import datetime

            # Helper function to get the current hour in UTC from a timestamp string
            def _get_utc_hour(timestamp_str):
                dt_object = datetime.datetime.fromisoformat(timestamp_str)
                return dt_object.hour

            # Helper to get KST hour for KR market specifically (KST = UTC+9)
            def _get_kst_hour(timestamp_str):
                dt_object = datetime.datetime.fromisoformat(timestamp_str)
                kst_dt = dt_object + datetime.timedelta(hours=9)
                return kst_dt.hour

            def evaluate(self, market_data: dict) -> dict:
                """
                Evaluates market data to generate a trading decision, avoiding identified failure patterns
                and implementing an improved strategy.

                Args:
                    market_data (dict): A dictionary containing various market and context information.
                        Expected keys include 'market', 'timestamp', 'context_snapshot', 'input_data'.

                Returns:
                    dict: A dictionary with 'action' (BUY/SELL/None), 'confidence' (0-100), and 'rationale'.
                """

                action = None
                confidence = 0
                rationale = "No strong signal or avoiding known failure patterns."

                market = market_data.get('market')
                timestamp_str = market_data.get('timestamp')

                # Basic data validation for essential fields
                if not all([market, timestamp_str]):
                    return {"action": None, "confidence": 0, "rationale": "Missing basic market or timestamp data."}

                utc_hour = _get_utc_hour(timestamp_str)

                # Extract technical indicators and context data, handling potential missing keys gracefully
                rsi = market_data.get('context_snapshot', {}).get('scenario_match', {}).get('rsi')
                volume_ratio = market_data.get('context_snapshot', {}).get('scenario_match', {}).get('volume_ratio')
                price_change_pct = market_data.get('input_data', {}).get('price_change_pct')

                # Prioritize 'foreigner_net' from L1 if available, otherwise from input_data
                foreigner_net = market_data.get('context_snapshot', {}).get('L1', {}).get('foreigner_net')
                if foreigner_net is None:
                    foreigner_net = market_data.get('input_data', {}).get('foreigner_net')

                # --- 1. Avoid identified failure patterns ---

                # Pattern 1 & 3: KR Market Open Volatility Avoidance
                # The majority of failures (14/16) were BUY trades in the KR market,
                # specifically concentrated between 23:00 UTC and 03:00 UTC. This corresponds to
                # pre-market (08:00 KST) and early trading hours (09:00 KST - 12:00 KST) of the KRX.
                # This filter explicitly prevents BUY actions during this historically problematic window.
                if market == 'KR':
                    if utc_hour in [23, 0, 1, 2, 3]:
                        return {
                            "action": None,
                            "confidence": 0,
                            "rationale": f"Avoiding BUY in KR market during highly volatile opening/pre-market hours (UTC {utc_hour}:00 - {utc_hour+1}:00). Past BUY failures are heavily concentrated in this period."
                        }

                # Pattern 2 & 4: Avoid Buying into Overbought Conditions (especially with high confidence)
                # Most failed BUYs had high RSI (often > 70-80) and strong price momentum, despite the original
                # strategy assigning high confidence (average 84.69). This indicates chasing unsustainable momentum.

                # Check for presence of key indicators before proceeding with signal generation
                if any(val is None for val in [rsi, volume_ratio, price_change_pct]):
                    return {"action": None, "confidence": 0, "rationale": "Missing critical technical indicators (RSI, Volume Ratio, Price Change Percentage) for decision."}

                # --- 2. Implement improved BUY strategy based on filtered conditions ---

                # Define a base level of confidence if a potential BUY signal is detected
                base_confidence = 60 

                # Conditions for a potential *valid* BUY, revised to be safer:
                # 1. Momentum: price_change_pct should be positive, but not excessively high to avoid chasing pumps.
                # 2. Volume: volume_ratio indicates increased interest, suggesting liquidity and conviction.
                # 3. RSI: Should indicate strength but *not* be overbought to allow for further upside.
                # 4. Market-specific conditions: positive foreigner_net for KR can be a supportive factor.

                # Minimum thresholds for considering a BUY signal to ensure a reasonable setup
                min_price_change_pct = 1.0  # At least 1% price increase to confirm upward momentum
                min_volume_ratio = 2.0      # At least 2x average volume to confirm significant interest

                # Only consider a BUY if there's sufficient momentum and volume
                if price_change_pct > min_price_change_pct and volume_ratio > min_volume_ratio:

                    # Handle KR market specific conditions and adjustments (higher failure rate historically)
                    if market == 'KR':
                        # Stricter RSI threshold for KR market to prevent buying into overbought conditions
                        if rsi < 65: # RSI below 65 indicates strong but not extremely overbought conditions
                            action = "BUY"
                            # Boost confidence if there is positive foreigner net buying, as this is a key factor in KRX
                            if foreigner_net is not None and foreigner_net > 0:
                                confidence = base_confidence + 15 
                                rationale = f"BUY signal: Moderate momentum ({price_change_pct:.2f}%), healthy RSI (<65, current {rsi:.2f}), strong volume ({volume_ratio:.2f}x) in KR. Positive foreigner net support. Avoiding known failure patterns."
                            else:
                                confidence = base_confidence + 5
                                rationale = f"BUY signal: Moderate momentum ({price_change_pct:.2f}%), healthy RSI (<65, current {rsi:.2f}), strong volume ({volume_ratio:.2f}x) in KR. Avoiding known failure patterns."

                            # Cap the confidence for KR market lower than US due to its higher historical failure rate
                            confidence = min(confidence, 70) 

                        else: # RSI is 65 or higher for KR market
                            rationale = f"Avoiding BUY in KR: RSI ({rsi:.2f}) is too high (>=65). Past failures indicate buying into overbought conditions is problematic."
                            confidence = 0 # Explicitly set to 0 to prevent action

                    # Handle US market specific conditions and adjustments (lower failure rate historically)
                    elif market in ['US_AMEX', 'US_NASDAQ']:
                        # Slightly more lenient RSI for US markets, but still cautious
                        if rsi < 70: # RSI below 70 is still strong but allows more room than KRX
                            action = "BUY"
                            confidence = base_confidence + 10
                            rationale = f"BUY signal: Moderate momentum ({price_change_pct:.2f}%), healthy RSI (<70, current {rsi:.2f}), strong volume ({volume_ratio:.2f}x) in US. Avoiding known failure patterns."

                            # Cap the confidence for US markets
                            confidence = min(confidence, 75) 
                        else: # RSI is 70 or higher for US market
                            rationale = f"Avoiding BUY in US: RSI ({rsi:.2f}) is too high (>=70). Past failures indicate buying into overbought conditions is problematic."
                            confidence = 0 # Explicitly set to 0 to prevent action

                    else:
                        # For any other market not explicitly handled, default to no action
                        rationale = "Market not specifically covered by improved strategy or missing conditions for BUY."
                        confidence = 0
                        action = None # Ensure no action if market not handled
                else:
                    # If momentum or volume thresholds are not met, no BUY signal is generated
                    rationale = "Insufficient price momentum or volume ratio for a BUY signal."
                    confidence = 0

                return {
                    "action": action,
                    "confidence": confidence,
                    "rationale": rationale
                }
