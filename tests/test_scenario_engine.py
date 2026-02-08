"""Tests for the local scenario engine."""

from __future__ import annotations

from datetime import date

import pytest

from src.strategy.models import (
    DayPlaybook,
    GlobalRule,
    ScenarioAction,
    StockCondition,
    StockPlaybook,
    StockScenario,
)
from src.strategy.scenario_engine import ScenarioEngine, ScenarioMatch


@pytest.fixture
def engine() -> ScenarioEngine:
    return ScenarioEngine()


def _scenario(
    rsi_below: float | None = None,
    rsi_above: float | None = None,
    volume_ratio_above: float | None = None,
    action: ScenarioAction = ScenarioAction.BUY,
    confidence: int = 85,
    **kwargs,
) -> StockScenario:
    return StockScenario(
        condition=StockCondition(
            rsi_below=rsi_below,
            rsi_above=rsi_above,
            volume_ratio_above=volume_ratio_above,
            **kwargs,
        ),
        action=action,
        confidence=confidence,
        rationale=f"Test scenario: {action.value}",
    )


def _playbook(
    stock_code: str = "005930",
    scenarios: list[StockScenario] | None = None,
    global_rules: list[GlobalRule] | None = None,
    default_action: ScenarioAction = ScenarioAction.HOLD,
) -> DayPlaybook:
    if scenarios is None:
        scenarios = [_scenario(rsi_below=30.0)]
    return DayPlaybook(
        date=date(2026, 2, 7),
        market="KR",
        stock_playbooks=[StockPlaybook(stock_code=stock_code, scenarios=scenarios)],
        global_rules=global_rules or [],
        default_action=default_action,
    )


# ---------------------------------------------------------------------------
# evaluate_condition
# ---------------------------------------------------------------------------


