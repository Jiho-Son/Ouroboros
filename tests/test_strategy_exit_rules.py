from src.strategy.exit_rules import ExitRuleConfig, ExitRuleInput, evaluate_exit
from src.strategy.position_state_machine import PositionState


def test_hard_stop_exit() -> None:
    out = evaluate_exit(
        current_state=PositionState.HOLDING,
        config=ExitRuleConfig(hard_stop_pct=-2.0, arm_pct=3.0),
        inp=ExitRuleInput(current_price=97.0, entry_price=100.0, peak_price=100.0),
    )
    assert out.should_exit is True
    assert out.reason == "hard_stop"


def test_take_profit_exit_for_backward_compatibility() -> None:
    out = evaluate_exit(
        current_state=PositionState.HOLDING,
        config=ExitRuleConfig(hard_stop_pct=-2.0, arm_pct=3.0),
        inp=ExitRuleInput(current_price=104.0, entry_price=100.0, peak_price=104.0),
    )
    assert out.should_exit is True
    assert out.reason == "arm_take_profit"


def test_model_assist_signal_promotes_be_lock_without_direct_exit() -> None:
    out = evaluate_exit(
        current_state=PositionState.HOLDING,
        config=ExitRuleConfig(model_prob_threshold=0.62, be_arm_pct=1.2, arm_pct=10.0),
        inp=ExitRuleInput(
            current_price=100.5,
            entry_price=100.0,
            peak_price=105.0,
            pred_down_prob=0.8,
            liquidity_weak=True,
        ),
    )
    assert out.should_exit is False
    assert out.state == PositionState.BE_LOCK
    assert out.reason == "model_assist_be_lock"
