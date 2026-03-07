"""Auto-generated strategy: v20260304_220234

Generated at: 2026-03-04T22:02:34.663763+00:00
Rationale: Auto-evolved from 10 failures. Primary failure markets: ['KR', 'US_AMEX', 'US_NASDAQ']. Average loss: -16219.82
"""

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class Strategy_v20260304_220234(BaseStrategy):
    """Strategy: v20260304_220234"""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
            import datetime

            def evaluate(self, market_data: dict) -> dict:
                # Default state: no action, neutral confidence, and a generic rationale
                action = "NO_ACTION"
                confidence = 50
                rationale = "No actionable signal detected."

                # Extract relevant data from the market_data dictionary
                market = market_data.get('market')
                timestamp_str = market_data.get('timestamp')
                current_price = market_data.get('input_data', {}).get('current_price')
                price_change_pct = market_data.get('input_data', {}).get('price_change_pct')
                foreigner_net = market_data.get('input_data', {}).get('foreigner_net') # Can be 0.0, especially for KR samples

                # Extract scenario_match data (indicators from previous analysis/scanner)
                scenario_match = market_data.get('context_snapshot', {}).get('scenario_match', {})
                rsi = scenario_match.get('rsi')
                volume_ratio = scenario_match.get('volume_ratio')

                # Parse UTC hour from timestamp for market timing analysis
                try:
                    dt_object = datetime.datetime.fromisoformat(timestamp_str)
                    hour_utc = dt_object.hour
                except (ValueError, TypeError):
                    hour_utc = -1 # Sentinel value if timestamp parsing fails

                # --- 1. FAILURE PATTERN AVOIDANCE ---
                # A clear pattern emerged: 80% of failures were BUYs in the KR market,
                # predominantly during UTC hours 0, 1, 2 (which corresponds to KST 9-11 AM, market open).
                # All 10 reported failures were BUYs.
                # Given the 100% failure rate for BUYs under these specific conditions,
                # the most crucial improvement is to avoid these trades entirely.
                if market == "KR" and hour_utc in [0, 1, 2]:
                    return {
                        "action": "NO_ACTION",
                        "confidence": 10, # Very low confidence, indicating a deliberate avoidance
                        "rationale": f"Avoiding BUY in KR market during early trading hours (UTC {hour_utc}) due to severe historical losses and 100% failure rate for BUY actions in this specific timeframe and market."
                    }

                # --- 2. EVALUATE POTENTIAL BUY OPPORTUNITIES (Outside of identified failure patterns) ---
                # If not falling into the immediate failure avoidance pattern,
                # evaluate for new BUY opportunities with stricter criteria.

                buy_confidence_score = 0
                buy_rationale_parts = []

                # Signal A: Strong Positive Price Momentum
                # Require a significant price increase, avoiding trades based on minor fluctuations.
                if price_change_pct is not None and price_change_pct > 3.0: 
                    buy_confidence_score += 30
                    buy_rationale_parts.append(f"Strong price momentum (+{price_change_pct:.2f}%)")
                elif price_change_pct is not None and price_change_pct > 1.0: 
                    buy_confidence_score += 15
                    buy_rationale_parts.append(f"Moderate price momentum (+{price_change_pct:.2f}%)")

                # Signal B: High Volume Ratio
                # High volume confirms genuine interest and conviction behind the price movement.
                if volume_ratio is not None and volume_ratio > 8.0: 
                    buy_confidence_score += 25
                    buy_rationale_parts.append(f"Very high volume ratio ({volume_ratio:.2f}x)")
                elif volume_ratio is not None and volume_ratio > 4.0: 
                    buy_confidence_score += 10
                    buy_rationale_parts.append(f"High volume ratio ({volume_ratio:.2f}x)")

                # Signal C: RSI Analysis (Correcting "oversold" misinterpretations)
                # Failed trades often used "oversold" rationale with neutral RSI (48-50).
                # This logic differentiates true oversold conditions from neutral or overbought.
                if rsi is not None:
                    if rsi < 30 and price_change_pct is not None and price_change_pct > 0: 
                        # Genuinely oversold with initial signs of positive rebound
                        buy_confidence_score += 40 
                        buy_rationale_parts.append(f"Genuine oversold (RSI {rsi:.2f}) with initial rebound")
                    elif 30 <= rsi <= 65: 
                        # RSI in a healthy, non-extreme range, suitable for momentum trades
                        buy_confidence_score += 10
                        buy_rationale_parts.append(f"RSI in healthy range ({rsi:.2f})")
                    elif rsi > 70: 
                        # Overbought condition, which should deter BUYs
                        buy_confidence_score -= 30
                        buy_rationale_parts.append(f"RSI overbought ({rsi:.2f})")

                # Signal D: Market-Specific Nuances (e.g., Foreigner Net for KR market)
                if market == "KR":
                    if foreigner_net is not None and foreigner_net > 0:
                        buy_confidence_score += 15
                        buy_rationale_parts.append(f"Positive foreign institutional flow ({foreigner_net})")
                    # Due to the overall higher risk profile observed in the KR market (even outside specific hours),
                    # apply a small general penalty to reduce aggressiveness compared to other markets.
                    buy_confidence_score -= 5 

                # --- 3. FINAL DECISION AND CONFIDENCE ADJUSTMENT ---
                # The old strategy exhibited high confidence (avg 83.2) in its failed trades.
                # The new strategy must require a higher *achieved* confidence score for a BUY,
                # and cap the reported confidence to avoid overconfidence.

                if buy_confidence_score >= 75: # A high threshold for a BUY decision
                    action = "BUY"
                    # Cap confidence to prevent over-optimism, reflecting historical patterns
                    confidence = min(buy_confidence_score, 90) 
                    rationale = " ".join(buy_rationale_parts) + ". Confident BUY based on robust, multi-factor analysis."
                elif buy_confidence_score >= 50: 
                    # Signals were present but not strong enough for a BUY
                    rationale = "Potential BUY signals detected, but insufficient strength or confirmations: " + " ".join(buy_rationale_parts)
                    confidence = buy_confidence_score # Report current confidence, but action is NO_ACTION
                else: 
                    # Weak or no relevant signals
                    if buy_rationale_parts:
                        rationale = "Weak or mixed signals: " + " ".join(buy_rationale_parts)
                        confidence = max(50, buy_confidence_score) # Ensure confidence doesn't drop too low for NO_ACTION
                    # Otherwise, default rationale and confidence remain.

                return {
                    "action": action,
                    "confidence": confidence,
                    "rationale": rationale
                }