class TestEvaluateCondition:
    def test_rsi_below_match(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(rsi_below=30.0)
        assert engine.evaluate_condition(cond, {"rsi": 25.0})

    def test_rsi_below_no_match(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(rsi_below=30.0)
        assert not engine.evaluate_condition(cond, {"rsi": 35.0})

    def test_rsi_above_match(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(rsi_above=70.0)
        assert engine.evaluate_condition(cond, {"rsi": 75.0})

    def test_rsi_above_no_match(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(rsi_above=70.0)
        assert not engine.evaluate_condition(cond, {"rsi": 65.0})

    def test_volume_ratio_above_match(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(volume_ratio_above=3.0)
        assert engine.evaluate_condition(cond, {"volume_ratio": 4.5})

    def test_volume_ratio_below_match(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(volume_ratio_below=1.0)
        assert engine.evaluate_condition(cond, {"volume_ratio": 0.5})

    def test_price_above_match(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(price_above=50000)
        assert engine.evaluate_condition(cond, {"current_price": 55000})

    def test_price_below_match(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(price_below=50000)
        assert engine.evaluate_condition(cond, {"current_price": 45000})

    def test_price_change_pct_above_match(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(price_change_pct_above=2.0)
        assert engine.evaluate_condition(cond, {"price_change_pct": 3.5})

    def test_price_change_pct_below_match(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(price_change_pct_below=-3.0)
        assert engine.evaluate_condition(cond, {"price_change_pct": -4.0})

    def test_multiple_conditions_and_logic(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(rsi_below=30.0, volume_ratio_above=3.0)
        # Both met
        assert engine.evaluate_condition(cond, {"rsi": 25.0, "volume_ratio": 4.0})
        # Only RSI met
        assert not engine.evaluate_condition(cond, {"rsi": 25.0, "volume_ratio": 2.0})
        # Only volume met
        assert not engine.evaluate_condition(cond, {"rsi": 35.0, "volume_ratio": 4.0})
        # Neither met
        assert not engine.evaluate_condition(cond, {"rsi": 35.0, "volume_ratio": 2.0})

    def test_empty_condition_returns_false(self, engine: ScenarioEngine) -> None:
        cond = StockCondition()
        assert not engine.evaluate_condition(cond, {"rsi": 25.0})

    def test_missing_data_returns_false(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(rsi_below=30.0)
        assert not engine.evaluate_condition(cond, {})

    def test_none_data_returns_false(self, engine: ScenarioEngine) -> None:
        cond = StockCondition(rsi_below=30.0)
        assert not engine.evaluate_condition(cond, {"rsi": None})

    def test_boundary_value_not_matched(self, engine: ScenarioEngine) -> None:
        """rsi_below=30 should NOT match rsi=30 (strict less than)."""
        cond = StockCondition(rsi_below=30.0)
        assert not engine.evaluate_condition(cond, {"rsi": 30.0})

    def test_boundary_value_above_not_matched(self, engine: ScenarioEngine) -> None:
        """rsi_above=70 should NOT match rsi=70 (strict greater than)."""
        cond = StockCondition(rsi_above=70.0)
        assert not engine.evaluate_condition(cond, {"rsi": 70.0})

    def test_string_value_no_exception(self, engine: ScenarioEngine) -> None:
        """String numeric value should not raise TypeError."""
        cond = StockCondition(rsi_below=30.0)
        # "25" can be cast to float → should match
        assert engine.evaluate_condition(cond, {"rsi": "25"})
        # "35" → should not match
        assert not engine.evaluate_condition(cond, {"rsi": "35"})

    def test_percent_string_returns_false(self, engine: ScenarioEngine) -> None:
        """Percent string like '30%' cannot be cast to float → False, no exception."""
        cond = StockCondition(rsi_below=30.0)
        assert not engine.evaluate_condition(cond, {"rsi": "30%"})

    def test_decimal_value_no_exception(self, engine: ScenarioEngine) -> None:
        """Decimal values should be safely handled."""
        from decimal import Decimal

        cond = StockCondition(rsi_below=30.0)
        assert engine.evaluate_condition(cond, {"rsi": Decimal("25.0")})

    def test_mixed_invalid_types_no_exception(self, engine: ScenarioEngine) -> None:
        """Various invalid types should not raise exceptions."""
        cond = StockCondition(
            rsi_below=30.0, volume_ratio_above=2.0,
            price_above=100, price_change_pct_below=-1.0,
        )
        data = {
            "rsi": [25],           # list
            "volume_ratio": "bad",  # non-numeric string
            "current_price": {},    # dict
            "price_change_pct": object(),  # arbitrary object
        }
        # Should return False (invalid types → None → False), never raise
        assert not engine.evaluate_condition(cond, data)

    def test_missing_key_logs_warning_once(self, caplog) -> None:
        """Missing key warning should fire only once per key per engine instance."""
        import logging

        eng = ScenarioEngine()
        cond = StockCondition(rsi_below=30.0)
        with caplog.at_level(logging.WARNING):
            eng.evaluate_condition(cond, {})
            eng.evaluate_condition(cond, {})
            eng.evaluate_condition(cond, {})
        # Warning should appear exactly once despite 3 calls
        assert caplog.text.count("'rsi' but key missing") == 1


# ---------------------------------------------------------------------------
# check_global_rules
# ---------------------------------------------------------------------------


class TestCheckGlobalRules:
    def test_no_rules(self, engine: ScenarioEngine) -> None:
        pb = _playbook(global_rules=[])
        result = engine.check_global_rules(pb, {"portfolio_pnl_pct": -1.0})
        assert result is None

    def test_rule_triggered(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            global_rules=[
                GlobalRule(
                    condition="portfolio_pnl_pct < -2.0",
                    action=ScenarioAction.REDUCE_ALL,
                    rationale="Near circuit breaker",
                ),
            ]
        )
        result = engine.check_global_rules(pb, {"portfolio_pnl_pct": -2.5})
        assert result is not None
        assert result.action == ScenarioAction.REDUCE_ALL

    def test_rule_not_triggered(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            global_rules=[
                GlobalRule(
                    condition="portfolio_pnl_pct < -2.0",
                    action=ScenarioAction.REDUCE_ALL,
                ),
            ]
        )
        result = engine.check_global_rules(pb, {"portfolio_pnl_pct": -1.0})
        assert result is None

    def test_first_rule_wins(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            global_rules=[
                GlobalRule(condition="portfolio_pnl_pct < -2.0", action=ScenarioAction.REDUCE_ALL),
                GlobalRule(condition="portfolio_pnl_pct < -1.0", action=ScenarioAction.HOLD),
            ]
        )
        result = engine.check_global_rules(pb, {"portfolio_pnl_pct": -2.5})
        assert result is not None
        assert result.action == ScenarioAction.REDUCE_ALL

    def test_greater_than_operator(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            global_rules=[
                GlobalRule(condition="volatility_index > 30", action=ScenarioAction.HOLD),
            ]
        )
        result = engine.check_global_rules(pb, {"volatility_index": 35})
        assert result is not None

    def test_missing_field_not_triggered(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            global_rules=[
                GlobalRule(condition="unknown_field < -2.0", action=ScenarioAction.REDUCE_ALL),
            ]
        )
        result = engine.check_global_rules(pb, {"portfolio_pnl_pct": -5.0})
        assert result is None

    def test_invalid_condition_format(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            global_rules=[
                GlobalRule(condition="bad format", action=ScenarioAction.HOLD),
            ]
        )
        result = engine.check_global_rules(pb, {})
        assert result is None

    def test_le_operator(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            global_rules=[
                GlobalRule(condition="portfolio_pnl_pct <= -2.0", action=ScenarioAction.REDUCE_ALL),
            ]
        )
        assert engine.check_global_rules(pb, {"portfolio_pnl_pct": -2.0}) is not None
        assert engine.check_global_rules(pb, {"portfolio_pnl_pct": -1.9}) is None

    def test_ge_operator(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            global_rules=[
                GlobalRule(condition="volatility >= 80.0", action=ScenarioAction.HOLD),
            ]
        )
        assert engine.check_global_rules(pb, {"volatility": 80.0}) is not None
        assert engine.check_global_rules(pb, {"volatility": 79.9}) is None


# ---------------------------------------------------------------------------
# evaluate (full pipeline)
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_scenario_match(self, engine: ScenarioEngine) -> None:
        pb = _playbook(scenarios=[_scenario(rsi_below=30.0)])
        result = engine.evaluate(pb, "005930", {"rsi": 25.0}, {})
        assert result.action == ScenarioAction.BUY
        assert result.confidence == 85
        assert result.matched_scenario is not None

    def test_no_scenario_match_returns_default(self, engine: ScenarioEngine) -> None:
        pb = _playbook(scenarios=[_scenario(rsi_below=30.0)])
        result = engine.evaluate(pb, "005930", {"rsi": 50.0}, {})
        assert result.action == ScenarioAction.HOLD
        assert result.confidence == 0
        assert result.matched_scenario is None

    def test_stock_not_in_playbook(self, engine: ScenarioEngine) -> None:
        pb = _playbook(stock_code="005930")
        result = engine.evaluate(pb, "AAPL", {"rsi": 25.0}, {})
        assert result.action == ScenarioAction.HOLD
        assert result.confidence == 0

    def test_global_rule_takes_priority(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            scenarios=[_scenario(rsi_below=30.0)],
            global_rules=[
                GlobalRule(
                    condition="portfolio_pnl_pct < -2.0",
                    action=ScenarioAction.REDUCE_ALL,
                    rationale="Loss limit",
                ),
            ],
        )
        result = engine.evaluate(
            pb,
            "005930",
            {"rsi": 25.0},  # Would match scenario
            {"portfolio_pnl_pct": -2.5},  # But global rule triggers first
        )
        assert result.action == ScenarioAction.REDUCE_ALL
        assert result.global_rule_triggered is not None
        assert result.matched_scenario is None

    def test_first_scenario_wins(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            scenarios=[
                _scenario(rsi_below=30.0, action=ScenarioAction.BUY, confidence=90),
                _scenario(rsi_below=25.0, action=ScenarioAction.BUY, confidence=95),
            ]
        )
        result = engine.evaluate(pb, "005930", {"rsi": 20.0}, {})
        # Both match, but first wins
        assert result.confidence == 90

    def test_sell_scenario(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            scenarios=[
                _scenario(rsi_above=75.0, action=ScenarioAction.SELL, confidence=80),
            ]
        )
        result = engine.evaluate(pb, "005930", {"rsi": 80.0}, {})
        assert result.action == ScenarioAction.SELL

    def test_empty_playbook(self, engine: ScenarioEngine) -> None:
        pb = DayPlaybook(date=date(2026, 2, 7), market="KR", stock_playbooks=[])
        result = engine.evaluate(pb, "005930", {"rsi": 25.0}, {})
        assert result.action == ScenarioAction.HOLD

    def test_match_details_populated(self, engine: ScenarioEngine) -> None:
        pb = _playbook(scenarios=[_scenario(rsi_below=30.0, volume_ratio_above=2.0)])
        result = engine.evaluate(
            pb, "005930", {"rsi": 25.0, "volume_ratio": 3.0}, {}
        )
        assert result.match_details.get("rsi") == 25.0
        assert result.match_details.get("volume_ratio") == 3.0

    def test_custom_default_action(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            scenarios=[_scenario(rsi_below=10.0)],  # Very unlikely to match
            default_action=ScenarioAction.SELL,
        )
        result = engine.evaluate(pb, "005930", {"rsi": 50.0}, {})
        assert result.action == ScenarioAction.SELL

    def test_multiple_stocks_in_playbook(self, engine: ScenarioEngine) -> None:
        pb = DayPlaybook(
            date=date(2026, 2, 7),
            market="US",
            stock_playbooks=[
                StockPlaybook(
                    stock_code="AAPL",
                    scenarios=[_scenario(rsi_below=25.0, confidence=90)],
                ),
                StockPlaybook(
                    stock_code="MSFT",
                    scenarios=[_scenario(rsi_above=75.0, action=ScenarioAction.SELL, confidence=80)],
                ),
            ],
        )
        aapl = engine.evaluate(pb, "AAPL", {"rsi": 20.0}, {})
        assert aapl.action == ScenarioAction.BUY
        assert aapl.confidence == 90

        msft = engine.evaluate(pb, "MSFT", {"rsi": 80.0}, {})
        assert msft.action == ScenarioAction.SELL

    def test_complex_multi_condition(self, engine: ScenarioEngine) -> None:
        pb = _playbook(
            scenarios=[
                _scenario(
                    rsi_below=30.0,
                    volume_ratio_above=3.0,
                    price_change_pct_below=-2.0,
                    confidence=95,
                ),
            ]
        )
        # All conditions met
        result = engine.evaluate(
            pb,
            "005930",
            {"rsi": 22.0, "volume_ratio": 4.0, "price_change_pct": -3.0},
            {},
        )
        assert result.action == ScenarioAction.BUY
        assert result.confidence == 95

        # One condition not met
        result2 = engine.evaluate(
            pb,
            "005930",
            {"rsi": 22.0, "volume_ratio": 4.0, "price_change_pct": -1.0},
            {},
        )
        assert result2.action == ScenarioAction.HOLD

    def test_scenario_match_returns_rationale(self, engine: ScenarioEngine) -> None:
        pb = _playbook(scenarios=[_scenario(rsi_below=30.0)])
        result = engine.evaluate(pb, "005930", {"rsi": 25.0}, {})
        assert result.rationale != ""

    def test_result_stock_code(self, engine: ScenarioEngine) -> None:
        pb = _playbook()
        result = engine.evaluate(pb, "005930", {"rsi": 25.0}, {})
        assert result.stock_code == "005930"

    def test_match_details_normalized(self, engine: ScenarioEngine) -> None:
        """match_details should contain _safe_float normalized values, not raw."""
        pb = _playbook(scenarios=[_scenario(rsi_below=30.0)])
        # Pass string value — should be normalized to float in match_details
        result = engine.evaluate(pb, "005930", {"rsi": "25.0"}, {})
        assert result.action == ScenarioAction.BUY
        assert result.match_details["rsi"] == 25.0
        assert isinstance(result.match_details["rsi"], float)
