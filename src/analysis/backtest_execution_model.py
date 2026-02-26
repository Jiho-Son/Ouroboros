"""Conservative backtest execution model."""

from __future__ import annotations

from dataclasses import dataclass
import math
from random import Random
from typing import Literal


OrderSide = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class ExecutionRequest:
    side: OrderSide
    session_id: str
    qty: int
    reference_price: float


@dataclass(frozen=True)
class ExecutionAssumptions:
    slippage_bps_by_session: dict[str, float]
    failure_rate_by_session: dict[str, float]
    partial_fill_rate_by_session: dict[str, float]
    partial_fill_min_ratio: float = 0.3
    partial_fill_max_ratio: float = 0.8
    seed: int = 0


@dataclass(frozen=True)
class ExecutionResult:
    status: Literal["FILLED", "PARTIAL", "REJECTED"]
    filled_qty: int
    avg_price: float
    slippage_bps: float
    reason: str


class BacktestExecutionModel:
    """Execution simulator with conservative unfavorable fill assumptions."""

    def __init__(self, assumptions: ExecutionAssumptions) -> None:
        self.assumptions = assumptions
        self._rng = Random(assumptions.seed)
        if assumptions.partial_fill_min_ratio <= 0 or assumptions.partial_fill_max_ratio > 1:
            raise ValueError("partial fill ratios must be within (0,1]")
        if assumptions.partial_fill_min_ratio > assumptions.partial_fill_max_ratio:
            raise ValueError("partial_fill_min_ratio must be <= partial_fill_max_ratio")
        for sess, bps in assumptions.slippage_bps_by_session.items():
            if not math.isfinite(bps) or bps < 0:
                raise ValueError(f"slippage_bps must be finite and >= 0 for session={sess}")
        for sess, rate in assumptions.failure_rate_by_session.items():
            if not math.isfinite(rate) or rate < 0 or rate > 1:
                raise ValueError(f"failure_rate must be in [0,1] for session={sess}")
        for sess, rate in assumptions.partial_fill_rate_by_session.items():
            if not math.isfinite(rate) or rate < 0 or rate > 1:
                raise ValueError(f"partial_fill_rate must be in [0,1] for session={sess}")

    def simulate(self, request: ExecutionRequest) -> ExecutionResult:
        if request.qty <= 0:
            raise ValueError("qty must be positive")
        if request.reference_price <= 0:
            raise ValueError("reference_price must be positive")

        slippage_bps = self.assumptions.slippage_bps_by_session.get(request.session_id, 0.0)
        failure_rate = self.assumptions.failure_rate_by_session.get(request.session_id, 0.0)
        partial_rate = self.assumptions.partial_fill_rate_by_session.get(request.session_id, 0.0)

        if self._rng.random() < failure_rate:
            return ExecutionResult(
                status="REJECTED",
                filled_qty=0,
                avg_price=0.0,
                slippage_bps=slippage_bps,
                reason="execution_failure",
            )

        slip_mult = 1.0 + (slippage_bps / 10000.0 if request.side == "BUY" else -slippage_bps / 10000.0)
        exec_price = request.reference_price * slip_mult

        if self._rng.random() < partial_rate:
            ratio = self._rng.uniform(
                self.assumptions.partial_fill_min_ratio,
                self.assumptions.partial_fill_max_ratio,
            )
            filled = max(1, min(request.qty - 1, int(request.qty * ratio)))
            return ExecutionResult(
                status="PARTIAL",
                filled_qty=filled,
                avg_price=exec_price,
                slippage_bps=slippage_bps,
                reason="partial_fill",
            )

        return ExecutionResult(
            status="FILLED",
            filled_qty=request.qty,
            avg_price=exec_price,
            slippage_bps=slippage_bps,
            reason="filled",
        )
