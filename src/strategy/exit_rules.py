"""Composite exit rules: hard stop, break-even lock, ATR trailing, model assist."""

from __future__ import annotations

from dataclasses import dataclass

from src.strategy.position_state_machine import PositionState, StateTransitionInput, promote_state


@dataclass(frozen=True)
class ExitRuleConfig:
    hard_stop_pct: float = -2.0
    be_arm_pct: float = 1.2
    arm_pct: float = 3.0
    atr_multiplier_k: float = 2.2
    model_prob_threshold: float = 0.62


@dataclass(frozen=True)
class ExitRuleInput:
    current_price: float
    entry_price: float
    peak_price: float
    atr_value: float = 0.0
    pred_down_prob: float = 0.0
    liquidity_weak: bool = False


@dataclass(frozen=True)
class ExitEvaluation:
    state: PositionState
    should_exit: bool
    reason: str
    unrealized_pnl_pct: float
    trailing_stop_price: float | None


def evaluate_exit(
    *,
    current_state: PositionState,
    config: ExitRuleConfig,
    inp: ExitRuleInput,
) -> ExitEvaluation:
    """Evaluate composite exit logic and return updated state."""
    if inp.entry_price <= 0 or inp.current_price <= 0:
        return ExitEvaluation(
            state=current_state,
            should_exit=False,
            reason="invalid_price",
            unrealized_pnl_pct=0.0,
            trailing_stop_price=None,
        )

    unrealized = (inp.current_price - inp.entry_price) / inp.entry_price * 100.0
    hard_stop_hit = unrealized <= config.hard_stop_pct
    take_profit_hit = unrealized >= config.arm_pct

    trailing_stop_price: float | None = None
    trailing_stop_hit = False
    if inp.atr_value > 0 and inp.peak_price > 0:
        trailing_stop_price = inp.peak_price - (config.atr_multiplier_k * inp.atr_value)
        trailing_stop_hit = inp.current_price <= trailing_stop_price

    be_lock_threat = current_state in (PositionState.BE_LOCK, PositionState.ARMED) and (
        inp.current_price <= inp.entry_price
    )
    model_exit_signal = inp.pred_down_prob >= config.model_prob_threshold and inp.liquidity_weak

    next_state = promote_state(
        current=current_state,
        inp=StateTransitionInput(
            unrealized_pnl_pct=unrealized,
            be_arm_pct=config.be_arm_pct,
            arm_pct=config.arm_pct,
            hard_stop_hit=hard_stop_hit,
            trailing_stop_hit=trailing_stop_hit,
            model_exit_signal=model_exit_signal,
            be_lock_threat=be_lock_threat,
        ),
    )

    if hard_stop_hit:
        reason = "hard_stop"
    elif trailing_stop_hit:
        reason = "atr_trailing_stop"
    elif be_lock_threat:
        reason = "be_lock_threat"
    elif model_exit_signal:
        reason = "model_liquidity_exit"
    elif take_profit_hit:
        # Backward-compatible immediate profit-taking path.
        reason = "arm_take_profit"
    else:
        reason = "hold"

    should_exit = next_state == PositionState.EXITED or take_profit_hit

    return ExitEvaluation(
        state=next_state,
        should_exit=should_exit,
        reason=reason,
        unrealized_pnl_pct=unrealized,
        trailing_stop_price=trailing_stop_price,
    )
