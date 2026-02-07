"""Local scenario engine for playbook execution.

Matches real-time market conditions against pre-defined scenarios
without any API calls. Designed for sub-100ms execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.strategy.models import (
    DayPlaybook,
    GlobalRule,
    ScenarioAction,
    StockCondition,
    StockScenario,
)

logger = logging.getLogger(__name__)


@dataclass
class ScenarioMatch:
    """Result of matching market conditions against scenarios."""

    stock_code: str
    matched_scenario: StockScenario | None
    action: ScenarioAction
    confidence: int
    rationale: str
    global_rule_triggered: GlobalRule | None = None
    match_details: dict[str, Any] = field(default_factory=dict)


class ScenarioEngine:
    """Evaluates playbook scenarios against real-time market data.

    No API calls — pure Python condition matching.
    """

    def evaluate(
        self,
        playbook: DayPlaybook,
        stock_code: str,
        market_data: dict[str, Any],
        portfolio_data: dict[str, Any],
    ) -> ScenarioMatch:
        """Match market conditions to scenarios and return a decision.

        Algorithm:
        1. Check global rules first (portfolio-level circuit breakers)
        2. Find the StockPlaybook for the given stock_code
        3. Iterate scenarios in order (first match wins)
        4. If no match, return playbook.default_action (HOLD)

        Args:
            playbook: Today's DayPlaybook for this market
            stock_code: Stock ticker to evaluate
            market_data: Real-time market data (price, rsi, volume_ratio, etc.)
            portfolio_data: Portfolio state (pnl_pct, total_cash, etc.)

        Returns:
            ScenarioMatch with the decision
        """
        # 1. Check global rules
        triggered_rule = self.check_global_rules(playbook, portfolio_data)
        if triggered_rule is not None:
            logger.info(
                "Global rule triggered for %s: %s -> %s",
                stock_code,
                triggered_rule.condition,
                triggered_rule.action.value,
            )
            return ScenarioMatch(
                stock_code=stock_code,
                matched_scenario=None,
                action=triggered_rule.action,
                confidence=100,
                rationale=f"Global rule: {triggered_rule.rationale or triggered_rule.condition}",
                global_rule_triggered=triggered_rule,
            )

        # 2. Find stock playbook
        stock_pb = playbook.get_stock_playbook(stock_code)
        if stock_pb is None:
            logger.debug("No playbook for %s — defaulting to %s", stock_code, playbook.default_action)
            return ScenarioMatch(
                stock_code=stock_code,
                matched_scenario=None,
                action=playbook.default_action,
                confidence=0,
                rationale=f"No scenarios defined for {stock_code}",
            )

        # 3. Iterate scenarios (first match wins)
        for scenario in stock_pb.scenarios:
            if self.evaluate_condition(scenario.condition, market_data):
                logger.info(
                    "Scenario matched for %s: %s (confidence=%d)",
                    stock_code,
                    scenario.action.value,
                    scenario.confidence,
                )
                return ScenarioMatch(
                    stock_code=stock_code,
                    matched_scenario=scenario,
                    action=scenario.action,
                    confidence=scenario.confidence,
                    rationale=scenario.rationale,
                    match_details=self._build_match_details(scenario.condition, market_data),
                )

        # 4. No match — default action
        logger.debug("No scenario matched for %s — defaulting to %s", stock_code, playbook.default_action)
        return ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=playbook.default_action,
            confidence=0,
            rationale="No scenario conditions met — holding position",
        )

    def check_global_rules(
        self,
        playbook: DayPlaybook,
        portfolio_data: dict[str, Any],
    ) -> GlobalRule | None:
        """Check portfolio-level rules. Returns first triggered rule or None."""
        for rule in playbook.global_rules:
            if self._evaluate_global_condition(rule.condition, portfolio_data):
                return rule
        return None

    def evaluate_condition(
        self,
        condition: StockCondition,
        market_data: dict[str, Any],
    ) -> bool:
        """Evaluate all non-None fields in condition as AND.

        Returns True only if ALL specified conditions are met.
        Empty condition (no fields set) returns False for safety.
        """
        if not condition.has_any_condition():
            return False

        checks: list[bool] = []

        rsi = market_data.get("rsi")
        if condition.rsi_below is not None:
            checks.append(rsi is not None and rsi < condition.rsi_below)
        if condition.rsi_above is not None:
            checks.append(rsi is not None and rsi > condition.rsi_above)

        volume_ratio = market_data.get("volume_ratio")
        if condition.volume_ratio_above is not None:
            checks.append(volume_ratio is not None and volume_ratio > condition.volume_ratio_above)
        if condition.volume_ratio_below is not None:
            checks.append(volume_ratio is not None and volume_ratio < condition.volume_ratio_below)

        price = market_data.get("current_price")
        if condition.price_above is not None:
            checks.append(price is not None and price > condition.price_above)
        if condition.price_below is not None:
            checks.append(price is not None and price < condition.price_below)

        price_change_pct = market_data.get("price_change_pct")
        if condition.price_change_pct_above is not None:
            checks.append(price_change_pct is not None and price_change_pct > condition.price_change_pct_above)
        if condition.price_change_pct_below is not None:
            checks.append(price_change_pct is not None and price_change_pct < condition.price_change_pct_below)

        return len(checks) > 0 and all(checks)

    def _evaluate_global_condition(
        self,
        condition_str: str,
        portfolio_data: dict[str, Any],
    ) -> bool:
        """Evaluate a simple global condition string against portfolio data.

        Supports: "field < value", "field > value", "field <= value", "field >= value"
        """
        parts = condition_str.strip().split()
        if len(parts) != 3:
            logger.warning("Invalid global condition format: %s", condition_str)
            return False

        field_name, operator, value_str = parts
        try:
            threshold = float(value_str)
        except ValueError:
            logger.warning("Invalid threshold in condition: %s", condition_str)
            return False

        actual = portfolio_data.get(field_name)
        if actual is None:
            return False

        try:
            actual_val = float(actual)
        except (ValueError, TypeError):
            return False

        if operator == "<":
            return actual_val < threshold
        elif operator == ">":
            return actual_val > threshold
        elif operator == "<=":
            return actual_val <= threshold
        elif operator == ">=":
            return actual_val >= threshold
        else:
            logger.warning("Unknown operator in condition: %s", operator)
            return False

    def _build_match_details(
        self,
        condition: StockCondition,
        market_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a summary of which conditions matched and their values."""
        details: dict[str, Any] = {}

        if condition.rsi_below is not None or condition.rsi_above is not None:
            details["rsi"] = market_data.get("rsi")
        if condition.volume_ratio_above is not None or condition.volume_ratio_below is not None:
            details["volume_ratio"] = market_data.get("volume_ratio")
        if condition.price_above is not None or condition.price_below is not None:
            details["current_price"] = market_data.get("current_price")
        if condition.price_change_pct_above is not None or condition.price_change_pct_below is not None:
            details["price_change_pct"] = market_data.get("price_change_pct")

        return details
