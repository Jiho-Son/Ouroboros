from src.strategy.position_state_machine import (
    PositionState,
    StateTransitionInput,
    promote_state,
)


def test_gap_jump_promotes_to_armed_directly() -> None:
    state = promote_state(
        PositionState.HOLDING,
        StateTransitionInput(
            unrealized_pnl_pct=4.0,
            be_arm_pct=1.2,
            arm_pct=2.8,
        ),
    )
    assert state == PositionState.ARMED


def test_exited_has_priority_over_promotion() -> None:
    state = promote_state(
        PositionState.HOLDING,
        StateTransitionInput(
            unrealized_pnl_pct=5.0,
            be_arm_pct=1.2,
            arm_pct=2.8,
            hard_stop_hit=True,
        ),
    )
    assert state == PositionState.EXITED


def test_model_signal_promotes_be_lock_as_assist() -> None:
    state = promote_state(
        PositionState.HOLDING,
        StateTransitionInput(
            unrealized_pnl_pct=0.5,
            be_arm_pct=1.2,
            arm_pct=2.8,
            model_exit_signal=True,
        ),
    )
    assert state == PositionState.BE_LOCK


def test_model_signal_does_not_force_exit_directly() -> None:
    state = promote_state(
        PositionState.ARMED,
        StateTransitionInput(
            unrealized_pnl_pct=1.0,
            be_arm_pct=1.2,
            arm_pct=2.8,
            model_exit_signal=True,
        ),
    )
    assert state == PositionState.ARMED
