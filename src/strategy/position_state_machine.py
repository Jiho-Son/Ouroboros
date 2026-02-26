"""Position state machine for staged exit control.

State progression is monotonic (promotion-only) except terminal EXITED.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PositionState(str, Enum):
    HOLDING = "HOLDING"
    BE_LOCK = "BE_LOCK"
    ARMED = "ARMED"
    EXITED = "EXITED"


_STATE_RANK: dict[PositionState, int] = {
    PositionState.HOLDING: 0,
    PositionState.BE_LOCK: 1,
    PositionState.ARMED: 2,
    PositionState.EXITED: 3,
}


@dataclass(frozen=True)
class StateTransitionInput:
    unrealized_pnl_pct: float
    be_arm_pct: float
    arm_pct: float
    hard_stop_hit: bool = False
    trailing_stop_hit: bool = False
    model_exit_signal: bool = False
    be_lock_threat: bool = False


def evaluate_exit_first(inp: StateTransitionInput) -> bool:
    """Return True when terminal exit conditions are met.

    EXITED must be evaluated before any promotion.
    """
    return (
        inp.hard_stop_hit
        or inp.trailing_stop_hit
        or inp.model_exit_signal
        or inp.be_lock_threat
    )


def promote_state(current: PositionState, inp: StateTransitionInput) -> PositionState:
    """Promote to highest admissible state for current tick/bar.

    Rules:
    - EXITED has highest precedence and is terminal.
    - Promotions are monotonic (no downgrade).
    """
    if current == PositionState.EXITED:
        return PositionState.EXITED

    if evaluate_exit_first(inp):
        return PositionState.EXITED

    target = PositionState.HOLDING
    if inp.unrealized_pnl_pct >= inp.arm_pct:
        target = PositionState.ARMED
    elif inp.unrealized_pnl_pct >= inp.be_arm_pct:
        target = PositionState.BE_LOCK

    return target if _STATE_RANK[target] > _STATE_RANK[current] else current
