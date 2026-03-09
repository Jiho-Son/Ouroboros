"""Exit state-machine runtime helpers.

Manages runtime exit states, peak tracking, and staged-exit evaluation
for open positions.  Extracted from ``src/main.py``.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from src.analysis.atr_helpers import (
    _compute_kr_atr_value,
    _compute_overseas_atr_value,
    _estimate_pred_down_prob_from_rsi,
)
from src.brain.gemini_client import TradeDecision
from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker
from src.config import Settings
from src.core.session_risk import (
    _compute_kr_dynamic_stop_loss_pct,
    _resolve_market_setting,
)
from src.markets.schedule import MarketInfo
from src.strategy.exit_rules import ExitRuleConfig, ExitRuleInput, evaluate_exit
from src.strategy.position_state_machine import PositionState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Local helpers (avoid circular imports with main.py)
# ---------------------------------------------------------------------------


def _safe_float(value: str | float | None, default: float = 0.0) -> float:
    """Convert to float, handling empty strings and None.

    Local copy of ``src.main.safe_float`` to avoid a circular import
    (main -> exit_manager -> main).
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Module-level globals
# ---------------------------------------------------------------------------

_RUNTIME_EXIT_STATES: dict[str, PositionState] = {}
_RUNTIME_EXIT_PEAKS: dict[str, float] = {}
_STAGED_EXIT_EVIDENCE_KEY = "_staged_exit_evidence"


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def _build_runtime_position_key(
    *,
    market_code: str,
    stock_code: str,
    open_position: dict[str, Any],
) -> str:
    return _build_runtime_position_key_from_fields(
        market_code=market_code,
        stock_code=stock_code,
        decision_id=str(open_position.get("decision_id") or ""),
        position_timestamp=str(open_position.get("timestamp") or ""),
    )


def _build_runtime_position_key_from_fields(
    *,
    market_code: str,
    stock_code: str,
    decision_id: str,
    position_timestamp: str,
) -> str:
    return f"{market_code}:{stock_code}:{decision_id}:{position_timestamp}"


def update_runtime_exit_peak(
    *,
    market_code: str,
    stock_code: str,
    decision_id: str,
    position_timestamp: str,
    entry_price: float,
    last_price: float,
) -> float | None:
    """Raise the cached staged-exit peak for an open position when realtime data improves it."""
    if not math.isfinite(last_price) or last_price <= 0:
        return None

    runtime_key = _build_runtime_position_key_from_fields(
        market_code=market_code,
        stock_code=stock_code,
        decision_id=decision_id,
        position_timestamp=position_timestamp,
    )
    floor_price = entry_price if math.isfinite(entry_price) and entry_price > 0 else 0.0
    candidate_peak = max(floor_price, last_price)
    current_peak = _RUNTIME_EXIT_PEAKS.get(runtime_key, 0.0)
    if candidate_peak <= current_peak:
        return current_peak if current_peak > 0 else None

    _RUNTIME_EXIT_PEAKS[runtime_key] = candidate_peak
    return candidate_peak


def _clear_runtime_exit_cache_for_symbol(*, market_code: str, stock_code: str) -> None:
    prefix = f"{market_code}:{stock_code}:"
    stale_keys = [key for key in _RUNTIME_EXIT_STATES if key.startswith(prefix)]
    for key in stale_keys:
        _RUNTIME_EXIT_STATES.pop(key, None)
        _RUNTIME_EXIT_PEAKS.pop(key, None)


def _record_staged_exit_evidence(
    *,
    market_data: dict[str, Any],
    atr_value: float,
    pred_down_prob: float,
    stop_loss_threshold: float,
    be_arm_pct: float,
    arm_pct: float,
    peak_price: float,
    current_state: PositionState,
    exit_eval: Any,
) -> None:
    """Persist staged-exit inputs/results on market_data for later decision logging."""
    market_data[_STAGED_EXIT_EVIDENCE_KEY] = {
        "atr_value": atr_value,
        "pred_down_prob": pred_down_prob,
        "stop_loss_threshold": stop_loss_threshold,
        "be_arm_pct": be_arm_pct,
        "arm_pct": arm_pct,
        "peak_price": peak_price,
        "current_state": current_state.value,
        "next_state": exit_eval.state.value,
        "reason": exit_eval.reason,
        "should_exit": bool(exit_eval.should_exit),
    }


