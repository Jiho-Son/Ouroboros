from __future__ import annotations

import pytest

from src.analysis.backtest_execution_model import (
    BacktestExecutionModel,
    ExecutionAssumptions,
    ExecutionRequest,
)


def test_buy_uses_unfavorable_slippage_direction() -> None:
    model = BacktestExecutionModel(
        ExecutionAssumptions(
            slippage_bps_by_session={"US_PRE": 50.0},
            failure_rate_by_session={"US_PRE": 0.0},
            partial_fill_rate_by_session={"US_PRE": 0.0},
            seed=1,
        )
    )
    out = model.simulate(
        ExecutionRequest(side="BUY", session_id="US_PRE", qty=10, reference_price=100.0)
    )
    assert out.status == "FILLED"
    assert out.avg_price == pytest.approx(100.5)


def test_sell_uses_unfavorable_slippage_direction() -> None:
    model = BacktestExecutionModel(
        ExecutionAssumptions(
            slippage_bps_by_session={"US_PRE": 50.0},
            failure_rate_by_session={"US_PRE": 0.0},
            partial_fill_rate_by_session={"US_PRE": 0.0},
            seed=1,
        )
    )
    out = model.simulate(
        ExecutionRequest(side="SELL", session_id="US_PRE", qty=10, reference_price=100.0)
    )
    assert out.status == "FILLED"
    assert out.avg_price == pytest.approx(99.5)


def test_failure_rate_can_reject_order() -> None:
    model = BacktestExecutionModel(
        ExecutionAssumptions(
            slippage_bps_by_session={"KRX_REG": 10.0},
            failure_rate_by_session={"KRX_REG": 1.0},
            partial_fill_rate_by_session={"KRX_REG": 0.0},
            seed=42,
        )
    )
    out = model.simulate(
        ExecutionRequest(side="BUY", session_id="KRX_REG", qty=10, reference_price=100.0)
    )
    assert out.status == "REJECTED"
    assert out.filled_qty == 0


def test_partial_fill_applies_when_rate_is_one() -> None:
    model = BacktestExecutionModel(
        ExecutionAssumptions(
            slippage_bps_by_session={"KRX_REG": 0.0},
            failure_rate_by_session={"KRX_REG": 0.0},
            partial_fill_rate_by_session={"KRX_REG": 1.0},
            partial_fill_min_ratio=0.4,
            partial_fill_max_ratio=0.4,
            seed=0,
        )
    )
    out = model.simulate(
        ExecutionRequest(side="BUY", session_id="KRX_REG", qty=10, reference_price=100.0)
    )
    assert out.status == "PARTIAL"
    assert out.filled_qty == 4
    assert out.avg_price == 100.0
