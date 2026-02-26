from __future__ import annotations

import pytest

from src.analysis.backtest_cost_guard import BacktestCostModel, validate_backtest_cost_model


def test_valid_backtest_cost_model_passes() -> None:
    model = BacktestCostModel(
        commission_bps=5.0,
        slippage_bps_by_session={"KRX_REG": 10.0, "US_PRE": 50.0},
        failure_rate_by_session={"KRX_REG": 0.01, "US_PRE": 0.08},
        unfavorable_fill_required=True,
    )
    validate_backtest_cost_model(model=model, required_sessions=["KRX_REG", "US_PRE"])


def test_missing_required_slippage_session_raises() -> None:
    model = BacktestCostModel(
        commission_bps=5.0,
        slippage_bps_by_session={"KRX_REG": 10.0},
        failure_rate_by_session={"KRX_REG": 0.01, "US_PRE": 0.08},
        unfavorable_fill_required=True,
    )
    with pytest.raises(ValueError, match="missing slippage_bps_by_session.*US_PRE"):
        validate_backtest_cost_model(model=model, required_sessions=["KRX_REG", "US_PRE"])


def test_missing_required_failure_rate_session_raises() -> None:
    model = BacktestCostModel(
        commission_bps=5.0,
        slippage_bps_by_session={"KRX_REG": 10.0, "US_PRE": 50.0},
        failure_rate_by_session={"KRX_REG": 0.01},
        unfavorable_fill_required=True,
    )
    with pytest.raises(ValueError, match="missing failure_rate_by_session.*US_PRE"):
        validate_backtest_cost_model(model=model, required_sessions=["KRX_REG", "US_PRE"])


def test_invalid_failure_rate_range_raises() -> None:
    model = BacktestCostModel(
        commission_bps=5.0,
        slippage_bps_by_session={"KRX_REG": 10.0},
        failure_rate_by_session={"KRX_REG": 1.2},
        unfavorable_fill_required=True,
    )
    with pytest.raises(ValueError, match="failure rate must be within"):
        validate_backtest_cost_model(model=model, required_sessions=["KRX_REG"])


def test_unfavorable_fill_requirement_cannot_be_disabled() -> None:
    model = BacktestCostModel(
        commission_bps=5.0,
        slippage_bps_by_session={"KRX_REG": 10.0},
        failure_rate_by_session={"KRX_REG": 0.02},
        unfavorable_fill_required=False,
    )
    with pytest.raises(ValueError, match="unfavorable_fill_required must be True"):
        validate_backtest_cost_model(model=model, required_sessions=["KRX_REG"])


@pytest.mark.parametrize("bad_commission", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_commission_rejected(bad_commission: float) -> None:
    model = BacktestCostModel(
        commission_bps=bad_commission,
        slippage_bps_by_session={"KRX_REG": 10.0},
        failure_rate_by_session={"KRX_REG": 0.02},
        unfavorable_fill_required=True,
    )
    with pytest.raises(ValueError, match="commission_bps"):
        validate_backtest_cost_model(model=model, required_sessions=["KRX_REG"])


@pytest.mark.parametrize("bad_slippage", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_slippage_rejected(bad_slippage: float) -> None:
    model = BacktestCostModel(
        commission_bps=5.0,
        slippage_bps_by_session={"KRX_REG": bad_slippage},
        failure_rate_by_session={"KRX_REG": 0.02},
        unfavorable_fill_required=True,
    )
    with pytest.raises(ValueError, match="slippage bps"):
        validate_backtest_cost_model(model=model, required_sessions=["KRX_REG"])