def _merge_staged_exit_evidence_into_log(
    *,
    market_data: dict[str, Any],
    context_snapshot: dict[str, Any],
    input_data: dict[str, Any],
) -> None:
    """Add staged-exit runtime evidence to decision log payloads when available."""
    raw_evidence = market_data.get(_STAGED_EXIT_EVIDENCE_KEY)
    if not isinstance(raw_evidence, dict):
        return

    input_data.update(
        {
            "atr_value": raw_evidence.get("atr_value", 0.0),
            "pred_down_prob": raw_evidence.get("pred_down_prob", 0.0),
            "stop_loss_threshold": raw_evidence.get("stop_loss_threshold", 0.0),
            "be_arm_pct": raw_evidence.get("be_arm_pct", 0.0),
            "arm_pct": raw_evidence.get("arm_pct", 0.0),
        }
    )
    context_snapshot["staged_exit"] = {
        "peak_price": raw_evidence.get("peak_price", 0.0),
        "current_state": raw_evidence.get("current_state", PositionState.HOLDING.value),
        "next_state": raw_evidence.get("next_state", PositionState.HOLDING.value),
        "reason": raw_evidence.get("reason", "none"),
        "should_exit": bool(raw_evidence.get("should_exit", False)),
    }


def _apply_staged_exit_override_for_hold(
    *,
    decision: TradeDecision,
    market: MarketInfo,
    stock_code: str,
    open_position: dict[str, Any] | None,
    market_data: dict[str, Any],
    stock_playbook: Any | None,
    settings: Settings | None = None,
) -> TradeDecision:
    """Apply v2 staged exit semantics for HOLD positions using runtime state."""
    if decision.action != "HOLD" or not open_position:
        return decision

    entry_price = _safe_float(open_position.get("price"), 0.0)
    current_price = _safe_float(market_data.get("current_price"), 0.0)
    if entry_price <= 0 or current_price <= 0:
        return decision

    stop_loss_threshold = -2.0
    playbook_stop_loss_threshold: float | None = None
    take_profit_threshold = 3.0
    if stock_playbook and stock_playbook.scenarios:
        playbook_stop_loss_threshold = _safe_float(stock_playbook.scenarios[0].stop_loss_pct, -2.0)
        if not math.isfinite(playbook_stop_loss_threshold):
            playbook_stop_loss_threshold = -2.0
        stop_loss_threshold = playbook_stop_loss_threshold
        take_profit_threshold = _safe_float(stock_playbook.scenarios[0].take_profit_pct, 3.0)
        if not math.isfinite(take_profit_threshold):
            take_profit_threshold = 3.0
    atr_value = _safe_float(market_data.get("atr_value"), 0.0)
    if market.code == "KR":
        dynamic_stop_loss_threshold = _compute_kr_dynamic_stop_loss_pct(
            market=market,
            entry_price=entry_price,
            atr_value=atr_value,
            fallback_stop_loss_pct=stop_loss_threshold,
            settings=settings,
        )
        # Keep KR ATR-adaptive behavior, but never loosen beyond an explicit playbook stop.
        if playbook_stop_loss_threshold is not None:
            stop_loss_threshold = max(playbook_stop_loss_threshold, dynamic_stop_loss_threshold)
        else:
            stop_loss_threshold = dynamic_stop_loss_threshold
    if settings is None:
        be_arm_pct = max(0.5, take_profit_threshold * 0.4)
        arm_pct = take_profit_threshold
    else:
        be_arm_pct = max(
            0.1,
            float(
                _resolve_market_setting(
                    market=market,
                    settings=settings,
                    key="STAGED_EXIT_BE_ARM_PCT",
                    default=1.2,
                )
            ),
        )
        arm_pct = max(
            be_arm_pct,
            float(
                _resolve_market_setting(
                    market=market,
                    settings=settings,
                    key="STAGED_EXIT_ARM_PCT",
                    default=3.0,
                )
            ),
        )

    runtime_key = _build_runtime_position_key(
        market_code=market.code,
        stock_code=stock_code,
        open_position=open_position,
    )
    current_state = _RUNTIME_EXIT_STATES.get(runtime_key, PositionState.HOLDING)
    prev_peak = _RUNTIME_EXIT_PEAKS.get(runtime_key, 0.0)
    peak_hint = max(
        _safe_float(market_data.get("peak_price"), 0.0),
        _safe_float(market_data.get("session_high_price"), 0.0),
    )
    peak_price = max(entry_price, current_price, prev_peak, peak_hint)

    exit_eval = evaluate_exit(
        current_state=current_state,
        config=ExitRuleConfig(
            hard_stop_pct=stop_loss_threshold,
            be_arm_pct=be_arm_pct,
            arm_pct=arm_pct,
        ),
        inp=ExitRuleInput(
            current_price=current_price,
            entry_price=entry_price,
            peak_price=peak_price,
            atr_value=atr_value,
            pred_down_prob=_safe_float(market_data.get("pred_down_prob"), 0.0),
            liquidity_weak=_safe_float(market_data.get("volume_ratio"), 1.0) < 1.0,
        ),
    )
    _record_staged_exit_evidence(
        market_data=market_data,
        atr_value=atr_value,
        pred_down_prob=_safe_float(market_data.get("pred_down_prob"), 0.0),
        stop_loss_threshold=stop_loss_threshold,
        be_arm_pct=be_arm_pct,
        arm_pct=arm_pct,
        peak_price=peak_price,
        current_state=current_state,
        exit_eval=exit_eval,
    )
    _RUNTIME_EXIT_STATES[runtime_key] = exit_eval.state
    _RUNTIME_EXIT_PEAKS[runtime_key] = peak_price

    if not exit_eval.should_exit:
        return decision

    pnl_pct = (current_price - entry_price) / entry_price * 100.0
    if exit_eval.reason == "hard_stop":
        rationale = f"Stop-loss triggered ({pnl_pct:.2f}% <= {stop_loss_threshold:.2f}%)"
    elif exit_eval.reason == "arm_take_profit":
        rationale = f"Take-profit triggered ({pnl_pct:.2f}% >= {arm_pct:.2f}%)"
    elif exit_eval.reason == "atr_trailing_stop":
        rationale = "ATR trailing-stop triggered"
    elif exit_eval.reason == "be_lock_threat":
        rationale = "Break-even lock threat detected"
    elif exit_eval.reason == "model_liquidity_exit":
        rationale = "Model/liquidity exit triggered"
    else:
        rationale = f"Exit rule triggered ({exit_eval.reason})"

    logger.info(
        "Staged exit override for %s (%s): HOLD -> SELL (reason=%s, state=%s)",
        stock_code,
        market.name,
        exit_eval.reason,
        exit_eval.state.value,
    )
    return TradeDecision(
        action="SELL",
        confidence=max(decision.confidence, 90),
        rationale=rationale,
    )


async def _inject_staged_exit_features(
    *,
    market: MarketInfo,
    stock_code: str,
    open_position: dict[str, Any] | None,
    market_data: dict[str, Any],
    broker: KISBroker | None,
    overseas_broker: OverseasBroker | None = None,
) -> None:
    """Inject ATR/pred_down_prob used by staged exit evaluation."""
    if not open_position:
        return

    if "pred_down_prob" not in market_data:
        market_data["pred_down_prob"] = _estimate_pred_down_prob_from_rsi(market_data.get("rsi"))

    existing_atr = _safe_float(market_data.get("atr_value"), 0.0)
    if existing_atr > 0:
        return

    if market.is_domestic and broker is not None:
        market_data["atr_value"] = await _compute_kr_atr_value(
            broker=broker,
            stock_code=stock_code,
        )
        return

    if not market.is_domestic and overseas_broker is not None:
        market_data["atr_value"] = await _compute_overseas_atr_value(
            overseas_broker=overseas_broker,
            exchange_code=market.exchange_code,
            stock_code=stock_code,
        )
        return

    market_data["atr_value"] = 0.0
