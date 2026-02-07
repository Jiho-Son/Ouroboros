"""Tests for strategy/playbook Pydantic models."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from src.strategy.models import (
    CrossMarketContext,
    DayPlaybook,
    GlobalRule,
    MarketOutlook,
    PlaybookStatus,
    ScenarioAction,
    StockCondition,
    StockPlaybook,
    StockScenario,
)


# ---------------------------------------------------------------------------
# StockCondition
# ---------------------------------------------------------------------------


class TestStockCondition:
    def test_empty_condition(self) -> None:
        cond = StockCondition()
        assert not cond.has_any_condition()

    def test_single_field(self) -> None:
        cond = StockCondition(rsi_below=30.0)
        assert cond.has_any_condition()

    def test_multiple_fields(self) -> None:
        cond = StockCondition(rsi_below=25.0, volume_ratio_above=3.0)
        assert cond.has_any_condition()

    def test_all_fields(self) -> None:
        cond = StockCondition(
            rsi_below=30,
            rsi_above=10,
            volume_ratio_above=2.0,
            volume_ratio_below=10.0,
            price_above=1000,
            price_below=50000,
            price_change_pct_above=-5.0,
            price_change_pct_below=5.0,
        )
        assert cond.has_any_condition()


# ---------------------------------------------------------------------------
# StockScenario
# ---------------------------------------------------------------------------


class TestStockScenario:
    def test_valid_scenario(self) -> None:
        s = StockScenario(
            condition=StockCondition(rsi_below=25.0),
            action=ScenarioAction.BUY,
            confidence=85,
            allocation_pct=15.0,
            stop_loss_pct=-2.0,
            take_profit_pct=3.0,
            rationale="Oversold bounce expected",
        )
        assert s.action == ScenarioAction.BUY
        assert s.confidence == 85

    def test_confidence_too_high(self) -> None:
        with pytest.raises(ValidationError):
            StockScenario(
                condition=StockCondition(),
                action=ScenarioAction.BUY,
                confidence=101,
            )

    def test_confidence_too_low(self) -> None:
        with pytest.raises(ValidationError):
            StockScenario(
                condition=StockCondition(),
                action=ScenarioAction.BUY,
                confidence=-1,
            )

    def test_allocation_too_high(self) -> None:
        with pytest.raises(ValidationError):
            StockScenario(
                condition=StockCondition(),
                action=ScenarioAction.BUY,
                confidence=80,
                allocation_pct=101.0,
            )

    def test_stop_loss_must_be_negative(self) -> None:
        with pytest.raises(ValidationError):
            StockScenario(
                condition=StockCondition(),
                action=ScenarioAction.BUY,
                confidence=80,
                stop_loss_pct=1.0,
            )

    def test_take_profit_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            StockScenario(
                condition=StockCondition(),
                action=ScenarioAction.BUY,
                confidence=80,
                take_profit_pct=-1.0,
            )

    def test_defaults(self) -> None:
        s = StockScenario(
            condition=StockCondition(),
            action=ScenarioAction.HOLD,
            confidence=50,
        )
        assert s.allocation_pct == 10.0
        assert s.stop_loss_pct == -2.0
        assert s.take_profit_pct == 3.0
        assert s.rationale == ""


# ---------------------------------------------------------------------------
# StockPlaybook
# ---------------------------------------------------------------------------


class TestStockPlaybook:
    def test_valid_playbook(self) -> None:
        pb = StockPlaybook(
            stock_code="005930",
            stock_name="Samsung Electronics",
            scenarios=[
                StockScenario(
                    condition=StockCondition(rsi_below=25.0),
                    action=ScenarioAction.BUY,
                    confidence=85,
                ),
            ],
        )
        assert pb.stock_code == "005930"
        assert len(pb.scenarios) == 1

    def test_empty_scenarios_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StockPlaybook(
                stock_code="005930",
                scenarios=[],
            )

    def test_multiple_scenarios(self) -> None:
        pb = StockPlaybook(
            stock_code="AAPL",
            scenarios=[
                StockScenario(
                    condition=StockCondition(rsi_below=25.0),
                    action=ScenarioAction.BUY,
                    confidence=85,
                ),
                StockScenario(
                    condition=StockCondition(rsi_above=75.0),
                    action=ScenarioAction.SELL,
                    confidence=80,
                ),
            ],
        )
        assert len(pb.scenarios) == 2


# ---------------------------------------------------------------------------
# GlobalRule
# ---------------------------------------------------------------------------


class TestGlobalRule:
    def test_valid_rule(self) -> None:
        rule = GlobalRule(
            condition="portfolio_pnl_pct < -2.0",
            action=ScenarioAction.REDUCE_ALL,
            rationale="Risk limit approaching",
        )
        assert rule.action == ScenarioAction.REDUCE_ALL

    def test_hold_rule(self) -> None:
        rule = GlobalRule(
            condition="volatility_index > 30",
            action=ScenarioAction.HOLD,
        )
        assert rule.rationale == ""


# ---------------------------------------------------------------------------
# CrossMarketContext
# ---------------------------------------------------------------------------


class TestCrossMarketContext:
    def test_valid_context(self) -> None:
        ctx = CrossMarketContext(
            market="US",
            date="2026-02-07",
            total_pnl=-1.5,
            win_rate=40.0,
            index_change_pct=-2.3,
            key_events=["Fed rate decision"],
            lessons=["Avoid tech sector on rate hike days"],
        )
        assert ctx.market == "US"
        assert len(ctx.key_events) == 1

    def test_defaults(self) -> None:
        ctx = CrossMarketContext(market="KR", date="2026-02-07")
        assert ctx.total_pnl == 0.0
        assert ctx.key_events == []
        assert ctx.lessons == []


# ---------------------------------------------------------------------------
# DayPlaybook
# ---------------------------------------------------------------------------


def _make_scenario(rsi_below: float = 25.0) -> StockScenario:
    return StockScenario(
        condition=StockCondition(rsi_below=rsi_below),
        action=ScenarioAction.BUY,
        confidence=85,
    )


def _make_playbook(**kwargs) -> DayPlaybook:
    defaults = {
        "date": date(2026, 2, 7),
        "market": "KR",
        "stock_playbooks": [
            StockPlaybook(stock_code="005930", scenarios=[_make_scenario()]),
        ],
    }
    defaults.update(kwargs)
    return DayPlaybook(**defaults)


class TestDayPlaybook:
    def test_valid_playbook(self) -> None:
        pb = _make_playbook()
        assert pb.market == "KR"
        assert pb.date == date(2026, 2, 7)
        assert pb.default_action == ScenarioAction.HOLD
        assert pb.scenario_count == 1
        assert pb.stock_count == 1

    def test_generated_at_auto_set(self) -> None:
        pb = _make_playbook()
        assert pb.generated_at != ""

    def test_explicit_generated_at(self) -> None:
        pb = _make_playbook(generated_at="2026-02-07T08:30:00")
        assert pb.generated_at == "2026-02-07T08:30:00"

    def test_duplicate_stocks_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DayPlaybook(
                date=date(2026, 2, 7),
                market="KR",
                stock_playbooks=[
                    StockPlaybook(stock_code="005930", scenarios=[_make_scenario()]),
                    StockPlaybook(stock_code="005930", scenarios=[_make_scenario(30)]),
                ],
            )

    def test_empty_stock_playbooks_allowed(self) -> None:
        pb = DayPlaybook(
            date=date(2026, 2, 7),
            market="KR",
            stock_playbooks=[],
        )
        assert pb.stock_count == 0
        assert pb.scenario_count == 0

    def test_get_stock_playbook_found(self) -> None:
        pb = _make_playbook()
        result = pb.get_stock_playbook("005930")
        assert result is not None
        assert result.stock_code == "005930"

    def test_get_stock_playbook_not_found(self) -> None:
        pb = _make_playbook()
        result = pb.get_stock_playbook("AAPL")
        assert result is None

    def test_with_global_rules(self) -> None:
        pb = _make_playbook(
            global_rules=[
                GlobalRule(
                    condition="portfolio_pnl_pct < -2.0",
                    action=ScenarioAction.REDUCE_ALL,
                ),
            ],
        )
        assert len(pb.global_rules) == 1

    def test_with_cross_market_context(self) -> None:
        ctx = CrossMarketContext(market="US", date="2026-02-07", total_pnl=-1.5)
        pb = _make_playbook(cross_market=ctx)
        assert pb.cross_market is not None
        assert pb.cross_market.market == "US"

    def test_market_outlook(self) -> None:
        pb = _make_playbook(market_outlook=MarketOutlook.BEARISH)
        assert pb.market_outlook == MarketOutlook.BEARISH

    def test_multiple_stocks_multiple_scenarios(self) -> None:
        pb = DayPlaybook(
            date=date(2026, 2, 7),
            market="US",
            stock_playbooks=[
                StockPlaybook(
                    stock_code="AAPL",
                    scenarios=[_make_scenario(), _make_scenario(30)],
                ),
                StockPlaybook(
                    stock_code="MSFT",
                    scenarios=[_make_scenario()],
                ),
            ],
        )
        assert pb.stock_count == 2
        assert pb.scenario_count == 3

    def test_serialization_roundtrip(self) -> None:
        pb = _make_playbook(
            market_outlook=MarketOutlook.BULLISH,
            cross_market=CrossMarketContext(market="US", date="2026-02-07"),
        )
        json_str = pb.model_dump_json()
        restored = DayPlaybook.model_validate_json(json_str)
        assert restored.market == pb.market
        assert restored.date == pb.date
        assert restored.scenario_count == pb.scenario_count
        assert restored.cross_market is not None


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnums:
    def test_scenario_action_values(self) -> None:
        assert ScenarioAction.BUY.value == "BUY"
        assert ScenarioAction.SELL.value == "SELL"
        assert ScenarioAction.HOLD.value == "HOLD"
        assert ScenarioAction.REDUCE_ALL.value == "REDUCE_ALL"

    def test_market_outlook_values(self) -> None:
        assert len(MarketOutlook) == 5

    def test_playbook_status_values(self) -> None:
        assert PlaybookStatus.READY.value == "ready"
        assert PlaybookStatus.EXPIRED.value == "expired"
