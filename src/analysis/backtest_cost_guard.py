"""Backtest cost/slippage/failure validation guard."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class BacktestCostModel:
    commission_bps: float | None = None
    slippage_bps_by_session: dict[str, float] | None = None
    failure_rate_by_session: dict[str, float] | None = None
    unfavorable_fill_required: bool = True


def validate_backtest_cost_model(
    *,
    model: BacktestCostModel,
    required_sessions: list[str],
) -> None:
    """Raise ValueError when required cost assumptions are missing/invalid."""
    if (
        model.commission_bps is None
        or not math.isfinite(model.commission_bps)
        or model.commission_bps < 0
    ):
        raise ValueError("commission_bps must be provided and >= 0")
    if not model.unfavorable_fill_required:
        raise ValueError("unfavorable_fill_required must be True")

    slippage = model.slippage_bps_by_session or {}
    failure = model.failure_rate_by_session or {}

    missing_slippage = [s for s in required_sessions if s not in slippage]
    if missing_slippage:
        raise ValueError(
            f"missing slippage_bps_by_session for sessions: {', '.join(missing_slippage)}"
        )

    missing_failure = [s for s in required_sessions if s not in failure]
    if missing_failure:
        raise ValueError(
            f"missing failure_rate_by_session for sessions: {', '.join(missing_failure)}"
        )

    for sess, bps in slippage.items():
        if not math.isfinite(bps) or bps < 0:
            raise ValueError(f"slippage bps must be >= 0 for session={sess}")
    for sess, rate in failure.items():
        if not math.isfinite(rate) or rate < 0 or rate > 1:
            raise ValueError(f"failure rate must be within [0,1] for session={sess}")
