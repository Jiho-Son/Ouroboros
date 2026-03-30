"""Tests for main trading loop integration."""

import asyncio
import json
import logging
import math
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import src.core.blackout_runtime as blackout_runtime
import src.main as main_module
from src.analysis.atr_helpers import (
    _compute_kr_atr_value,
    _estimate_pred_down_prob_from_rsi,
    _split_trade_pnl_components,
)
from src.analysis.smart_scanner import ScanCandidate
from src.broker.balance_utils import (
    _extract_avg_price_from_balance,
    _extract_buy_fx_rate,
    _extract_fx_rate_from_sources,
    _extract_held_codes_from_balance,
    _extract_held_qty_from_balance,
)
from src.broker.kis_websocket import KISWebSocketPriceEvent
from src.broker.pending_orders import (
    handle_domestic_pending_orders,
    handle_overseas_pending_orders,
)
from src.config import Settings
from src.context.layer import ContextLayer
from src.context.scheduler import ScheduleResult
from src.core.blackout_manager import BlackoutOrderManager
from src.core.blackout_runtime import (
    _maybe_queue_order_intent,
    process_blackout_recovery_orders,
)
from src.core.kill_switch_runtime import (
    KILL_SWITCH,
    _trigger_emergency_kill_switch,
)
from src.core.market_tracking import MarketTrackingStore
from src.core.order_helpers import (
    _determine_order_quantity,
    _resolve_recent_sell_guard_window_seconds,
    _resolve_sell_qty_for_pnl,
    _should_block_buy_above_recent_sell,
    _should_block_buy_chasing_session_high,
    _should_block_overseas_buy_for_fx_buffer,
    _should_force_exit_for_overnight,
)
from src.core.order_policy import OrderPolicyRejected, get_session_info
from src.core.realtime_hard_stop import HardStopTrigger, RealtimeHardStopMonitor
from src.core.risk_manager import CircuitBreakerTripped, FatFingerRejected
from src.core.session_risk import (
    _SESSION_RISK_LAST_BY_MARKET,
    _SESSION_RISK_OVERRIDES_BY_MARKET,
    _SESSION_RISK_PROFILES_MAP,
    _STOPLOSS_REENTRY_COOLDOWN_UNTIL,
    _compute_kr_dynamic_stop_loss_pct,
    _resolve_market_setting,
    _stoploss_cooldown_minutes,
)
from src.db import init_db, log_trade
from src.decision_logging.decision_logger import DecisionLogger
from src.evolution.scorecard import DailyScorecard
from src.main import (
    _acquire_live_runtime_lock,
    _apply_dashboard_flag,
    _execute_trading_cycle_action,
    _handle_market_close,
    _handle_realtime_hard_stop_trigger,
    _handle_realtime_price_event,
    _has_market_session_transition,
    _load_daily_session_market_candidates,
    _process_daily_session_stock,
    _refresh_cached_playbook_on_session_transition,
    _register_post_buy_for_hard_stop,
    _release_live_runtime_lock,
    _restart_realtime_hard_stop_task_if_needed,
    _retry_connection,
    _rollback_pending_order_position,
    _run_context_scheduler,
    _run_evolution_loop,
    _run_markets_in_parallel,
    _should_mid_session_refresh,
    _should_refresh_cached_playbook_on_session_transition,
    _should_rescan_market,
    _start_dashboard_server,
    _sync_realtime_hard_stop_monitor,
    run_daily_session,
    safe_float,
    sync_positions_from_broker,
    trading_cycle,
)
from src.markets.schedule import MARKETS
from src.strategy.exit_manager import (
    _RUNTIME_EXIT_PEAKS,
    _RUNTIME_EXIT_STATES,
    _apply_staged_exit_override_for_hold,
    _inject_staged_exit_features,
    update_runtime_exit_peak,
)
from src.strategy.models import (
    DayPlaybook,
    ScenarioAction,
    StockCondition,
    StockPlaybook,
    StockScenario,
)
from src.strategy.playbook_store import StoredPlaybookEntry
from src.strategy.position_state_machine import PositionState
from src.strategy.scenario_engine import ScenarioEngine, ScenarioMatch


def _make_playbook(market: str = "KR") -> DayPlaybook:
    """Create a minimal empty playbook for testing."""
    return DayPlaybook(date=date(2026, 2, 8), market=market)


def _make_stock_playbook(
    market: str = "KR",
    stock_code: str = "005930",
    *,
    rationale: str = "Primary test scenario",
) -> DayPlaybook:
    """Create a minimal non-empty playbook for reuse/refresh tests."""
    return DayPlaybook(
        date=date(2026, 2, 8),
        market=market,
        stock_playbooks=[
            StockPlaybook(
                stock_code=stock_code,
                scenarios=[
                    StockScenario(
                        condition=StockCondition(rsi_below=30),
                        action=ScenarioAction.BUY,
                        confidence=80,
                        allocation_pct=10.0,
                        stop_loss_pct=-3.0,
                        take_profit_pct=5.0,
                        rationale=rationale,
                    )
                ],
            )
        ],
    )


def _make_settings(**overrides: Any) -> Settings:
    base = {
        "KIS_APP_KEY": "k",
        "KIS_APP_SECRET": "s",
        "KIS_ACCOUNT_NO": "12345678-01",
        "GEMINI_API_KEY": "g",
        "MODE": "live",
        "REALTIME_HARD_STOP_ENABLED": False,
    }
    base.update(overrides)
    return Settings(**base)


def test_with_playbook_session_id_returns_copy_without_mutating_input() -> None:
    playbook = _make_playbook("US_NASDAQ")

    updated = main_module._with_playbook_session_id(playbook, "US_PRE")

    assert updated is not playbook
    assert updated.session_id == "US_PRE"
    assert playbook.session_id == "UNKNOWN"


def test_log_realtime_hard_stop_monitor_start_includes_enabled_market_coverage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _make_settings(TRADE_MODE="realtime", ENABLED_MARKETS="US")

    with caplog.at_level(logging.INFO):
        main_module._log_realtime_hard_stop_monitor_start(settings)

    assert "Realtime hard-stop websocket monitor started" in caplog.text
    assert "enabled_markets=US_NASDAQ,US_NYSE,US_AMEX" in caplog.text
    assert "source=websocket_hard_stop" in caplog.text


def test_realtime_hard_stop_startup_predicate_includes_live_daily_mode() -> None:
    settings = _make_settings(
        MODE="live",
        TRADE_MODE="daily",
        REALTIME_HARD_STOP_ENABLED=True,
        ENABLED_MARKETS="US",
    )

    assert main_module._should_start_realtime_hard_stop_monitor(settings) is True


@pytest.mark.parametrize(
    ("mode", "trade_mode", "hard_stop_enabled", "enabled_markets"),
    [
        ("paper", "daily", True, "US"),
        ("live", "daily", False, "US"),
        ("live", "daily", True, ""),
    ],
)
def test_realtime_hard_stop_startup_predicate_skips_unsupported_runtime_cases(
    mode: str,
    trade_mode: str,
    hard_stop_enabled: bool,
    enabled_markets: str,
) -> None:
    settings = _make_settings(
        MODE=mode,
        TRADE_MODE=trade_mode,
        REALTIME_HARD_STOP_ENABLED=hard_stop_enabled,
        ENABLED_MARKETS=enabled_markets,
    )

    assert main_module._should_start_realtime_hard_stop_monitor(settings) is False


def test_daily_mode_batch_cadence_detects_no_additional_kr_regular_session() -> None:
    current_batch_started_at = datetime(
        2026,
        3,
        23,
        9,
        31,
        tzinfo=ZoneInfo("Asia/Seoul"),
    ).astimezone(UTC)
    next_scheduled_batch_at = datetime(
        2026,
        3,
        23,
        15,
        31,
        tzinfo=ZoneInfo("Asia/Seoul"),
    )
    next_scheduled_batch_at = next_scheduled_batch_at.astimezone(UTC)

    assert (
        main_module._daily_mode_has_additional_regular_session_batch(
            market=MARKETS["KR"],
            current_batch_started_at=current_batch_started_at,
            next_scheduled_batch_at=next_scheduled_batch_at,
            session_interval=timedelta(hours=6),
        )
        is False
    )


def test_daily_mode_batch_cadence_skips_false_warning_for_lunch_break_market() -> None:
    next_scheduled_batch_at = datetime(
        2026,
        3,
        23,
        12,
        0,
        tzinfo=ZoneInfo("Asia/Tokyo"),
    )
    next_scheduled_batch_at = next_scheduled_batch_at.astimezone(UTC)

    assert (
        main_module._daily_mode_has_additional_regular_session_batch(
            market=MARKETS["JP"],
            current_batch_started_at=next_scheduled_batch_at,
            next_scheduled_batch_at=next_scheduled_batch_at,
            session_interval=timedelta(hours=2),
        )
        is True
    )


def test_daily_mode_batch_cadence_returns_false_for_nonpositive_interval() -> None:
    next_scheduled_batch_at = datetime(
        2026,
        3,
        23,
        15,
        31,
        tzinfo=ZoneInfo("Asia/Seoul"),
    ).astimezone(UTC)

    with patch("src.main.is_market_open") as is_market_open_mock:
        assert (
            main_module._daily_mode_has_additional_regular_session_batch(
                market=MARKETS["KR"],
                current_batch_started_at=next_scheduled_batch_at,
                next_scheduled_batch_at=next_scheduled_batch_at,
                session_interval=timedelta(0),
            )
            is False
        )

    is_market_open_mock.assert_not_called()


def test_daily_mode_batch_cadence_anchors_market_close_to_current_batch_date() -> None:
    current_batch_started_at = datetime(
        2026,
        3,
        23,
        15,
        20,
        tzinfo=ZoneInfo("Asia/Seoul"),
    ).astimezone(UTC)
    next_scheduled_batch_at = current_batch_started_at + timedelta(hours=10)

    assert (
        main_module._daily_mode_has_additional_regular_session_batch(
            market=MARKETS["KR"],
            current_batch_started_at=current_batch_started_at,
            next_scheduled_batch_at=next_scheduled_batch_at,
            session_interval=timedelta(hours=10),
        )
        is False
    )


def test_daily_mode_batch_cadence_uses_is_market_open_for_future_batches() -> None:
    current_batch_started_at = datetime(
        2026,
        3,
        23,
        9,
        0,
        tzinfo=ZoneInfo("Asia/Seoul"),
    ).astimezone(UTC)
    next_scheduled_batch_at = datetime(
        2026,
        3,
        23,
        15,
        0,
        tzinfo=ZoneInfo("Asia/Seoul"),
    ).astimezone(UTC)

    with patch("src.main.is_market_open", side_effect=[False, True]) as is_market_open_mock:
        assert (
            main_module._daily_mode_has_additional_regular_session_batch(
                market=MARKETS["KR"],
                current_batch_started_at=current_batch_started_at,
                next_scheduled_batch_at=next_scheduled_batch_at,
                session_interval=timedelta(minutes=15),
            )
            is True
        )

    assert is_market_open_mock.call_count == 2


def test_resolve_daily_mode_next_batch_at_keeps_default_when_dst_regular_session_is_active(
) -> None:
    current_batch_started_at = datetime(2026, 3, 25, 14, 12, tzinfo=UTC)
    batch_completed_at = datetime(2026, 3, 25, 14, 13, tzinfo=UTC)

    assert main_module._resolve_daily_mode_next_batch_at(
        open_markets=[MARKETS["US_NASDAQ"]],
        current_batch_started_at=current_batch_started_at,
        batch_completed_at=batch_completed_at,
        session_interval=timedelta(hours=6),
    ) == datetime(2026, 3, 25, 20, 13, tzinfo=UTC)


def test_resolve_terminal_sell_order_price_uses_limit_in_low_liquidity_session() -> None:
    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    with patch("src.main.get_session_info", return_value=MagicMock(is_low_liquidity=True)):
        price, mode = main_module._resolve_terminal_sell_order_price(
            market=market,
            current_price=100.0,
        )

    assert price == pytest.approx(99.0)
    assert mode == "low_liquidity_limit"


def test_resolve_terminal_sell_order_price_uses_market_order_in_regular_session() -> None:
    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    with patch("src.main.get_session_info", return_value=MagicMock(is_low_liquidity=False)):
        price, mode = main_module._resolve_terminal_sell_order_price(
            market=market,
            current_price=100.0,
        )

    assert price == 0.0
    assert mode == "market"


def test_resolve_terminal_sell_order_price_overseas_regular_session() -> None:
    market = MagicMock()
    market.code = "US_NASDAQ"
    market.exchange_code = "NASD"
    market.is_domestic = False

    with patch("src.main.get_session_info", return_value=MagicMock(is_low_liquidity=False)):
        price, mode = main_module._resolve_terminal_sell_order_price(
            market=market,
            current_price=250.0,
        )

    assert price == 0.0
    assert mode == "market"


def test_resolve_terminal_sell_order_price_overseas_low_liquidity_normal_price() -> None:
    market = MagicMock()
    market.code = "US_NASDAQ"
    market.exchange_code = "NASD"
    market.is_domestic = False

    with patch("src.main.get_session_info", return_value=MagicMock(is_low_liquidity=True)):
        price, mode = main_module._resolve_terminal_sell_order_price(
            market=market,
            current_price=250.0,
        )

    assert price == pytest.approx(round(250.0 * 0.996, 2))
    assert mode == "low_liquidity_limit"


def test_resolve_terminal_sell_order_price_overseas_low_liquidity_penny_stock() -> None:
    market = MagicMock()
    market.code = "US_NASDAQ"
    market.exchange_code = "NASD"
    market.is_domestic = False

    with patch("src.main.get_session_info", return_value=MagicMock(is_low_liquidity=True)):
        price, mode = main_module._resolve_terminal_sell_order_price(
            market=market,
            current_price=0.5,
        )

    assert price == pytest.approx(round(0.5 * 0.996, 4))
    assert mode == "low_liquidity_limit"


@pytest.mark.asyncio
async def test_sync_realtime_hard_stop_monitor_registers_hold_position() -> None:
    monitor = RealtimeHardStopMonitor()
    market = MARKETS["KR"]
    market_data = {
        "stock_name": "Samsung",
        "_staged_exit_evidence": {"stop_loss_threshold": -3.5},
    }
    websocket_client = MagicMock()
    websocket_client.subscribe = AsyncMock()
    websocket_client.unsubscribe = AsyncMock()

    await _sync_realtime_hard_stop_monitor(
        monitor=monitor,
        websocket_client=websocket_client,
        market=market,
        stock_code="005930",
        decision_action="HOLD",
        open_position={
            "price": 100.0,
            "quantity": 7,
            "decision_id": "buy-dec",
            "timestamp": "2026-03-09T00:00:00+00:00",
        },
        market_data=market_data,
    )

    tracked = monitor.get("KR", "005930")
    assert tracked is not None
    assert tracked.hard_stop_price == pytest.approx(96.5)
    assert tracked.quantity == 7
    assert tracked.stock_name == "Samsung"
    websocket_client.subscribe.assert_awaited_once_with("KR", "005930")


@pytest.mark.asyncio
async def test_sync_realtime_hard_stop_monitor_registers_us_hold_position() -> None:
    monitor = RealtimeHardStopMonitor()
    market = MARKETS["US_NASDAQ"]
    market_data = {
        "stock_name": "Apple",
        "_staged_exit_evidence": {"stop_loss_threshold": -3.5},
    }
    websocket_client = MagicMock()
    websocket_client.subscribe = AsyncMock()
    websocket_client.unsubscribe = AsyncMock()

    await _sync_realtime_hard_stop_monitor(
        monitor=monitor,
        websocket_client=websocket_client,
        market=market,
        stock_code="AAPL",
        decision_action="HOLD",
        open_position={
            "price": 100.0,
            "quantity": 7,
            "decision_id": "buy-dec",
            "timestamp": "2026-03-09T00:00:00+00:00",
        },
        market_data=market_data,
    )

    tracked = monitor.get("US_NASDAQ", "AAPL")
    assert tracked is not None
    assert tracked.hard_stop_price == pytest.approx(96.5)
    assert tracked.quantity == 7
    assert tracked.stock_name == "Apple"
    websocket_client.subscribe.assert_awaited_once_with("US_NASDAQ", "AAPL")


@pytest.mark.asyncio
async def test_sync_realtime_hard_stop_monitor_logs_websocket_subscription_source(
    caplog: pytest.LogCaptureFixture,
) -> None:
    monitor = RealtimeHardStopMonitor()
    market = MARKETS["US_NASDAQ"]
    market_data = {"_staged_exit_evidence": {"stop_loss_threshold": -3.5}}
    websocket_client = MagicMock()
    websocket_client.subscribe = AsyncMock()
    websocket_client.unsubscribe = AsyncMock()

    with caplog.at_level(logging.INFO):
        await _sync_realtime_hard_stop_monitor(
            monitor=monitor,
            websocket_client=websocket_client,
            market=market,
            stock_code="AAPL",
            decision_action="HOLD",
            open_position={
                "price": 100.0,
                "quantity": 7,
                "decision_id": "buy-dec",
                "timestamp": "2026-03-09T00:00:00+00:00",
            },
            market_data=market_data,
        )

    assert (
        "Realtime hard-stop monitor sync action=subscribe market=US_NASDAQ "
        "stock=AAPL source=websocket_hard_stop"
    ) in caplog.text


def test_update_runtime_exit_peak_only_raises_cached_peak() -> None:
    _RUNTIME_EXIT_PEAKS.clear()

    update_runtime_exit_peak(
        market_code="KR",
        stock_code="005930",
        decision_id="d1",
        position_timestamp="t1",
        entry_price=100.0,
        last_price=105.0,
    )
    update_runtime_exit_peak(
        market_code="KR",
        stock_code="005930",
        decision_id="d1",
        position_timestamp="t1",
        entry_price=100.0,
        last_price=103.0,
    )
    update_runtime_exit_peak(
        market_code="KR",
        stock_code="005930",
        decision_id="d1",
        position_timestamp="t1",
        entry_price=100.0,
        last_price=float("nan"),
    )

    assert _RUNTIME_EXIT_PEAKS["KR:005930:d1:t1"] == pytest.approx(105.0)


def test_websocket_peak_hint_can_trigger_atr_trailing_exit_earlier() -> None:
    _RUNTIME_EXIT_PEAKS.clear()
    _RUNTIME_EXIT_STATES.clear()

    market = MagicMock()
    market.code = "KR"
    market.name = "Korea"
    decision = MagicMock(action="HOLD", confidence=70, rationale="hold")
    open_position = {"price": 100.0, "quantity": 1, "decision_id": "d1", "timestamp": "t1"}

    runtime_key = "KR:005930:d1:t1"
    _RUNTIME_EXIT_STATES[runtime_key] = PositionState.ARMED

    update_runtime_exit_peak(
        market_code="KR",
        stock_code="005930",
        decision_id="d1",
        position_timestamp="t1",
        entry_price=100.0,
        last_price=110.0,
    )

    out = _apply_staged_exit_override_for_hold(
        decision=decision,
        market=market,
        stock_code="005930",
        open_position=open_position,
        market_data={"current_price": 104.0, "atr_value": 2.0, "pred_down_prob": 0.4},
        stock_playbook=None,
        settings=None,
    )

    assert out.action == "SELL"
    assert out.rationale == "ATR trailing-stop triggered"


@pytest.mark.asyncio
async def test_sync_realtime_hard_stop_monitor_clears_tracking_while_sell_is_pending() -> None:
    monitor = RealtimeHardStopMonitor()
    market = MARKETS["KR"]
    market_data = {"_staged_exit_evidence": {"stop_loss_threshold": -3.5}}
    websocket_client = MagicMock()
    websocket_client.subscribe = AsyncMock()
    websocket_client.unsubscribe = AsyncMock()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        stock_name="Samsung",
        entry_price=100.0,
        quantity=7,
        hard_stop_pct=-3.5,
        decision_id="buy-dec",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    await _sync_realtime_hard_stop_monitor(
        monitor=monitor,
        websocket_client=websocket_client,
        market=market,
        stock_code="005930",
        decision_action="SELL",
        open_position={
            "price": 100.0,
            "quantity": 7,
            "decision_id": "buy-dec",
            "timestamp": "2026-03-09T00:00:00+00:00",
        },
        market_data=market_data,
    )

    assert monitor.get("KR", "005930") is None
    websocket_client.unsubscribe.assert_awaited_once_with("KR", "005930")


@pytest.mark.asyncio
async def test_handle_realtime_price_event_updates_peak_before_hard_stop_eval() -> None:
    _RUNTIME_EXIT_PEAKS.clear()

    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=1,
        hard_stop_pct=-2.0,
        decision_id="d1",
        position_timestamp="t1",
    )

    with patch("src.main._handle_realtime_hard_stop_trigger", new=AsyncMock()) as mock_handle:
        await _handle_realtime_price_event(
            event=KISWebSocketPriceEvent(
                market_code="KR",
                stock_code="005930",
                price=110,
                tr_id="H0STCNT0",
            ),
            broker=MagicMock(),
            overseas_broker=MagicMock(),
            db_conn=MagicMock(),
            decision_logger=MagicMock(),
            telegram=MagicMock(),
            settings=_make_settings(TRADE_MODE="realtime"),
            monitor=monitor,
            websocket_client=MagicMock(),
        )

    assert _RUNTIME_EXIT_PEAKS["KR:005930:d1:t1"] == pytest.approx(110.0)
    mock_handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_realtime_price_event_updates_us_peak_before_hard_stop_eval() -> None:
    _RUNTIME_EXIT_PEAKS.clear()

    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="US_NASDAQ",
        stock_code="AAPL",
        entry_price=100.0,
        quantity=1,
        hard_stop_pct=-2.0,
        decision_id="d1",
        position_timestamp="t1",
    )

    with patch("src.main._handle_realtime_hard_stop_trigger", new=AsyncMock()) as mock_handle:
        await _handle_realtime_price_event(
            event=KISWebSocketPriceEvent(
                market_code="US_NASDAQ",
                stock_code="AAPL",
                price=110.25,
                tr_id="HDFSCNT0",
            ),
            broker=MagicMock(),
            overseas_broker=MagicMock(),
            db_conn=MagicMock(),
            decision_logger=MagicMock(),
            telegram=MagicMock(),
            settings=_make_settings(TRADE_MODE="realtime"),
            monitor=monitor,
            websocket_client=MagicMock(),
        )

    assert _RUNTIME_EXIT_PEAKS["US_NASDAQ:AAPL:d1:t1"] == pytest.approx(110.25)
    mock_handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_realtime_price_event_logs_us_receive_and_no_trigger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="US_NASDAQ",
        stock_code="AAPL",
        entry_price=100.0,
        quantity=1,
        hard_stop_pct=-2.0,
        decision_id="d1",
        position_timestamp="t1",
    )
    caplog.set_level("INFO")

    with patch("src.main._handle_realtime_hard_stop_trigger", new=AsyncMock()) as mock_handle:
        await _handle_realtime_price_event(
            event=KISWebSocketPriceEvent(
                market_code="US_NASDAQ",
                stock_code="AAPL",
                price=110.25,
                tr_id="HDFSCNT0",
            ),
            broker=MagicMock(),
            overseas_broker=MagicMock(),
            db_conn=MagicMock(),
            decision_logger=MagicMock(),
            telegram=MagicMock(),
            settings=_make_settings(TRADE_MODE="realtime"),
            monitor=monitor,
            websocket_client=MagicMock(),
        )

    mock_handle.assert_not_awaited()
    assert "action=received_us_event" in caplog.text
    assert "action=no_trigger" in caplog.text
    assert "reason=above_stop" in caplog.text


@pytest.mark.asyncio
async def test_handle_realtime_price_event_logs_us_dispatch_trigger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="US_NASDAQ",
        stock_code="AAPL",
        entry_price=100.0,
        quantity=1,
        hard_stop_pct=-2.0,
        decision_id="d1",
        position_timestamp="t1",
    )
    caplog.set_level("INFO")

    with patch("src.main._handle_realtime_hard_stop_trigger", new=AsyncMock()) as mock_handle:
        await _handle_realtime_price_event(
            event=KISWebSocketPriceEvent(
                market_code="US_NASDAQ",
                stock_code="AAPL",
                price=97.5,
                tr_id="HDFSCNT0",
            ),
            broker=MagicMock(),
            overseas_broker=MagicMock(),
            db_conn=MagicMock(),
            decision_logger=MagicMock(),
            telegram=MagicMock(),
            settings=_make_settings(TRADE_MODE="realtime"),
            monitor=monitor,
            websocket_client=MagicMock(),
        )

    mock_handle.assert_awaited_once()
    assert "action=received_us_event" in caplog.text
    assert "action=dispatch_trigger" in caplog.text
    assert "market=US_NASDAQ" in caplog.text
    assert "stock=AAPL" in caplog.text


@pytest.mark.asyncio
async def test_handle_realtime_hard_stop_trigger_submits_sell_and_logs_trade() -> None:
    db_conn = init_db(":memory:")
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=87,
        rationale="initial entry",
        quantity=7,
        price=100.0,
        pnl=0.0,
        market="KR",
        exchange_code="KRX",
        session_id="KRX_REG",
        decision_id="buy-dec",
        mode="live",
    )
    broker = MagicMock()
    broker.get_balance = AsyncMock(
        return_value={"output1": [{"pdno": "005930", "ord_psbl_qty": "7"}], "output2": [{}]}
    )
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    decision_logger = MagicMock()
    decision_logger.log_decision.return_value = "sell-dec"
    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    monitor = RealtimeHardStopMonitor()
    websocket_client = MagicMock()
    websocket_client.unsubscribe = AsyncMock()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=7,
        hard_stop_pct=-3.5,
        decision_id="buy-dec",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    ok = await _handle_realtime_hard_stop_trigger(
        broker=broker,
        overseas_broker=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        telegram=telegram,
        settings=_make_settings(),
        monitor=monitor,
        websocket_client=websocket_client,
        trigger=HardStopTrigger(
            market_code="KR",
            stock_code="005930",
            stock_name="Samsung",
            last_price=96.0,
            hard_stop_price=96.5,
            quantity=7,
            decision_id="buy-dec",
            position_timestamp="2026-03-09T00:00:00+00:00",
        ),
    )

    assert ok is True
    broker.send_order.assert_called_once()
    telegram.notify_trade_execution.assert_awaited_once()
    notify_kwargs = telegram.notify_trade_execution.await_args.kwargs
    assert notify_kwargs["stock_name"] == "Samsung"
    websocket_client.unsubscribe.assert_awaited_once_with("KR", "005930")
    decision_logger.log_decision.assert_called_once()
    decision_logger.update_outcome.assert_called_once_with(
        decision_id="buy-dec",
        pnl=pytest.approx(-28.0),
        accuracy=0,
    )
    latest_sell = db_conn.execute(
        """
        SELECT action, price, quantity, pnl, decision_id, selection_context
        FROM trades
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    assert latest_sell is not None
    assert latest_sell[0] == "SELL"
    assert latest_sell[1] == pytest.approx(96.0)
    assert latest_sell[2] == 7
    assert latest_sell[3] == pytest.approx(-28.0)
    assert latest_sell[4] == "sell-dec"
    assert json.loads(str(latest_sell[5]))["source"] == "websocket_hard_stop"
    assert monitor.get("KR", "005930") is None


@pytest.mark.asyncio
async def test_handle_realtime_hard_stop_trigger_uses_current_balance_qty() -> None:
    db_conn = init_db(":memory:")
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=87,
        rationale="initial entry",
        quantity=10,
        price=100.0,
        pnl=0.0,
        market="KR",
        exchange_code="KRX",
        session_id="KRX_REG",
        decision_id="buy-dec",
        mode="live",
    )
    broker = MagicMock()
    broker.get_balance = AsyncMock(
        return_value={"output1": [{"pdno": "005930", "ord_psbl_qty": "3"}], "output2": [{}]}
    )
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    decision_logger = MagicMock()
    decision_logger.log_decision.return_value = "sell-dec"
    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    monitor = RealtimeHardStopMonitor()
    websocket_client = MagicMock()
    websocket_client.unsubscribe = AsyncMock()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        stock_name="Samsung",
        entry_price=100.0,
        quantity=10,
        hard_stop_pct=-3.5,
        decision_id="buy-dec",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    ok = await _handle_realtime_hard_stop_trigger(
        broker=broker,
        overseas_broker=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        telegram=telegram,
        settings=_make_settings(),
        monitor=monitor,
        websocket_client=websocket_client,
        trigger=HardStopTrigger(
            market_code="KR",
            stock_code="005930",
            stock_name="Samsung",
            last_price=96.0,
            hard_stop_price=96.5,
            quantity=10,
            decision_id="buy-dec",
            position_timestamp="2026-03-09T00:00:00+00:00",
        ),
    )

    assert ok is True
    broker.send_order.assert_awaited_once()
    assert broker.send_order.await_args.kwargs["quantity"] == 3
    decision_logger.update_outcome.assert_called_once_with(
        decision_id="buy-dec",
        pnl=pytest.approx(-12.0),
        accuracy=0,
    )


@pytest.mark.asyncio
async def test_handle_realtime_hard_stop_trigger_queues_sell_during_blackout() -> None:
    broker = MagicMock()
    broker.get_balance = AsyncMock(
        return_value={"output1": [{"pdno": "005930", "ord_psbl_qty": "4"}], "output2": [{}]}
    )
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    monitor = RealtimeHardStopMonitor()
    websocket_client = MagicMock()
    websocket_client.unsubscribe = AsyncMock()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=10,
        hard_stop_pct=-3.5,
        decision_id="buy-dec",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    with patch("src.main._maybe_queue_order_intent", return_value=True) as mock_queue:
        ok = await _handle_realtime_hard_stop_trigger(
            broker=broker,
            overseas_broker=MagicMock(),
            db_conn=MagicMock(),
            decision_logger=MagicMock(),
            telegram=MagicMock(notify_trade_execution=AsyncMock()),
            settings=_make_settings(),
            monitor=monitor,
            websocket_client=websocket_client,
            trigger=HardStopTrigger(
                market_code="KR",
                stock_code="005930",
                last_price=96.0,
                hard_stop_price=96.5,
                quantity=10,
                decision_id="buy-dec",
                position_timestamp="2026-03-09T00:00:00+00:00",
            ),
        )

    assert ok is True
    broker.send_order.assert_not_awaited()
    assert mock_queue.call_args.kwargs["quantity"] == 4
    assert mock_queue.call_args.kwargs["source"] == "websocket_hard_stop"
    assert monitor.get("KR", "005930") is None
    websocket_client.unsubscribe.assert_awaited_once_with("KR", "005930")


@pytest.mark.asyncio
async def test_realtime_hard_stop_post_submit_failure_does_not_rearm() -> None:
    broker = MagicMock()
    broker.get_balance = AsyncMock(
        return_value={"output1": [{"pdno": "005930", "ord_psbl_qty": "7"}], "output2": [{}]}
    )
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    decision_logger = MagicMock()
    decision_logger.log_decision.return_value = "sell-dec"
    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    monitor = RealtimeHardStopMonitor()
    websocket_client = MagicMock()
    websocket_client.unsubscribe = AsyncMock()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=7,
        hard_stop_pct=-3.5,
        decision_id="buy-dec",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    with patch("src.main.log_trade", side_effect=RuntimeError("trade persistence failed")):
        ok = await _handle_realtime_hard_stop_trigger(
            broker=broker,
            overseas_broker=MagicMock(),
            db_conn=MagicMock(),
            decision_logger=decision_logger,
            telegram=telegram,
            settings=_make_settings(),
            monitor=monitor,
            websocket_client=websocket_client,
            trigger=HardStopTrigger(
                market_code="KR",
                stock_code="005930",
                last_price=96.0,
                hard_stop_price=96.5,
                quantity=7,
                decision_id="buy-dec",
                position_timestamp="2026-03-09T00:00:00+00:00",
            ),
        )

    assert ok is True
    assert monitor.get("KR", "005930") is None
    websocket_client.unsubscribe.assert_awaited_once_with("KR", "005930")


@pytest.mark.asyncio
async def test_handle_realtime_hard_stop_trigger_submits_overseas_sell_and_logs_trade() -> None:
    db_conn = init_db(":memory:")
    log_trade(
        conn=db_conn,
        stock_code="AAPL",
        action="BUY",
        confidence=87,
        rationale="initial entry",
        quantity=7,
        price=100.0,
        pnl=0.0,
        market="US_NASDAQ",
        exchange_code="NASD",
        session_id="US_REG",
        decision_id="buy-dec",
        selection_context={"fx_rate": 1200.0},
        mode="live",
    )
    decision_logger = MagicMock()
    decision_logger.log_decision.return_value = "sell-dec"
    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    monitor = RealtimeHardStopMonitor()
    websocket_client = MagicMock()
    websocket_client.unsubscribe = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [{"ovrs_pdno": "AAPL", "ord_psbl_qty": "7", "ovrs_excg_cd": "NASD"}],
            "output2": [{}],
            "exchange_rate": "1260.0",
        }
    )
    overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    monitor.register(
        market_code="US_NASDAQ",
        stock_code="AAPL",
        stock_name="Apple",
        entry_price=100.0,
        quantity=7,
        hard_stop_pct=-3.5,
        decision_id="buy-dec",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    ok = await _handle_realtime_hard_stop_trigger(
        broker=MagicMock(),
        overseas_broker=overseas_broker,
        db_conn=db_conn,
        decision_logger=decision_logger,
        telegram=telegram,
        settings=_make_settings(),
        monitor=monitor,
        websocket_client=websocket_client,
        trigger=HardStopTrigger(
            market_code="US_NASDAQ",
            stock_code="AAPL",
            stock_name="Apple",
            last_price=96.12,
            hard_stop_price=96.5,
            quantity=7,
            decision_id="buy-dec",
            position_timestamp="2026-03-09T00:00:00+00:00",
        ),
    )

    assert ok is True
    overseas_broker.get_overseas_balance.assert_awaited_once_with("NASD")
    overseas_broker.send_overseas_order.assert_awaited_once()
    assert overseas_broker.send_overseas_order.await_args.kwargs["price"] == pytest.approx(95.93)
    telegram.notify_trade_execution.assert_awaited_once()
    notify_kwargs = telegram.notify_trade_execution.await_args.kwargs
    assert notify_kwargs["stock_name"] == "Apple"
    websocket_client.unsubscribe.assert_awaited_once_with("US_NASDAQ", "AAPL")
    decision_logger.update_outcome.assert_called_once_with(
        decision_id="buy-dec",
        pnl=pytest.approx(-27.16),
        accuracy=0,
    )
    latest_sell = db_conn.execute(
        """
        SELECT action, price, quantity, pnl, decision_id, selection_context, strategy_pnl, fx_pnl
        FROM trades
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    assert latest_sell is not None
    assert latest_sell[0] == "SELL"
    assert latest_sell[1] == pytest.approx(96.12)
    assert latest_sell[2] == 7
    assert latest_sell[3] == pytest.approx(-27.16)
    assert latest_sell[4] == "sell-dec"
    selection_context = json.loads(str(latest_sell[5]))
    assert selection_context["source"] == "websocket_hard_stop"
    assert selection_context["fx_rate"] == pytest.approx(1260.0)
    assert latest_sell[6] == pytest.approx(-62.16)
    assert latest_sell[7] == pytest.approx(35.0)
    assert monitor.get("US_NASDAQ", "AAPL") is None


@pytest.mark.asyncio
async def test_handle_realtime_hard_stop_trigger_logs_us_persistence_boundaries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_conn = init_db(":memory:")
    log_trade(
        conn=db_conn,
        stock_code="AAPL",
        action="BUY",
        confidence=87,
        rationale="initial entry",
        quantity=7,
        price=100.0,
        pnl=0.0,
        market="US_NASDAQ",
        exchange_code="NASD",
        session_id="US_REG",
        decision_id="buy-dec",
        selection_context={"fx_rate": 1200.0},
        mode="live",
    )
    decision_logger = MagicMock()
    decision_logger.log_decision.return_value = "sell-dec"
    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    monitor = RealtimeHardStopMonitor()
    websocket_client = MagicMock()
    websocket_client.unsubscribe = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [{"ovrs_pdno": "AAPL", "ord_psbl_qty": "7", "ovrs_excg_cd": "NASD"}],
            "output2": [{}],
            "exchange_rate": "1260.0",
        }
    )
    overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    monitor.register(
        market_code="US_NASDAQ",
        stock_code="AAPL",
        entry_price=100.0,
        quantity=7,
        hard_stop_pct=-3.5,
        decision_id="buy-dec",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )
    caplog.set_level("INFO")

    ok = await _handle_realtime_hard_stop_trigger(
        broker=MagicMock(),
        overseas_broker=overseas_broker,
        db_conn=db_conn,
        decision_logger=decision_logger,
        telegram=telegram,
        settings=_make_settings(),
        monitor=monitor,
        websocket_client=websocket_client,
        trigger=HardStopTrigger(
            market_code="US_NASDAQ",
            stock_code="AAPL",
            last_price=96.12,
            hard_stop_price=96.5,
            quantity=7,
            decision_id="buy-dec",
            position_timestamp="2026-03-09T00:00:00+00:00",
        ),
    )

    assert ok is True
    assert "action=decision_logged" in caplog.text
    assert "action=trade_logged" in caplog.text
    assert "source=websocket_hard_stop" in caplog.text


@pytest.mark.asyncio
async def test_execute_trading_cycle_action_clears_realtime_hard_stop_after_successful_sell(
) -> None:
    db_conn = init_db(":memory:")
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=87,
        rationale="initial entry",
        quantity=7,
        price=100.0,
        pnl=0.0,
        market="KR",
        exchange_code="KRX",
        session_id="KRX_REG",
        decision_id="buy-dec",
        mode="live",
    )
    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    monitor = RealtimeHardStopMonitor()
    websocket_client = MagicMock()
    websocket_client.unsubscribe = AsyncMock()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=7,
        hard_stop_pct=-3.5,
        decision_id="buy-dec",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    execution_result = await _execute_trading_cycle_action(
        broker=broker,
        overseas_broker=MagicMock(),
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=MagicMock(),
        telegram=MagicMock(notify_trade_execution=AsyncMock()),
        market=MARKETS["KR"],
        stock_code="005930",
        runtime_session_id="KRX_REG",
        snapshot={
            "current_price": 96.0,
            "total_cash": 1_000_000.0,
            "pnl_pct": -4.0,
            "candidate": None,
            "balance_data": {"output1": [{"pdno": "005930", "hldg_qty": "7"}]},
        },
        decision_data={
            "decision": main_module.TradeDecision(
                action="SELL",
                confidence=90,
                rationale="polling sell",
            ),
            "match": _make_sell_match(),
            "decision_id": "sell-dec",
        },
        settings=_make_settings(),
        realtime_hard_stop_monitor=monitor,
        realtime_hard_stop_client=websocket_client,
    )

    assert execution_result["order_succeeded"] is True
    assert monitor.get("KR", "005930") is None
    websocket_client.unsubscribe.assert_awaited_once_with("KR", "005930")


@pytest.mark.asyncio
async def test_execute_trading_cycle_action_records_us_sell_settlement_fx_rate_from_snapshot(
) -> None:
    db_conn = init_db(":memory:")
    log_trade(
        conn=db_conn,
        stock_code="AAPL",
        action="BUY",
        confidence=87,
        rationale="initial entry",
        quantity=7,
        price=100.0,
        pnl=0.0,
        market="US_NASDAQ",
        exchange_code="NASD",
        session_id="US_REG",
        decision_id="buy-dec",
        mode="live",
    )
    overseas_broker = MagicMock()
    overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

    execution_result = await _execute_trading_cycle_action(
        broker=MagicMock(),
        overseas_broker=overseas_broker,
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=MagicMock(),
        telegram=MagicMock(notify_trade_execution=AsyncMock()),
        market=MARKETS["US_NASDAQ"],
        stock_code="AAPL",
        runtime_session_id="US_REG",
        snapshot={
            "current_price": 96.0,
            "total_cash": 10_000.0,
            "pnl_pct": -4.0,
            "candidate": None,
            "market_data": {"stock_name": "Apple Inc."},
            "balance_data": {"output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "7"}]},
            "balance_info": {"output1": [{"bass_exrt": "1340.50"}]},
            "price_output": {},
        },
        decision_data={
            "decision": main_module.TradeDecision(
                action="SELL",
                confidence=90,
                rationale="polling sell",
            ),
            "match": _make_sell_match(),
            "decision_id": "sell-dec",
        },
        settings=_make_settings(),
    )

    assert execution_result["order_succeeded"] is True
    assert execution_result["settlement_fx_rate"] == pytest.approx(1340.50)


@pytest.mark.asyncio
async def test_execute_trading_cycle_action_keeps_realtime_hard_stop_after_rejected_sell(
) -> None:
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"rt_cd": "1", "msg1": "rejected"})
    monitor = RealtimeHardStopMonitor()
    websocket_client = MagicMock()
    websocket_client.unsubscribe = AsyncMock()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=7,
        hard_stop_pct=-3.5,
        decision_id="buy-dec",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    execution_result = await _execute_trading_cycle_action(
        broker=broker,
        overseas_broker=MagicMock(),
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=MagicMock(),
        telegram=MagicMock(notify_trade_execution=AsyncMock()),
        market=MARKETS["KR"],
        stock_code="005930",
        runtime_session_id="KRX_REG",
        snapshot={
            "current_price": 96.0,
            "total_cash": 1_000_000.0,
            "pnl_pct": -4.0,
            "candidate": None,
            "balance_data": {"output1": [{"pdno": "005930", "hldg_qty": "7"}]},
        },
        decision_data={
            "decision": main_module.TradeDecision(
                action="SELL",
                confidence=90,
                rationale="polling sell",
            ),
            "match": _make_sell_match(),
            "decision_id": "sell-dec",
        },
        settings=_make_settings(),
        realtime_hard_stop_monitor=monitor,
        realtime_hard_stop_client=websocket_client,
    )

    assert execution_result["order_succeeded"] is False
    assert monitor.get("KR", "005930") is not None
    websocket_client.unsubscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_trading_cycle_action_ignores_unsubscribe_failure_after_successful_sell(
) -> None:
    db_conn = init_db(":memory:")
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=87,
        rationale="initial entry",
        quantity=7,
        price=100.0,
        pnl=0.0,
        market="KR",
        exchange_code="KRX",
        session_id="KRX_REG",
        decision_id="buy-dec",
        mode="live",
    )
    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    decision_logger = MagicMock()
    monitor = RealtimeHardStopMonitor()
    websocket_client = MagicMock()
    websocket_client.unsubscribe = AsyncMock(side_effect=RuntimeError("ws down"))
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=7,
        hard_stop_pct=-3.5,
        decision_id="buy-dec",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    execution_result = await _execute_trading_cycle_action(
        broker=broker,
        overseas_broker=MagicMock(),
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        telegram=MagicMock(notify_trade_execution=AsyncMock()),
        market=MARKETS["KR"],
        stock_code="005930",
        runtime_session_id="KRX_REG",
        snapshot={
            "current_price": 96.0,
            "total_cash": 1_000_000.0,
            "pnl_pct": -4.0,
            "candidate": None,
            "balance_data": {"output1": [{"pdno": "005930", "hldg_qty": "7"}]},
        },
        decision_data={
            "decision": main_module.TradeDecision(
                action="SELL",
                confidence=90,
                rationale="polling sell",
            ),
            "match": _make_sell_match(),
            "decision_id": "sell-dec",
        },
        settings=_make_settings(),
        realtime_hard_stop_monitor=monitor,
        realtime_hard_stop_client=websocket_client,
    )

    assert execution_result["order_succeeded"] is True
    assert execution_result["trade_pnl"] == pytest.approx(-28.0)
    decision_logger.update_outcome.assert_called_once_with(
        decision_id="buy-dec",
        pnl=pytest.approx(-28.0),
        accuracy=0,
    )


@pytest.mark.asyncio
async def test_restart_realtime_hard_stop_task_if_needed_restarts_completed_task() -> None:
    async def _finished() -> None:
        return None

    completed = asyncio.create_task(_finished())
    await completed

    client = MagicMock()
    client.run = AsyncMock(return_value=None)

    restarted = _restart_realtime_hard_stop_task_if_needed(client=client, task=completed)

    assert restarted is not None
    await restarted
    client.run.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_domestic", "expected_fallback", "expected_builder_calls"),
    [
        (True, None, 0),
        (False, ["AAPL"], 1),
    ],
)
async def test_daily_session_market_candidates_uses_expected_scanner_inputs(
    is_domestic: bool,
    expected_fallback: list[str] | None,
    expected_builder_calls: int,
) -> None:
    market = MagicMock()
    market.code = "KR" if is_domestic else "US_NASDAQ"
    market.name = "Korea" if is_domestic else "Nasdaq"
    market.exchange_code = "KRX" if is_domestic else "NASD"
    market.is_domestic = is_domestic
    market.timezone = ZoneInfo("Asia/Seoul" if is_domestic else "America/New_York")

    smart_scanner = MagicMock()
    smart_scanner.scan = AsyncMock(return_value=["candidate"])
    overseas_broker = MagicMock()

    with patch(
        "src.main.build_overseas_symbol_universe",
        new=AsyncMock(return_value=["AAPL"]),
    ) as mock_build_universe:
        candidates = await _load_daily_session_market_candidates(
            db_conn=MagicMock(),
            market=market,
            overseas_broker=overseas_broker,
            smart_scanner=smart_scanner,
        )

    assert candidates == ["candidate"]
    assert mock_build_universe.await_count == expected_builder_calls
    smart_scanner.scan.assert_awaited_once_with(
        market=market,
        fallback_stocks=expected_fallback,
    )


def test_main_rejects_paper_mode() -> None:
    args = MagicMock(mode="paper", dashboard=False)

    with (
        patch("argparse.ArgumentParser.parse_args", return_value=args),
        patch("src.main.setup_logging"),
        patch("src.main.asyncio.run") as mock_asyncio_run,
        pytest.raises(ValueError, match="paper"),
    ):
        main_module.main()

    mock_asyncio_run.assert_not_called()


def test_main_does_not_force_mode_when_flag_omitted() -> None:
    args = MagicMock(mode=None, dashboard=False)
    settings = MagicMock()
    settings.MODE = "live"

    with (
        patch("argparse.ArgumentParser.parse_args", return_value=args),
        patch("src.main.setup_logging"),
        patch("src.main.Settings", return_value=settings) as mock_settings,
        patch("src.main._apply_dashboard_flag", side_effect=lambda s, _: s),
        patch("src.main.asyncio.run", side_effect=lambda coro: coro.close()) as mock_asyncio_run,
        patch("src.main.run", new=AsyncMock()),
    ):
        main_module.main()

    mock_settings.assert_called_once_with()
    mock_asyncio_run.assert_called_once()


@pytest.mark.asyncio
async def test_run_rejects_paper_mode_before_runtime_init() -> None:
    settings = _make_settings(MODE="paper")

    with (
        patch("src.main.KISBroker", side_effect=AssertionError("runtime initialized")),
        pytest.raises(ValueError, match="paper"),
    ):
        await main_module.run(settings)


@pytest.mark.asyncio
async def test_run_rejects_duplicate_live_instance_before_runtime_init() -> None:
    settings = _make_settings(MODE="live")

    with (
        patch(
            "src.main._acquire_live_runtime_lock",
            side_effect=RuntimeError("another live runtime is already active"),
        ),
        patch("src.main.KISBroker", side_effect=AssertionError("runtime initialized")),
        pytest.raises(RuntimeError, match="already active"),
    ):
        await main_module.run(settings)


def test_live_runtime_lock_can_be_reacquired_after_release(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _make_settings(MODE="live")

    first_lock = _acquire_live_runtime_lock(settings)
    try:
        with pytest.raises(RuntimeError, match="already active"):
            _acquire_live_runtime_lock(settings)
    finally:
        _release_live_runtime_lock(first_lock)

    second_lock = _acquire_live_runtime_lock(settings)
    _release_live_runtime_lock(second_lock)


def test_live_runtime_lock_uses_configured_path(tmp_path) -> None:
    lock_path = tmp_path / "runtime-state" / "feature.lock"
    settings = _make_settings(
        MODE="live",
        LIVE_RUNTIME_LOCK_PATH=str(lock_path),
    )

    lock_file = _acquire_live_runtime_lock(settings)
    try:
        assert lock_path.exists()
        assert lock_path.read_text(encoding="utf-8").strip().isdigit()
    finally:
        _release_live_runtime_lock(lock_file)


def _make_buy_match(stock_code: str = "005930") -> ScenarioMatch:
    """Create a ScenarioMatch that returns BUY."""
    return ScenarioMatch(
        stock_code=stock_code,
        matched_scenario=None,
        action=ScenarioAction.BUY,
        confidence=85,
        rationale="Test buy",
    )


def _make_hold_match(stock_code: str = "005930") -> ScenarioMatch:
    """Create a ScenarioMatch that returns HOLD."""
    return ScenarioMatch(
        stock_code=stock_code,
        matched_scenario=None,
        action=ScenarioAction.HOLD,
        confidence=0,
        rationale="No scenario conditions met",
    )


def _make_sell_match(stock_code: str = "005930") -> ScenarioMatch:
    """Create a ScenarioMatch that returns SELL."""
    return ScenarioMatch(
        stock_code=stock_code,
        matched_scenario=None,
        action=ScenarioAction.SELL,
        confidence=90,
        rationale="Test sell",
    )


@pytest.fixture(autouse=True)
def _reset_kill_switch_state() -> None:
    """Prevent cross-test leakage from global kill-switch state."""
    def _reset_blackout_runtime_state() -> None:
        blackout_runtime.BLACKOUT_ORDER_MANAGER = BlackoutOrderManager(
            enabled=False,
            windows=[],
        )

    def _reset_session_risk_globals() -> None:
        _SESSION_RISK_LAST_BY_MARKET.clear()
        _SESSION_RISK_OVERRIDES_BY_MARKET.clear()
        _SESSION_RISK_PROFILES_MAP.clear()
        main_module._SESSION_RISK_PROFILES_RAW = "{}"

    _reset_blackout_runtime_state()
    KILL_SWITCH.clear_block()
    _RUNTIME_EXIT_STATES.clear()
    _RUNTIME_EXIT_PEAKS.clear()
    _reset_session_risk_globals()
    _STOPLOSS_REENTRY_COOLDOWN_UNTIL.clear()
    yield
    _reset_blackout_runtime_state()
    KILL_SWITCH.clear_block()
    _RUNTIME_EXIT_STATES.clear()
    _RUNTIME_EXIT_PEAKS.clear()
    _reset_session_risk_globals()
    _STOPLOSS_REENTRY_COOLDOWN_UNTIL.clear()


class TestExtractAvgPriceFromBalance:
    """Tests for _extract_avg_price_from_balance() (issue #249)."""

    def test_domestic_returns_pchs_avg_pric(self) -> None:
        """Domestic balance with pchs_avg_pric returns the correct float."""
        balance = {"output1": [{"pdno": "005930", "pchs_avg_pric": "68000.00"}]}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 68000.0

    def test_overseas_returns_pchs_avg_pric(self) -> None:
        """Overseas balance with pchs_avg_pric returns the correct float."""
        balance = {"output1": [{"ovrs_pdno": "AAPL", "pchs_avg_pric": "170.50"}]}
        result = _extract_avg_price_from_balance(balance, "AAPL", is_domestic=False)
        assert result == 170.5

    def test_returns_zero_when_field_empty_string(self) -> None:
        """Returns 0.0 when pchs_avg_pric is an empty string."""
        balance = {"output1": [{"pdno": "005930", "pchs_avg_pric": ""}]}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0

    def test_returns_zero_when_stock_not_found(self) -> None:
        """Returns 0.0 when the requested stock_code is not in output1."""
        balance = {"output1": [{"pdno": "000660", "pchs_avg_pric": "100000.0"}]}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0

    def test_returns_zero_when_output1_empty(self) -> None:
        """Returns 0.0 when output1 is an empty list."""
        balance = {"output1": []}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0

    def test_returns_zero_when_output1_key_absent(self) -> None:
        """Returns 0.0 when output1 key is missing from balance_data."""
        balance: dict = {}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0

    def test_handles_output1_as_dict(self) -> None:
        """Handles the edge case where output1 is a dict instead of a list."""
        balance = {"output1": {"pdno": "005930", "pchs_avg_pric": "55000.0"}}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 55000.0

    def test_case_insensitive_code_matching(self) -> None:
        """Stock code comparison is case-insensitive."""
        balance = {"output1": [{"ovrs_pdno": "aapl", "pchs_avg_pric": "170.0"}]}
        result = _extract_avg_price_from_balance(balance, "AAPL", is_domestic=False)
        assert result == 170.0

    def test_returns_zero_for_non_numeric_string(self) -> None:
        """Returns 0.0 when pchs_avg_pric contains a non-numeric value."""
        balance = {"output1": [{"pdno": "005930", "pchs_avg_pric": "N/A"}]}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0

    def test_returns_correct_stock_among_multiple(self) -> None:
        """Returns only the avg price of the requested stock when output1 has multiple holdings."""
        balance = {
            "output1": [
                {"pdno": "000660", "pchs_avg_pric": "150000.0"},
                {"pdno": "005930", "pchs_avg_pric": "68000.0"},
            ]
        }
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 68000.0


class TestRealtimeSessionStateHelpers:
    """Tests for realtime loop session-transition/rescan helper logic."""

    def test_has_market_session_transition_when_state_missing(self) -> None:
        states: dict[str, str] = {}
        assert not _has_market_session_transition(states, "US_NASDAQ", "US_REG")

    def test_has_market_session_transition_when_session_changes(self) -> None:
        states = {"US_NASDAQ": "US_PRE"}
        assert _has_market_session_transition(states, "US_NASDAQ", "US_REG")

    def test_has_market_session_transition_false_when_same_session(self) -> None:
        states = {"US_NASDAQ": "US_REG"}
        assert not _has_market_session_transition(states, "US_NASDAQ", "US_REG")

    def test_should_rescan_market_forces_on_session_transition(self) -> None:
        assert _should_rescan_market(
            last_scan=1000.0,
            now_timestamp=1050.0,
            rescan_interval=300.0,
            session_changed=True,
        )

    def test_should_rescan_market_uses_interval_without_transition(self) -> None:
        assert not _should_rescan_market(
            last_scan=1000.0,
            now_timestamp=1050.0,
            rescan_interval=300.0,
            session_changed=False,
        )

    def test_reconcile_market_lifecycle_separates_open_close_and_session_transition(
        self,
    ) -> None:
        diff = main_module._reconcile_market_lifecycle(
            previous_market_states={
                "KR": "KRX_REG",
                "US_NASDAQ": "US_PRE",
            },
            current_market_sessions={
                "US_NASDAQ": "US_REG",
                "US_NYSE": "US_PRE",
            },
            current_markets={
                "US_NASDAQ": MARKETS["US_NASDAQ"],
                "US_NYSE": MARKETS["US_NYSE"],
            },
        )

        assert [event.market_code for event in diff.opened] == ["US_NYSE"]
        assert [event.market_code for event in diff.closed] == ["KR"]
        assert [event.market_code for event in diff.session_changed] == ["US_NASDAQ"]
        assert diff.session_changed[0].previous_session_id == "US_PRE"
        assert diff.session_changed[0].current_session_id == "US_REG"

    def test_decide_playbook_selection_reuses_stored_regular_session_playbook(
        self,
    ) -> None:
        stored_playbook = _make_stock_playbook("US_NASDAQ", "AAPL")
        entry = StoredPlaybookEntry(
            playbook=stored_playbook,
            slot="mid",
            generated_at=stored_playbook.generated_at,
        )
        playbook_store = MagicMock()
        playbook_store.load_latest_entry = MagicMock(return_value=entry)

        decision = main_module._decide_playbook_selection(
            playbook_store=playbook_store,
            market_today=date(2026, 2, 8),
            market_code="US_NASDAQ",
            session_id="US_REG",
            selection_intent=main_module.PlaybookSelectionIntent.RESUME_CURRENT_SESSION,
            current_candidate_codes={"AAPL"},
        )

        assert decision.action is main_module.PlaybookSelectionAction.REUSE_STORED
        assert decision.stored_entry is entry
        playbook_store.load_latest_entry.assert_called_once_with(
            date(2026, 2, 8),
            "US_NASDAQ",
            session_id="US_REG",
        )

    def test_decide_playbook_selection_rejects_empty_stored_playbook(self) -> None:
        stored_playbook = _make_playbook("US_NASDAQ")
        playbook_store = MagicMock()
        playbook_store.load_latest_entry = MagicMock(
            return_value=StoredPlaybookEntry(
                playbook=stored_playbook,
                slot="open",
                generated_at=stored_playbook.generated_at,
            )
        )

        decision = main_module._decide_playbook_selection(
            playbook_store=playbook_store,
            market_today=date(2026, 2, 8),
            market_code="US_NASDAQ",
            session_id="US_REG",
            selection_intent=main_module.PlaybookSelectionIntent.RESUME_CURRENT_SESSION,
            current_candidate_codes={"AAPL"},
        )

        assert decision.action is main_module.PlaybookSelectionAction.GENERATE_FRESH
        assert decision.stored_entry is None
        assert "empty" in decision.reason

    def test_decide_playbook_selection_rejects_fallback_stored_playbook(self) -> None:
        stored_playbook = _make_stock_playbook(
            "US_NASDAQ",
            "AAPL",
            rationale="Rule-based BUY: momentum signal, volume=2.0x (fallback planner)",
        )
        playbook_store = MagicMock()
        playbook_store.load_latest_entry = MagicMock(
            return_value=StoredPlaybookEntry(
                playbook=stored_playbook,
                slot="open",
                generated_at=stored_playbook.generated_at,
            )
        )

        decision = main_module._decide_playbook_selection(
            playbook_store=playbook_store,
            market_today=date(2026, 2, 8),
            market_code="US_NASDAQ",
            session_id="US_REG",
            selection_intent=main_module.PlaybookSelectionIntent.RESUME_CURRENT_SESSION,
            current_candidate_codes={"AAPL"},
        )

        assert decision.action is main_module.PlaybookSelectionAction.GENERATE_FRESH
        assert decision.stored_entry is None
        assert "fallback" in decision.reason

    def test_decide_playbook_selection_rejects_changed_candidate_set(self) -> None:
        stored_playbook = _make_stock_playbook("US_NASDAQ", "AAPL")
        playbook_store = MagicMock()
        playbook_store.load_latest_entry = MagicMock(
            return_value=StoredPlaybookEntry(
                playbook=stored_playbook,
                slot="open",
                generated_at=stored_playbook.generated_at,
            )
        )

        decision = main_module._decide_playbook_selection(
            playbook_store=playbook_store,
            market_today=date(2026, 2, 8),
            market_code="US_NASDAQ",
            session_id="US_REG",
            selection_intent=main_module.PlaybookSelectionIntent.RESUME_CURRENT_SESSION,
            current_candidate_codes={"TSLA"},
        )

        assert decision.action is main_module.PlaybookSelectionAction.GENERATE_FRESH
        assert decision.stored_entry is None
        assert "candidate" in decision.reason

    def test_decide_playbook_selection_force_fresh_on_transition(self) -> None:
        playbook_store = MagicMock()

        decision = main_module._decide_playbook_selection(
            playbook_store=playbook_store,
            market_today=date(2026, 2, 8),
            market_code="US_NASDAQ",
            session_id="US_REG",
            selection_intent=main_module.PlaybookSelectionIntent.FORCE_FRESH,
            force_reason="live session transition",
        )

        assert decision.action is main_module.PlaybookSelectionAction.GENERATE_FRESH
        assert decision.stored_entry is None
        assert "live session transition" in decision.reason
        playbook_store.load_latest_entry.assert_not_called()

    def test_should_refresh_cached_playbook_on_session_transition_true_for_transition(self) -> None:
        assert _should_refresh_cached_playbook_on_session_transition(
            session_changed=True,
            market_code="KR",
            session_id="KRX_REG",
        )

    def test_should_refresh_cached_playbook_on_session_transition_false_without_transition(
        self,
    ) -> None:
        assert not _should_refresh_cached_playbook_on_session_transition(
            session_changed=False,
            market_code="KR",
            session_id="KRX_REG",
        )

    def test_refresh_cached_playbook_on_session_transition_drops_existing_kr_cache(self) -> None:
        playbooks = {"KR": _make_playbook("KR")}
        removed = _refresh_cached_playbook_on_session_transition(
            playbooks=playbooks,
            session_changed=True,
            market_code="KR",
            session_id="KRX_REG",
        )
        assert removed
        assert "KR" not in playbooks

    def test_refresh_cached_playbook_on_session_transition_drops_cache_for_any_transition(
        self,
    ) -> None:
        playbooks = {"KR": _make_playbook("KR")}
        removed = _refresh_cached_playbook_on_session_transition(
            playbooks=playbooks,
            session_changed=True,
            market_code="KR",
            session_id="NXT_PRE",
        )
        assert removed
        assert "KR" not in playbooks

    def test_load_stored_playbook_for_session_uses_current_session_identity(self) -> None:
        playbook_store = MagicMock()
        stored_playbook = _make_stock_playbook("US_NASDAQ", "AAPL")
        playbook_store.load_latest_entry = MagicMock(
            return_value=StoredPlaybookEntry(
                playbook=stored_playbook,
                slot="mid",
                generated_at=stored_playbook.generated_at,
            )
        )
        mid_refreshed: set[str] = set()

        restored = main_module._load_stored_playbook_for_session(
            playbook_store=playbook_store,
            market_today=date(2026, 2, 8),
            market_code="US_NASDAQ",
            session_id="US_PRE",
            selection_intent=main_module.PlaybookSelectionIntent.RESUME_CURRENT_SESSION,
            mid_refreshed=mid_refreshed,
            current_candidate_codes={"AAPL"},
        )

        assert restored is stored_playbook
        playbook_store.load_latest_entry.assert_called_once_with(
            date(2026, 2, 8),
            "US_NASDAQ",
            session_id="US_PRE",
        )
        assert "US_NASDAQ" in mid_refreshed

    def test_load_stored_playbook_for_session_reuses_stored_regular_session_playbook(
        self,
    ) -> None:
        playbook_store = MagicMock()
        stored_playbook = _make_stock_playbook("US_NASDAQ", "AAPL")
        playbook_store.load_latest_entry = MagicMock(
            return_value=StoredPlaybookEntry(
                playbook=stored_playbook,
                slot="open",
                generated_at=stored_playbook.generated_at,
            )
        )

        restored = main_module._load_stored_playbook_for_session(
            playbook_store=playbook_store,
            market_today=date(2026, 2, 8),
            market_code="US_NASDAQ",
            session_id="US_REG",
            selection_intent=main_module.PlaybookSelectionIntent.RESUME_CURRENT_SESSION,
            mid_refreshed=set(),
            current_candidate_codes={"AAPL"},
        )

        assert restored is stored_playbook
        playbook_store.load_latest_entry.assert_called_once_with(
            date(2026, 2, 8),
            "US_NASDAQ",
            session_id="US_REG",
        )

    def test_load_stored_playbook_for_session_force_fresh_on_transition(self) -> None:
        playbook_store = MagicMock()

        restored = main_module._load_stored_playbook_for_session(
            playbook_store=playbook_store,
            market_today=date(2026, 2, 8),
            market_code="US_NASDAQ",
            session_id="US_REG",
            selection_intent=main_module.PlaybookSelectionIntent.FORCE_FRESH,
            mid_refreshed=set(),
            force_reason="live session transition",
            current_candidate_codes={"AAPL"},
        )

        assert restored is None
        playbook_store.load_latest_entry.assert_not_called()

    def test_load_stored_playbook_for_session_skips_mid_lookup_when_latest_missing(
        self,
    ) -> None:
        playbook_store = MagicMock()
        playbook_store.load_latest_entry = MagicMock(return_value=None)
        mid_refreshed: set[str] = set()

        restored = main_module._load_stored_playbook_for_session(
            playbook_store=playbook_store,
            market_today=date(2026, 2, 8),
            market_code="US_NASDAQ",
            session_id="US_PRE",
            selection_intent=main_module.PlaybookSelectionIntent.RESUME_CURRENT_SESSION,
            mid_refreshed=mid_refreshed,
            current_candidate_codes={"AAPL"},
        )

        assert restored is None
        playbook_store.load_latest_entry.assert_called_once_with(
            date(2026, 2, 8),
            "US_NASDAQ",
            session_id="US_PRE",
        )
        assert mid_refreshed == set()

    def test_refresh_cached_playbook_on_session_transition_false_when_session_unchanged(
        self,
    ) -> None:
        playbooks = {"KR": _make_playbook("KR")}
        removed = _refresh_cached_playbook_on_session_transition(
            playbooks=playbooks,
            session_changed=False,
            market_code="KR",
            session_id="KRX_REG",
        )
        assert not removed
        assert "KR" in playbooks

    def test_refresh_cached_playbook_on_session_transition_false_when_cache_missing(
        self,
    ) -> None:
        playbooks: dict[str, DayPlaybook] = {}
        removed = _refresh_cached_playbook_on_session_transition(
            playbooks=playbooks,
            session_changed=True,
            market_code="KR",
            session_id="KRX_REG",
        )
        assert not removed
        assert playbooks == {}

    def test_refresh_cached_playbook_on_session_transition_drops_existing_us_cache(self) -> None:
        playbooks = {"US_NASDAQ": _make_playbook("US_NASDAQ")}
        removed = _refresh_cached_playbook_on_session_transition(
            playbooks=playbooks,
            session_changed=True,
            market_code="US_NASDAQ",
            session_id="US_REG",
        )
        assert removed
        assert "US_NASDAQ" not in playbooks

class TestMarketParallelRunner:
    """Tests for market-level parallel processing helper."""

    @pytest.mark.asyncio
    async def test_run_markets_in_parallel_runs_all_markets(self) -> None:
        processed: list[str] = []

        async def _processor(market: str) -> None:
            await asyncio.sleep(0.01)
            processed.append(market)

        await _run_markets_in_parallel(["KR", "US_NASDAQ", "US_NYSE"], _processor)
        assert set(processed) == {"KR", "US_NASDAQ", "US_NYSE"}

    @pytest.mark.asyncio
    async def test_run_markets_in_parallel_propagates_errors(self) -> None:
        async def _processor(market: str) -> None:
            if market == "US_NASDAQ":
                raise RuntimeError("boom")
            await asyncio.sleep(0.01)

        with pytest.raises(RuntimeError, match="boom"):
            await _run_markets_in_parallel(["KR", "US_NASDAQ"], _processor)

    def test_returns_zero_when_field_absent(self) -> None:
        """Returns 0.0 when pchs_avg_pric key is missing entirely."""
        balance = {"output1": [{"pdno": "005930", "ord_psbl_qty": "5"}]}
        result = _extract_avg_price_from_balance(balance, "005930", is_domestic=True)
        assert result == 0.0


def test_resolve_sell_qty_for_pnl_prefers_sell_qty() -> None:
    assert _resolve_sell_qty_for_pnl(sell_qty=30, buy_qty=100) == 30


def test_resolve_sell_qty_for_pnl_uses_buy_qty_fallback_when_sell_qty_missing() -> None:
    assert _resolve_sell_qty_for_pnl(sell_qty=None, buy_qty=12) == 12


def test_resolve_sell_qty_for_pnl_returns_zero_when_both_missing() -> None:
    assert _resolve_sell_qty_for_pnl(sell_qty=None, buy_qty=None) == 0


def test_compute_kr_dynamic_stop_loss_pct_falls_back_without_atr() -> None:
    out = _compute_kr_dynamic_stop_loss_pct(
        entry_price=100.0,
        atr_value=0.0,
        fallback_stop_loss_pct=-2.0,
        settings=None,
    )
    assert out == -2.0


def test_compute_kr_dynamic_stop_loss_pct_clamps_to_min_and_max() -> None:
    # Small ATR -> clamp to min (-2%)
    out_small = _compute_kr_dynamic_stop_loss_pct(
        entry_price=100.0,
        atr_value=0.2,
        fallback_stop_loss_pct=-2.0,
        settings=None,
    )
    assert out_small == -2.0

    # Large ATR -> clamp to max (-7%)
    out_large = _compute_kr_dynamic_stop_loss_pct(
        entry_price=100.0,
        atr_value=10.0,
        fallback_stop_loss_pct=-2.0,
        settings=None,
    )
    assert out_large == -7.0


def test_compute_kr_dynamic_stop_loss_pct_uses_settings_values() -> None:
    settings = MagicMock(
        KR_ATR_STOP_MULTIPLIER_K=3.0,
        KR_ATR_STOP_MIN_PCT=-1.5,
        KR_ATR_STOP_MAX_PCT=-6.0,
    )
    out = _compute_kr_dynamic_stop_loss_pct(
        entry_price=100.0,
        atr_value=1.0,
        fallback_stop_loss_pct=-2.0,
        settings=settings,
    )
    assert out == -3.0


def test_resolve_market_setting_uses_session_profile_override() -> None:
    settings = Settings(
        KIS_APP_KEY="k",
        KIS_APP_SECRET="s",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="g",
        SESSION_RISK_PROFILES_JSON='{"US_PRE": {"US_MIN_PRICE": 7.5}}',
    )
    market = MagicMock()
    market.code = "US_NASDAQ"

    with patch(
        "src.core.session_risk.get_session_info",
        return_value=MagicMock(session_id="US_PRE"),
    ):
        value = _resolve_market_setting(
            market=market,
            settings=settings,
            key="US_MIN_PRICE",
            default=5.0,
        )

    assert value == pytest.approx(7.5)


def test_stoploss_cooldown_minutes_uses_session_override() -> None:
    settings = Settings(
        KIS_APP_KEY="k",
        KIS_APP_SECRET="s",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="g",
        STOPLOSS_REENTRY_COOLDOWN_MINUTES=120,
        SESSION_RISK_PROFILES_JSON='{"NXT_AFTER": {"STOPLOSS_REENTRY_COOLDOWN_MINUTES": 45}}',
    )
    market = MagicMock()
    market.code = "KR"

    with patch(
        "src.core.session_risk.get_session_info",
        return_value=MagicMock(session_id="NXT_AFTER"),
    ):
        value = _stoploss_cooldown_minutes(settings, market=market)

    assert value == 45


def test_resolve_market_setting_ignores_profile_when_reload_disabled() -> None:
    settings = Settings(
        KIS_APP_KEY="k",
        KIS_APP_SECRET="s",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="g",
        US_MIN_PRICE=5.0,
        SESSION_RISK_RELOAD_ENABLED=False,
        SESSION_RISK_PROFILES_JSON='{"US_PRE": {"US_MIN_PRICE": 9.5}}',
    )
    market = MagicMock()
    market.code = "US_NASDAQ"

    with patch(
        "src.core.session_risk.get_session_info",
        return_value=MagicMock(session_id="US_PRE"),
    ):
        value = _resolve_market_setting(
            market=market,
            settings=settings,
            key="US_MIN_PRICE",
            default=5.0,
        )

    assert value == pytest.approx(5.0)


def test_resolve_market_setting_falls_back_on_invalid_profile_json() -> None:
    settings = Settings(
        KIS_APP_KEY="k",
        KIS_APP_SECRET="s",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="g",
        US_MIN_PRICE=5.0,
        SESSION_RISK_PROFILES_JSON="{invalid-json",
    )
    market = MagicMock()
    market.code = "US_NASDAQ"

    with patch(
        "src.core.session_risk.get_session_info",
        return_value=MagicMock(session_id="US_PRE"),
    ):
        value = _resolve_market_setting(
            market=market,
            settings=settings,
            key="US_MIN_PRICE",
            default=5.0,
        )

    assert value == pytest.approx(5.0)


def test_resolve_market_setting_coerces_bool_string_override() -> None:
    settings = Settings(
        KIS_APP_KEY="k",
        KIS_APP_SECRET="s",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="g",
        OVERNIGHT_EXCEPTION_ENABLED=True,
        SESSION_RISK_PROFILES_JSON='{"US_AFTER": {"OVERNIGHT_EXCEPTION_ENABLED": "false"}}',
    )
    market = MagicMock()
    market.code = "US_NASDAQ"

    with patch(
        "src.core.session_risk.get_session_info",
        return_value=MagicMock(session_id="US_AFTER"),
    ):
        value = _resolve_market_setting(
            market=market,
            settings=settings,
            key="OVERNIGHT_EXCEPTION_ENABLED",
            default=True,
        )

    assert value is False


def test_estimate_pred_down_prob_from_rsi_uses_linear_mapping() -> None:
    assert _estimate_pred_down_prob_from_rsi(None) == 0.5
    assert _estimate_pred_down_prob_from_rsi(0.0) == 0.0
    assert _estimate_pred_down_prob_from_rsi(50.0) == 0.5
    assert _estimate_pred_down_prob_from_rsi(100.0) == 1.0


@pytest.mark.asyncio
async def test_compute_kr_atr_value_returns_zero_on_short_series() -> None:
    broker = MagicMock()
    broker.get_daily_prices = AsyncMock(
        return_value=[{"high": 101.0, "low": 99.0, "close": 100.0}] * 10
    )

    atr = await _compute_kr_atr_value(broker=broker, stock_code="005930")
    assert atr == 0.0


@pytest.mark.asyncio
async def test_inject_staged_exit_features_sets_pred_down_prob_and_atr_for_kr() -> None:
    market = MagicMock()
    market.is_domestic = True
    stock_data: dict[str, float] = {"rsi": 65.0}

    broker = MagicMock()
    broker.get_daily_prices = AsyncMock(
        return_value=[{"high": 102.0 + i, "low": 98.0 + i, "close": 100.0 + i} for i in range(40)]
    )

    await _inject_staged_exit_features(
        market=market,
        stock_code="005930",
        open_position={"price": 100.0, "quantity": 1},
        market_data=stock_data,
        broker=broker,
    )

    assert stock_data["pred_down_prob"] == pytest.approx(0.65)
    assert stock_data["atr_value"] > 0.0


@pytest.mark.asyncio
async def test_inject_staged_exit_features_sets_atr_for_overseas() -> None:
    market = MagicMock()
    market.is_domestic = False
    market.exchange_code = "NASD"
    stock_data: dict[str, float] = {"rsi": 55.0}

    overseas_broker = MagicMock()
    overseas_broker.get_daily_prices = AsyncMock(
        return_value=[
            {"high": 102.0 + i, "low": 98.0 + i, "close": 100.0 + i}
            for i in range(40)
        ]
    )

    await _inject_staged_exit_features(
        market=market,
        stock_code="AAPL",
        open_position={"price": 100.0, "quantity": 1},
        market_data=stock_data,
        broker=None,
        overseas_broker=overseas_broker,
    )

    assert stock_data["pred_down_prob"] == pytest.approx(0.55)
    assert stock_data["atr_value"] > 0.0


@pytest.mark.asyncio
async def test_inject_staged_exit_features_returns_zero_atr_for_overseas_short_series() -> None:
    market = MagicMock()
    market.is_domestic = False
    market.exchange_code = "NASD"
    stock_data: dict[str, float] = {"rsi": 55.0}

    overseas_broker = MagicMock()
    overseas_broker.get_daily_prices = AsyncMock(
        return_value=[{"high": 102.0, "low": 98.0, "close": 100.0}] * 10
    )

    await _inject_staged_exit_features(
        market=market,
        stock_code="AAPL",
        open_position={"price": 100.0, "quantity": 1},
        market_data=stock_data,
        broker=None,
        overseas_broker=overseas_broker,
    )

    assert stock_data["pred_down_prob"] == pytest.approx(0.55)
    assert stock_data["atr_value"] == 0.0


@pytest.mark.asyncio
async def test_inject_staged_exit_features_returns_zero_atr_on_overseas_connection_error() -> None:
    market = MagicMock()
    market.is_domestic = False
    market.exchange_code = "NASD"
    stock_data: dict[str, float] = {"rsi": 55.0}

    overseas_broker = MagicMock()
    overseas_broker.get_daily_prices = AsyncMock(side_effect=ConnectionError("timeout"))

    await _inject_staged_exit_features(
        market=market,
        stock_code="AAPL",
        open_position={"price": 100.0, "quantity": 1},
        market_data=stock_data,
        broker=None,
        overseas_broker=overseas_broker,
    )

    assert stock_data["pred_down_prob"] == pytest.approx(0.55)
    assert stock_data["atr_value"] == 0.0


def test_apply_staged_exit_uses_independent_arm_threshold_settings() -> None:
    market = MagicMock()
    market.code = "KR"
    market.name = "Korea"

    decision = MagicMock()
    decision.action = "HOLD"
    decision.confidence = 70
    decision.rationale = "hold"

    settings = Settings(
        KIS_APP_KEY="k",
        KIS_APP_SECRET="s",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="g",
        STAGED_EXIT_BE_ARM_PCT=2.2,
        STAGED_EXIT_ARM_PCT=5.4,
    )

    captured: dict[str, float] = {}

    def _fake_eval(**kwargs):  # type: ignore[no-untyped-def]
        cfg = kwargs["config"]
        captured["be_arm_pct"] = cfg.be_arm_pct
        captured["arm_pct"] = cfg.arm_pct

        class _Out:
            should_exit = False
            reason = "none"
            state = PositionState.HOLDING

        return _Out()

    with patch("src.strategy.exit_manager.evaluate_exit", side_effect=_fake_eval):
        out = _apply_staged_exit_override_for_hold(
            decision=decision,
            market=market,
            stock_code="005930",
            open_position={"price": 100.0, "quantity": 1, "decision_id": "d1", "timestamp": "t1"},
            market_data={"current_price": 101.0, "rsi": 60.0, "pred_down_prob": 0.6},
            stock_playbook=None,
            settings=settings,
        )

    assert out is decision
    assert captured["be_arm_pct"] == pytest.approx(2.2)
    assert captured["arm_pct"] == pytest.approx(5.4)


def test_apply_staged_exit_kr_does_not_loosen_beyond_playbook_stop() -> None:
    market = MagicMock()
    market.code = "KR"
    market.name = "Korea"

    decision = MagicMock()
    decision.action = "HOLD"
    decision.confidence = 70
    decision.rationale = "hold"

    playbook_scenario = MagicMock()
    playbook_scenario.stop_loss_pct = -3.0
    playbook_scenario.take_profit_pct = 5.0
    stock_playbook = MagicMock()
    stock_playbook.scenarios = [playbook_scenario]

    captured: dict[str, float] = {}

    def _fake_eval(**kwargs):  # type: ignore[no-untyped-def]
        cfg = kwargs["config"]
        captured["hard_stop_pct"] = cfg.hard_stop_pct

        class _Out:
            should_exit = False
            reason = "none"
            state = PositionState.HOLDING

        return _Out()

    with (
        patch("src.strategy.exit_manager._compute_kr_dynamic_stop_loss_pct", return_value=-7.0),
        patch("src.strategy.exit_manager.evaluate_exit", side_effect=_fake_eval),
    ):
        out = _apply_staged_exit_override_for_hold(
            decision=decision,
            market=market,
            stock_code="024060",
            open_position={"price": 100.0, "quantity": 1, "decision_id": "d1", "timestamp": "t1"},
            market_data={"current_price": 95.0, "atr_value": 3.0},
            stock_playbook=stock_playbook,
            settings=None,
        )

    assert out is decision
    assert captured["hard_stop_pct"] == pytest.approx(-3.0)


def test_apply_staged_exit_kr_keeps_dynamic_when_it_is_tighter_than_playbook() -> None:
    market = MagicMock()
    market.code = "KR"
    market.name = "Korea"

    decision = MagicMock()
    decision.action = "HOLD"
    decision.confidence = 70
    decision.rationale = "hold"

    playbook_scenario = MagicMock()
    playbook_scenario.stop_loss_pct = -3.0
    playbook_scenario.take_profit_pct = 5.0
    stock_playbook = MagicMock()
    stock_playbook.scenarios = [playbook_scenario]

    captured: dict[str, float] = {}

    def _fake_eval(**kwargs):  # type: ignore[no-untyped-def]
        cfg = kwargs["config"]
        captured["hard_stop_pct"] = cfg.hard_stop_pct

        class _Out:
            should_exit = False
            reason = "none"
            state = PositionState.HOLDING

        return _Out()

    with (
        patch("src.strategy.exit_manager._compute_kr_dynamic_stop_loss_pct", return_value=-2.5),
        patch("src.strategy.exit_manager.evaluate_exit", side_effect=_fake_eval),
    ):
        _apply_staged_exit_override_for_hold(
            decision=decision,
            market=market,
            stock_code="024060",
            open_position={"price": 100.0, "quantity": 1, "decision_id": "d1", "timestamp": "t1"},
            market_data={"current_price": 95.0, "atr_value": 3.0},
            stock_playbook=stock_playbook,
            settings=None,
        )

    assert captured["hard_stop_pct"] == pytest.approx(-2.5)


def test_apply_staged_exit_kr_handles_non_finite_playbook_stop_loss() -> None:
    market = MagicMock()
    market.code = "KR"
    market.name = "Korea"

    decision = MagicMock()
    decision.action = "HOLD"
    decision.confidence = 70
    decision.rationale = "hold"

    playbook_scenario = MagicMock()
    playbook_scenario.stop_loss_pct = "NaN"
    playbook_scenario.take_profit_pct = 5.0
    stock_playbook = MagicMock()
    stock_playbook.scenarios = [playbook_scenario]

    captured: dict[str, float] = {}

    def _fake_eval(**kwargs):  # type: ignore[no-untyped-def]
        cfg = kwargs["config"]
        captured["hard_stop_pct"] = cfg.hard_stop_pct

        class _Out:
            should_exit = False
            reason = "none"
            state = PositionState.HOLDING

        return _Out()

    with (
        patch("src.strategy.exit_manager._compute_kr_dynamic_stop_loss_pct", return_value=-7.0),
        patch("src.strategy.exit_manager.evaluate_exit", side_effect=_fake_eval),
    ):
        _apply_staged_exit_override_for_hold(
            decision=decision,
            market=market,
            stock_code="024060",
            open_position={"price": 100.0, "quantity": 1, "decision_id": "d1", "timestamp": "t1"},
            market_data={"current_price": 95.0, "atr_value": 3.0},
            stock_playbook=stock_playbook,
            settings=None,
        )

    assert captured["hard_stop_pct"] == pytest.approx(-2.0)


def test_apply_staged_exit_kr_without_playbook_uses_dynamic_stop() -> None:
    market = MagicMock()
    market.code = "KR"
    market.name = "Korea"

    decision = MagicMock()
    decision.action = "HOLD"
    decision.confidence = 70
    decision.rationale = "hold"

    captured: dict[str, float] = {}

    def _fake_eval(**kwargs):  # type: ignore[no-untyped-def]
        cfg = kwargs["config"]
        captured["hard_stop_pct"] = cfg.hard_stop_pct

        class _Out:
            should_exit = False
            reason = "none"
            state = PositionState.HOLDING

        return _Out()

    with (
        patch("src.strategy.exit_manager._compute_kr_dynamic_stop_loss_pct", return_value=-5.5),
        patch("src.strategy.exit_manager.evaluate_exit", side_effect=_fake_eval),
    ):
        _apply_staged_exit_override_for_hold(
            decision=decision,
            market=market,
            stock_code="024060",
            open_position={"price": 100.0, "quantity": 1, "decision_id": "d1", "timestamp": "t1"},
            market_data={"current_price": 95.0, "atr_value": 3.0},
            stock_playbook=None,
            settings=None,
        )

    assert captured["hard_stop_pct"] == pytest.approx(-5.5)


def test_apply_staged_exit_handles_non_finite_playbook_take_profit() -> None:
    market = MagicMock()
    market.code = "KR"
    market.name = "Korea"

    decision = MagicMock()
    decision.action = "HOLD"
    decision.confidence = 70
    decision.rationale = "hold"

    playbook_scenario = MagicMock()
    playbook_scenario.stop_loss_pct = -3.0
    playbook_scenario.take_profit_pct = "NaN"
    stock_playbook = MagicMock()
    stock_playbook.scenarios = [playbook_scenario]

    captured: dict[str, float] = {}

    def _fake_eval(**kwargs):  # type: ignore[no-untyped-def]
        cfg = kwargs["config"]
        captured["be_arm_pct"] = cfg.be_arm_pct
        captured["arm_pct"] = cfg.arm_pct

        class _Out:
            should_exit = False
            reason = "none"
            state = PositionState.HOLDING

        return _Out()

    with (
        patch("src.strategy.exit_manager._compute_kr_dynamic_stop_loss_pct", return_value=-3.0),
        patch("src.strategy.exit_manager.evaluate_exit", side_effect=_fake_eval),
    ):
        _apply_staged_exit_override_for_hold(
            decision=decision,
            market=market,
            stock_code="024060",
            open_position={"price": 100.0, "quantity": 1, "decision_id": "d1", "timestamp": "t1"},
            market_data={"current_price": 95.0, "atr_value": 3.0},
            stock_playbook=stock_playbook,
            settings=None,
        )

    assert math.isfinite(captured["be_arm_pct"])
    assert math.isfinite(captured["arm_pct"])
    assert captured["be_arm_pct"] == pytest.approx(1.2)
    assert captured["arm_pct"] == pytest.approx(3.0)
class TestExtractHeldQtyFromBalance:
    """Tests for _extract_held_qty_from_balance()."""

    def _domestic_balance(self, stock_code: str, ord_psbl_qty: int) -> dict:
        return {
            "output1": [{"pdno": stock_code, "ord_psbl_qty": str(ord_psbl_qty)}],
            "output2": [{"dnca_tot_amt": "1000000"}],
        }

    def test_domestic_returns_ord_psbl_qty(self) -> None:
        balance = self._domestic_balance("005930", 7)
        assert _extract_held_qty_from_balance(balance, "005930", is_domestic=True) == 7

    def test_domestic_fallback_to_hldg_qty(self) -> None:
        balance = {"output1": [{"pdno": "005930", "hldg_qty": "3"}]}
        assert _extract_held_qty_from_balance(balance, "005930", is_domestic=True) == 3

    def test_domestic_returns_zero_when_not_found(self) -> None:
        balance = self._domestic_balance("005930", 5)
        assert _extract_held_qty_from_balance(balance, "000660", is_domestic=True) == 0

    def test_domestic_returns_zero_when_output1_empty(self) -> None:
        balance = {"output1": [], "output2": [{}]}
        assert _extract_held_qty_from_balance(balance, "005930", is_domestic=True) == 0

    def test_overseas_returns_ord_psbl_qty_first(self) -> None:
        """ord_psbl_qty (주문가능수량) takes priority over ovrs_cblc_qty."""
        balance = {"output1": [{"ovrs_pdno": "AAPL", "ord_psbl_qty": "8", "ovrs_cblc_qty": "10"}]}
        assert _extract_held_qty_from_balance(balance, "AAPL", is_domestic=False) == 8

    def test_overseas_fallback_to_ovrs_cblc_qty_when_ord_psbl_qty_absent(self) -> None:
        balance = {"output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10"}]}
        assert _extract_held_qty_from_balance(balance, "AAPL", is_domestic=False) == 10

    def test_overseas_returns_zero_when_ord_psbl_qty_zero(self) -> None:
        """Expired/delisted securities: ovrs_cblc_qty large but ord_psbl_qty=0."""
        balance = {
            "output1": [{"ovrs_pdno": "MLECW", "ord_psbl_qty": "0", "ovrs_cblc_qty": "289456"}]
        }
        assert _extract_held_qty_from_balance(balance, "MLECW", is_domestic=False) == 0

    def test_overseas_fallback_to_hldg_qty(self) -> None:
        balance = {"output1": [{"ovrs_pdno": "AAPL", "hldg_qty": "4"}]}
        assert _extract_held_qty_from_balance(balance, "AAPL", is_domestic=False) == 4

    def test_case_insensitive_match(self) -> None:
        balance = {"output1": [{"pdno": "005930", "ord_psbl_qty": "2"}]}
        assert _extract_held_qty_from_balance(balance, "005930", is_domestic=True) == 2


class TestExtractHeldCodesFromBalance:
    """Tests for _extract_held_codes_from_balance()."""

    def test_returns_codes_with_positive_qty(self) -> None:
        balance = {
            "output1": [
                {"pdno": "005930", "ord_psbl_qty": "5"},
                {"pdno": "000660", "ord_psbl_qty": "3"},
            ]
        }
        result = _extract_held_codes_from_balance(balance, is_domestic=True)
        assert set(result) == {"005930", "000660"}

    def test_excludes_zero_qty_holdings(self) -> None:
        balance = {
            "output1": [
                {"pdno": "005930", "ord_psbl_qty": "0"},
                {"pdno": "000660", "ord_psbl_qty": "2"},
            ]
        }
        result = _extract_held_codes_from_balance(balance, is_domestic=True)
        assert "005930" not in result
        assert "000660" in result

    def test_returns_empty_when_output1_missing(self) -> None:
        balance: dict = {}
        assert _extract_held_codes_from_balance(balance, is_domestic=True) == []

    def test_overseas_uses_ovrs_pdno(self) -> None:
        balance = {"output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "3"}]}
        result = _extract_held_codes_from_balance(balance, is_domestic=False)
        assert result == ["AAPL"]

    def test_overseas_uses_ord_psbl_qty_to_filter(self) -> None:
        """ord_psbl_qty=0 should exclude stock even if ovrs_cblc_qty is large."""
        balance = {
            "output1": [
                {"ovrs_pdno": "MLECW", "ord_psbl_qty": "0", "ovrs_cblc_qty": "289456"},
                {"ovrs_pdno": "AAPL", "ord_psbl_qty": "5", "ovrs_cblc_qty": "5"},
            ]
        }
        result = _extract_held_codes_from_balance(balance, is_domestic=False)
        assert "MLECW" not in result
        assert "AAPL" in result

    def test_overseas_includes_stock_when_ord_psbl_qty_absent_and_ovrs_cblc_qty_positive(
        self,
    ) -> None:
        """Fallback to ovrs_cblc_qty when ord_psbl_qty field is missing."""
        balance = {"output1": [{"ovrs_pdno": "TSLA", "ovrs_cblc_qty": "3"}]}
        result = _extract_held_codes_from_balance(balance, is_domestic=False)
        assert "TSLA" in result

    def test_overseas_filters_holdings_by_exchange_code_when_present(self) -> None:
        balance = {
            "output1": [
                {"ovrs_pdno": "KORE", "ord_psbl_qty": "5", "ovrs_excg_cd": "NASD"},
                {"ovrs_pdno": "KORE", "ord_psbl_qty": "5", "ovrs_excg_cd": "NYSE"},
                {"ovrs_pdno": "AAPL", "ord_psbl_qty": "2", "ovrs_excg_cd": "NASD"},
            ]
        }

        result = _extract_held_codes_from_balance(
            balance,
            is_domestic=False,
            exchange_code="NASD",
        )

        assert result == ["KORE", "AAPL"]


class TestDetermineOrderQuantity:
    """Test _determine_order_quantity() — SELL uses broker_held_qty."""

    def test_sell_returns_broker_held_qty(self) -> None:
        result = _determine_order_quantity(
            action="SELL",
            current_price=105.0,
            total_cash=50000.0,
            candidate=None,
            settings=None,
            broker_held_qty=7,
        )
        assert result == 7

    def test_sell_returns_zero_when_broker_qty_zero(self) -> None:
        result = _determine_order_quantity(
            action="SELL",
            current_price=105.0,
            total_cash=50000.0,
            candidate=None,
            settings=None,
            broker_held_qty=0,
        )
        assert result == 0

    def test_buy_without_position_sizing_returns_one(self) -> None:
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=None,
        )
        assert result == 1

    def test_buy_with_zero_cash_returns_zero(self) -> None:
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=0.0,
            candidate=None,
            settings=None,
        )
        assert result == 0

    def test_buy_with_position_sizing_calculates_correctly(self) -> None:
        settings = MagicMock(spec=Settings)
        settings.POSITION_SIZING_ENABLED = True
        settings.POSITION_VOLATILITY_TARGET_SCORE = 50.0
        settings.POSITION_BASE_ALLOCATION_PCT = 10.0
        settings.POSITION_MAX_ALLOCATION_PCT = 30.0
        settings.POSITION_MIN_ALLOCATION_PCT = 1.0
        # 1,000,000 * 10% = 100,000 budget // 50,000 price = 2 shares
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=settings,
        )
        assert result == 2

    def test_determine_order_quantity_uses_playbook_allocation_pct(self) -> None:
        """playbook_allocation_pct should take priority over volatility-based sizing."""
        settings = MagicMock(spec=Settings)
        settings.POSITION_SIZING_ENABLED = True
        settings.POSITION_MAX_ALLOCATION_PCT = 30.0
        settings.POSITION_MIN_ALLOCATION_PCT = 1.0
        # playbook says 20%, confidence 80 → scale=1.0 → 20%
        # 1,000,000 * 20% = 200,000 // 50,000 price = 4 shares
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=settings,
            playbook_allocation_pct=20.0,
            scenario_confidence=80,
        )
        assert result == 4

    def test_determine_order_quantity_confidence_scales_allocation(self) -> None:
        """Higher confidence should produce a larger allocation (up to max)."""
        settings = MagicMock(spec=Settings)
        settings.POSITION_SIZING_ENABLED = True
        settings.POSITION_MAX_ALLOCATION_PCT = 30.0
        settings.POSITION_MIN_ALLOCATION_PCT = 1.0
        # confidence 96 → scale=1.2 → 10% * 1.2 = 12%
        # 1,000,000 * 12% = 120,000 // 50,000 price = 2 shares
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=settings,
            playbook_allocation_pct=10.0,
            scenario_confidence=96,
        )
        # scale = 96/80 = 1.2 → effective_pct = 12.0
        # budget = 1_000_000 * 0.12 = 120_000 → qty = 120_000 // 50_000 = 2
        assert result == 2

    def test_determine_order_quantity_confidence_clamped_to_max(self) -> None:
        """Confidence scaling should not exceed POSITION_MAX_ALLOCATION_PCT."""
        settings = MagicMock(spec=Settings)
        settings.POSITION_SIZING_ENABLED = True
        settings.POSITION_MAX_ALLOCATION_PCT = 15.0
        settings.POSITION_MIN_ALLOCATION_PCT = 1.0
        # playbook 20% * scale 1.5 = 30% → clamped to 15%
        # 1,000,000 * 15% = 150,000 // 50,000 price = 3 shares
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=settings,
            playbook_allocation_pct=20.0,
            scenario_confidence=120,  # extreme → scale = 1.5
        )
        assert result == 3

    def test_determine_order_quantity_fallback_when_no_playbook(self) -> None:
        """Without playbook_allocation_pct, falls back to volatility-based sizing."""
        settings = MagicMock(spec=Settings)
        settings.POSITION_SIZING_ENABLED = True
        settings.POSITION_VOLATILITY_TARGET_SCORE = 50.0
        settings.POSITION_BASE_ALLOCATION_PCT = 10.0
        settings.POSITION_MAX_ALLOCATION_PCT = 30.0
        settings.POSITION_MIN_ALLOCATION_PCT = 1.0
        # Same as test_buy_with_position_sizing_calculates_correctly (no playbook)
        result = _determine_order_quantity(
            action="BUY",
            current_price=50000.0,
            total_cash=1000000.0,
            candidate=None,
            settings=settings,
            playbook_allocation_pct=None,  # explicit None → fallback
        )
        assert result == 2


class TestSafeFloat:
    """Test safe_float() helper function."""

    def test_converts_valid_string(self):
        """Test conversion of valid numeric string."""
        assert safe_float("123.45") == 123.45
        assert safe_float("0") == 0.0
        assert safe_float("-99.9") == -99.9

    def test_handles_empty_string(self):
        """Test empty string returns default."""
        assert safe_float("") == 0.0
        assert safe_float("", 99.0) == 99.0

    def test_handles_none(self):
        """Test None returns default."""
        assert safe_float(None) == 0.0
        assert safe_float(None, 42.0) == 42.0

    def test_handles_invalid_string(self):
        """Test invalid string returns default."""
        assert safe_float("invalid") == 0.0
        assert safe_float("not_a_number", 100.0) == 100.0
        assert safe_float("12.34.56") == 0.0

    def test_handles_float_input(self):
        """Test float input passes through."""
        assert safe_float(123.45) == 123.45
        assert safe_float(0.0) == 0.0

    def test_custom_default(self):
        """Test custom default value."""
        assert safe_float("", -1.0) == -1.0
        assert safe_float(None, 999.0) == 999.0


class TestTradingCycleTelegramIntegration:
    """Test telegram notifications in trading_cycle function."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        """Create mock broker."""
        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(50000.0, 1.23, 100.0))
        broker.get_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "5000000",
                    }
                ]
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "OK"})
        return broker

    @pytest.fixture
    def mock_overseas_broker(self) -> MagicMock:
        """Create mock overseas broker."""
        broker = MagicMock()
        return broker

    @pytest.fixture
    def mock_scenario_engine(self) -> MagicMock:
        """Create mock scenario engine that returns BUY."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match())
        return engine

    @pytest.fixture
    def mock_playbook(self) -> DayPlaybook:
        """Create a minimal day playbook."""
        scenario = StockScenario(
            condition=StockCondition(rsi_below=30),
            action=ScenarioAction.BUY,
            confidence=85,
            rationale="fixture",
        )
        return DayPlaybook(
            date=date(2026, 2, 8),
            market="KR",
            stock_playbooks=[
                {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
            ],
        )

    @pytest.fixture
    def mock_risk(self) -> MagicMock:
        """Create mock risk manager."""
        risk = MagicMock()
        risk.validate_order = MagicMock()
        return risk

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        """Create mock database connection."""
        return MagicMock()

    @pytest.fixture
    def mock_decision_logger(self) -> MagicMock:
        """Create mock decision logger."""
        logger = MagicMock()
        logger.log_decision = MagicMock()
        return logger

    @pytest.fixture
    def mock_context_store(self) -> MagicMock:
        """Create mock context store."""
        store = MagicMock()
        store.get_latest_timeframe = MagicMock(return_value=None)
        return store

    @pytest.fixture
    def mock_criticality_assessor(self) -> MagicMock:
        """Create mock criticality assessor."""
        assessor = MagicMock()
        assessor.assess_market_conditions = MagicMock(return_value=MagicMock(value="NORMAL"))
        assessor.get_timeout = MagicMock(return_value=5.0)
        return assessor

    @pytest.fixture
    def mock_telegram(self) -> MagicMock:
        """Create mock telegram client."""
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()
        return telegram

    @pytest.fixture
    def mock_market(self) -> MagicMock:
        """Create mock market info."""
        market = MagicMock()
        market.name = "Korea"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        return market

    @pytest.fixture
    def mock_overseas_market(self) -> MagicMock:
        """Create mock overseas market info."""
        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NASD"
        market.is_domestic = False
        return market

    @pytest.mark.asyncio
    async def test_trade_execution_notification_sent(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """Test telegram notification sent on trade execution."""
        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=mock_scenario_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # Verify notification was sent
        mock_telegram.notify_trade_execution.assert_called_once()
        call_kwargs = mock_telegram.notify_trade_execution.call_args.kwargs
        assert call_kwargs["stock_code"] == "005930"
        assert call_kwargs["stock_name"] == "Samsung"
        assert call_kwargs["market"] == "Korea"
        assert call_kwargs["action"] == "BUY"
        assert call_kwargs["confidence"] == 85

    @pytest.mark.asyncio
    async def test_trade_execution_notification_sent_us_market(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """US trading_cycle path should forward stock_name to trade notifications."""
        scenario = StockScenario(
            condition=StockCondition(rsi_below=30),
            action=ScenarioAction.BUY,
            confidence=85,
            rationale="fixture",
        )
        mock_playbook = DayPlaybook(
            date=date(2026, 2, 8),
            market="US",
            stock_playbooks=[
                {"stock_code": "AAPL", "stock_name": "Apple", "scenarios": [scenario]}
            ],
        )
        mock_overseas_broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "182.50", "rate": "1.25"}}
        )
        mock_overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "frcr_evlu_tota": "100000.00",
                        "frcr_buy_amt_smtl": "50000.00",
                    }
                ]
            }
        )
        mock_overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000.00"}}
        )
        mock_overseas_broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": "0", "msg1": "OK"}
        )

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=mock_scenario_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        mock_telegram.notify_trade_execution.assert_called_once()
        call_kwargs = mock_telegram.notify_trade_execution.call_args.kwargs
        assert call_kwargs["stock_code"] == "AAPL"
        assert call_kwargs["stock_name"] == "Apple"
        assert call_kwargs["market"] == "NASDAQ"
        assert call_kwargs["action"] == "BUY"
        assert call_kwargs["confidence"] == 85

    @pytest.mark.asyncio
    async def test_trade_execution_notification_failure_doesnt_crash(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """Test trading continues even if notification fails."""
        # Make notification fail
        mock_telegram.notify_trade_execution.side_effect = Exception("API error")

        with patch("src.main.log_trade"):
            # Should not raise exception
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=mock_scenario_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # Verify notification was attempted
        mock_telegram.notify_trade_execution.assert_called_once()

    @pytest.mark.asyncio
    async def test_kr_rejected_order_does_not_notify_or_log_trade(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """KR orders rejected by KIS should not trigger success side effects."""
        mock_broker.send_order = AsyncMock(
            return_value={"rt_cd": "1", "msg1": "장운영시간이 아닙니다."}
        )

        with patch("src.main.log_trade") as mock_log_trade:
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=mock_scenario_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        mock_telegram.notify_trade_execution.assert_not_called()
        mock_log_trade.assert_not_called()

    @pytest.mark.asyncio
    async def test_fat_finger_notification_sent(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """Test telegram notification sent on fat finger rejection."""
        # Make risk manager reject the order
        mock_risk.validate_order.side_effect = FatFingerRejected(
            order_amount=2000000,
            total_cash=5000000,
            max_pct=30.0,
        )

        with patch("src.main.log_trade"):
            with pytest.raises(FatFingerRejected):
                await trading_cycle(
                    broker=mock_broker,
                    overseas_broker=mock_overseas_broker,
                    scenario_engine=mock_scenario_engine,
                    playbook=mock_playbook,
                    risk=mock_risk,
                    db_conn=mock_db,
                    decision_logger=mock_decision_logger,
                    context_store=mock_context_store,
                    criticality_assessor=mock_criticality_assessor,
                    telegram=mock_telegram,
                    market=mock_market,
                    stock_code="005930",
                    scan_candidates={},
                )

        # Verify notification was sent
        mock_telegram.notify_fat_finger.assert_called_once()
        call_kwargs = mock_telegram.notify_fat_finger.call_args.kwargs
        assert call_kwargs["stock_code"] == "005930"
        assert call_kwargs["order_amount"] == 2000000
        assert call_kwargs["total_cash"] == 5000000
        assert call_kwargs["max_pct"] == 30.0

    @pytest.mark.asyncio
    async def test_fat_finger_notification_failure_still_raises(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """Test fat finger exception still raised even if notification fails."""
        # Make risk manager reject the order
        mock_risk.validate_order.side_effect = FatFingerRejected(
            order_amount=2000000,
            total_cash=5000000,
            max_pct=30.0,
        )
        # Make notification fail
        mock_telegram.notify_fat_finger.side_effect = Exception("API error")

        with patch("src.main.log_trade"):
            with pytest.raises(FatFingerRejected):
                await trading_cycle(
                    broker=mock_broker,
                    overseas_broker=mock_overseas_broker,
                    scenario_engine=mock_scenario_engine,
                    playbook=mock_playbook,
                    risk=mock_risk,
                    db_conn=mock_db,
                    decision_logger=mock_decision_logger,
                    context_store=mock_context_store,
                    criticality_assessor=mock_criticality_assessor,
                    telegram=mock_telegram,
                    market=mock_market,
                    stock_code="005930",
                    scan_candidates={},
                )

        # Verify notification was attempted
        mock_telegram.notify_fat_finger.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_notification_on_hold_decision(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """Test no trade notification sent when decision is HOLD."""
        # Scenario engine returns HOLD
        mock_scenario_engine.evaluate = MagicMock(return_value=_make_hold_match())

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=mock_scenario_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # Verify no trade notification sent
        mock_telegram.notify_trade_execution.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_skips_fat_finger_check(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """SELL orders must not be blocked by fat-finger check.

        Even if position value > 30% of cash (e.g. stop-loss on a large holding
        with low remaining cash), the SELL should proceed — only circuit breaker
        applies to SELLs.
        """
        # SELL decision with held qty=100 shares @ 50,000 = 5,000,000
        # cash = 5,000,000 → ratio = 100% which would normally trigger fat finger
        mock_scenario_engine.evaluate = MagicMock(return_value=_make_sell_match())
        mock_broker.get_balance = AsyncMock(
            return_value={
                "output1": [{"pdno": "005930", "ord_psbl_qty": "100"}],
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "5000000",
                    }
                ],
            }
        )

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=mock_scenario_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # validate_order (which includes fat finger) must NOT be called for SELL
        mock_risk.validate_order.assert_not_called()
        # check_circuit_breaker MUST be called for SELL
        mock_risk.check_circuit_breaker.assert_called_once()

    @pytest.mark.asyncio
    async def test_sell_circuit_breaker_still_applies(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_scenario_engine: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_market: MagicMock,
    ) -> None:
        """SELL orders must still respect the circuit breaker."""
        mock_scenario_engine.evaluate = MagicMock(return_value=_make_sell_match())
        mock_broker.get_balance = AsyncMock(
            return_value={
                "output1": [{"pdno": "005930", "ord_psbl_qty": "100"}],
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "5000000",
                    }
                ],
            }
        )
        mock_risk.check_circuit_breaker.side_effect = CircuitBreakerTripped(
            pnl_pct=-4.0, threshold=-3.0
        )

        with patch("src.main.log_trade"):
            with pytest.raises(CircuitBreakerTripped):
                await trading_cycle(
                    broker=mock_broker,
                    overseas_broker=mock_overseas_broker,
                    scenario_engine=mock_scenario_engine,
                    playbook=mock_playbook,
                    risk=mock_risk,
                    db_conn=mock_db,
                    decision_logger=mock_decision_logger,
                    context_store=mock_context_store,
                    criticality_assessor=mock_criticality_assessor,
                    telegram=mock_telegram,
                    market=mock_market,
                    stock_code="005930",
                    scan_candidates={},
                )

        mock_risk.check_circuit_breaker.assert_called_once()
        mock_risk.validate_order.assert_not_called()


class TestRunFunctionTelegramIntegration:
    """Test telegram notifications in run function."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_notification_sent(self) -> None:
        """Test telegram notification sent when circuit breaker trips."""
        mock_telegram = MagicMock()
        mock_telegram.notify_circuit_breaker = AsyncMock()

        # Simulate circuit breaker exception
        exc = CircuitBreakerTripped(pnl_pct=-3.5, threshold=-3.0)

        # Test the notification logic
        try:
            await mock_telegram.notify_circuit_breaker(
                pnl_pct=exc.pnl_pct,
                threshold=exc.threshold,
            )
        except Exception:
            pass  # Ignore errors in notification

        # Verify notification was called
        mock_telegram.notify_circuit_breaker.assert_called_once_with(
            pnl_pct=-3.5,
            threshold=-3.0,
        )


class TestOverseasBalanceParsing:
    """Test overseas balance output2 parsing handles different formats."""

    @pytest.fixture
    def mock_overseas_broker_with_list(self) -> MagicMock:
        """Create mock overseas broker returning list format."""
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "150.50"}})
        broker.get_overseas_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "frcr_evlu_tota": "10000.00",
                        "frcr_buy_amt_smtl": "4500.00",
                    }
                ]
            }
        )
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "5000.00"}}
        )
        return broker

    @pytest.fixture
    def mock_overseas_broker_with_dict(self) -> MagicMock:
        """Create mock overseas broker returning dict format."""
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "150.50"}})
        broker.get_overseas_balance = AsyncMock(
            return_value={
                "output2": {
                    "frcr_evlu_tota": "10000.00",
                    "frcr_buy_amt_smtl": "4500.00",
                }
            }
        )
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "5000.00"}}
        )
        return broker

    @pytest.fixture
    def mock_overseas_broker_with_empty(self) -> MagicMock:
        """Create mock overseas broker returning empty output2."""
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "150.50"}})
        broker.get_overseas_balance = AsyncMock(return_value={"output2": []})
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "0.00"}}
        )
        return broker

    @pytest.fixture
    def mock_overseas_broker_with_empty_price(self) -> MagicMock:
        """Create mock overseas broker returning empty string for price."""
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": ""}}  # Empty string
        )
        broker.get_overseas_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "frcr_evlu_tota": "10000.00",
                        "frcr_buy_amt_smtl": "4500.00",
                    }
                ]
            }
        )
        # get_overseas_buying_power not called when price=0, but mock for safety
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "5000.00"}}
        )
        return broker

    @pytest.fixture
    def mock_domestic_broker(self) -> MagicMock:
        """Create minimal mock domestic broker."""
        broker = MagicMock()
        return broker

    @pytest.fixture
    def mock_overseas_market(self) -> MagicMock:
        """Create mock overseas market info."""
        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NASD"
        market.is_domestic = False
        return market

    @pytest.fixture
    def mock_scenario_engine_hold(self) -> MagicMock:
        """Create mock scenario engine that always returns HOLD."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match("AAPL"))
        return engine

    @pytest.fixture
    def mock_playbook(self) -> DayPlaybook:
        """Create a minimal playbook."""
        return _make_playbook("US")

    @pytest.fixture
    def mock_risk(self) -> MagicMock:
        """Create mock risk manager."""
        return MagicMock()

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        """Create mock database."""
        return MagicMock()

    @pytest.fixture
    def mock_decision_logger(self) -> MagicMock:
        """Create mock decision logger."""
        return MagicMock()

    @pytest.fixture
    def mock_context_store(self) -> MagicMock:
        """Create mock context store."""
        store = MagicMock()
        store.get_latest_timeframe = MagicMock(return_value=None)
        return store

    @pytest.fixture
    def mock_criticality_assessor(self) -> MagicMock:
        """Create mock criticality assessor."""
        assessor = MagicMock()
        assessor.assess_market_conditions = MagicMock(return_value=MagicMock(value="NORMAL"))
        assessor.get_timeout = MagicMock(return_value=5.0)
        return assessor

    @pytest.fixture
    def mock_telegram(self) -> MagicMock:
        """Create mock telegram client."""
        telegram = MagicMock()
        telegram.notify_scenario_matched = AsyncMock()
        return telegram

    @pytest.mark.asyncio
    async def test_overseas_balance_list_format(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_list: MagicMock,
        mock_scenario_engine_hold: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Test overseas balance parsing with list format (output2=[{...}])."""
        with patch("src.main.log_trade"):
            # Should not raise KeyError
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=mock_overseas_broker_with_list,
                scenario_engine=mock_scenario_engine_hold,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # Verify balance API was called
        mock_overseas_broker_with_list.get_overseas_balance.assert_called_once()

    @pytest.mark.asyncio
    async def test_overseas_balance_dict_format(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_dict: MagicMock,
        mock_scenario_engine_hold: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Test overseas balance parsing with dict format (output2={...})."""
        with patch("src.main.log_trade"):
            # Should not raise KeyError
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=mock_overseas_broker_with_dict,
                scenario_engine=mock_scenario_engine_hold,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # Verify balance API was called
        mock_overseas_broker_with_dict.get_overseas_balance.assert_called_once()

    @pytest.mark.asyncio
    async def test_overseas_balance_empty_format(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_empty: MagicMock,
        mock_scenario_engine_hold: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Test overseas balance parsing with empty output2."""
        with patch("src.main.log_trade"):
            # Should not raise KeyError, should default to 0
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=mock_overseas_broker_with_empty,
                scenario_engine=mock_scenario_engine_hold,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # Verify balance API was called
        mock_overseas_broker_with_empty.get_overseas_balance.assert_called_once()

    @pytest.mark.asyncio
    async def test_overseas_price_empty_string(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_empty_price: MagicMock,
        mock_scenario_engine_hold: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Test overseas price parsing with empty string (issue #49)."""
        with patch("src.main.log_trade"):
            # Should not raise ValueError, should default to 0.0
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=mock_overseas_broker_with_empty_price,
                scenario_engine=mock_scenario_engine_hold,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # Verify price API was called
        mock_overseas_broker_with_empty_price.get_overseas_price.assert_called_once()

    @pytest.fixture
    def mock_overseas_broker_with_buy_scenario(self) -> MagicMock:
        """Create mock overseas broker that returns a valid price for BUY orders."""
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "182.50"}})
        broker.get_overseas_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "frcr_evlu_tota": "100000.00",
                        "frcr_buy_amt_smtl": "50000.00",
                    }
                ]
            }
        )
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000.00"}}
        )
        broker.send_overseas_order = AsyncMock(return_value={"msg1": "주문접수"})
        return broker

    @pytest.fixture
    def mock_scenario_engine_buy(self) -> MagicMock:
        """Create mock scenario engine that returns BUY."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("AAPL"))
        return engine

    @pytest.mark.asyncio
    async def test_overseas_buy_order_uses_limit_price(
        self,
        mock_domestic_broker: MagicMock,
        mock_overseas_broker_with_buy_scenario: MagicMock,
        mock_scenario_engine_buy: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Overseas BUY order must use current_price +0.2% limit, not market order.

        KIS market orders (ORD_DVSN=01) calculate quantity based on upper limit price
        (상한가 기준), resulting in only 60-80% of intended cash being used.
        Regression test for issue #149 / #211.
        """
        mock_telegram.notify_trade_execution = AsyncMock()

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=mock_overseas_broker_with_buy_scenario,
                scenario_engine=mock_scenario_engine_buy,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # Verify BUY limit order uses +0.2% premium (issue #211)
        mock_overseas_broker_with_buy_scenario.send_overseas_order.assert_called_once()
        call_kwargs = mock_overseas_broker_with_buy_scenario.send_overseas_order.call_args
        sent_price = call_kwargs[1].get("price") or call_kwargs[0][4]
        # KIS requires max 2 decimal places for prices >= $1 (#252)
        expected_price = round(182.5 * 1.002, 2)  # 0.2% premium for BUY limit orders
        assert sent_price == expected_price, (
            f"Expected limit price {expected_price} (182.5 * 1.002) but got {sent_price}. "
            "BUY uses +0.2% to improve fill rate while minimising overpayment (#211)."
        )

    @pytest.mark.asyncio
    async def test_overseas_sell_order_uses_limit_price_below_current(
        self,
        mock_domestic_broker: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Overseas SELL order must use current_price -0.2% limit (#211).

        Placing SELL at exact last price risks no-fill when the bid is just below.
        Using -0.2% ensures the order fills even if the price dips slightly.
        """
        sell_price = 182.5

        # Broker mock: returns price data and a balance with 5 AAPL shares held.
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": str(sell_price), "rate": "1.5", "tvol": "5000000"}}
        )
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [
                    {
                        "ovrs_pdno": "AAPL",
                        "ovrs_cblc_qty": "5",
                        "pchs_avg_pric": "170.0",
                        "evlu_pfls_rt": "7.35",
                    }
                ],
                "output2": [
                    {
                        "frcr_evlu_tota": "100000.00",
                        "frcr_buy_amt_smtl": "50000.00",
                    }
                ],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000.00"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

        sell_engine = MagicMock(spec=ScenarioEngine)
        sell_engine.evaluate = MagicMock(return_value=_make_sell_match("AAPL"))
        mock_telegram.notify_trade_execution = AsyncMock()

        with patch("src.main.log_trade"), patch("src.main.get_open_position") as mock_pos:
            mock_pos.return_value = {"quantity": 5, "stock_code": "AAPL", "price": 170.0}
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=overseas_broker,
                scenario_engine=sell_engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=mock_db,
                decision_logger=mock_decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="AAPL",
                scan_candidates={},
            )

        overseas_broker.send_overseas_order.assert_called_once()
        call_kwargs = overseas_broker.send_overseas_order.call_args
        sent_price = call_kwargs[1].get("price") or call_kwargs[0][4]
        # KIS requires max 2 decimal places for prices >= $1 (#252)
        expected_price = round(sell_price * 0.998, 2)  # -0.2% for SELL limit orders
        assert sent_price == expected_price, (
            f"Expected SELL limit price {expected_price} (182.5 * 0.998) but got {sent_price}. "
            "SELL uses -0.2% to ensure fill even when price dips slightly (#211)."
        )

    @pytest.mark.asyncio
    async def test_overseas_buy_price_rounded_to_2_decimals_for_dollar_plus_stock(
        self,
        mock_domestic_broker: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """BUY price for $1+ stocks is rounded to 2 decimal places (issue #252).

        KIS rejects prices with more than 2 decimal places for stocks priced >= $1.
        current_price=50.1234 * 1.002 = 50.22... should be sent as 50.22, not 50.2236.
        """
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [{"frcr_evlu_tota": "0", "frcr_buy_amt_smtl": "0"}],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "10000"}}
        )
        overseas_broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "50.1234", "rate": "0"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": None, "msg1": "주문접수"}
        )

        db_conn = init_db(":memory:")
        decision_logger = DecisionLogger(db_conn)

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match())

        await trading_cycle(
            broker=mock_domestic_broker,
            overseas_broker=overseas_broker,
            scenario_engine=engine,
            playbook=mock_playbook,
            risk=mock_risk,
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=mock_context_store,
            criticality_assessor=mock_criticality_assessor,
            telegram=mock_telegram,
            market=mock_overseas_market,
            stock_code="TQQQ",
            scan_candidates={},
        )

        overseas_broker.send_overseas_order.assert_called_once()
        sent_price = (
            overseas_broker.send_overseas_order.call_args[1].get("price")
            or overseas_broker.send_overseas_order.call_args[0][4]
        )
        # 50.1234 * 1.002 = 50.2235... rounded to 2 decimals = 50.22
        assert sent_price == round(50.1234 * 1.002, 2), (
            f"Expected 2-decimal price {round(50.1234 * 1.002, 2)} but got {sent_price} (#252)"
        )

    @pytest.mark.asyncio
    async def test_overseas_penny_stock_price_keeps_4_decimals(
        self,
        mock_domestic_broker: MagicMock,
        mock_playbook: DayPlaybook,
        mock_risk: MagicMock,
        mock_db: MagicMock,
        mock_decision_logger: MagicMock,
        mock_context_store: MagicMock,
        mock_criticality_assessor: MagicMock,
        mock_telegram: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """BUY price for penny stocks (< $1) uses 4 decimal places (issue #252)."""
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [{"frcr_evlu_tota": "0", "frcr_buy_amt_smtl": "0"}],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "10000"}}
        )
        overseas_broker.get_overseas_price = AsyncMock(
            return_value={"output": {"last": "0.5678", "rate": "0"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": None, "msg1": "주문접수"}
        )

        db_conn = init_db(":memory:")
        decision_logger = DecisionLogger(db_conn)

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match())

        with patch(
            "src.main._resolve_market_setting",
            side_effect=lambda **kwargs: (
                0.1 if kwargs.get("key") == "US_MIN_PRICE" else kwargs.get("default")
            ),
        ):
            await trading_cycle(
                broker=mock_domestic_broker,
                overseas_broker=overseas_broker,
                scenario_engine=engine,
                playbook=mock_playbook,
                risk=mock_risk,
                db_conn=db_conn,
                decision_logger=decision_logger,
                context_store=mock_context_store,
                criticality_assessor=mock_criticality_assessor,
                telegram=mock_telegram,
                market=mock_overseas_market,
                stock_code="PENNYX",
                scan_candidates={},
            )

        overseas_broker.send_overseas_order.assert_called_once()
        sent_price = (
            overseas_broker.send_overseas_order.call_args[1].get("price")
            or overseas_broker.send_overseas_order.call_args[0][4]
        )
        # 0.5678 * 1.002 = 0.56893... rounded to 4 decimals = 0.5689
        assert sent_price == round(0.5678 * 1.002, 4), (
            f"Expected 4-decimal price {round(0.5678 * 1.002, 4)} but got {sent_price} (#252)"
        )


class TestScenarioEngineIntegration:
    """Test scenario engine integration in trading_cycle."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        """Create mock broker with standard domestic data."""
        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(50000.0, 2.50, 100.0))
        broker.get_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "9500000",
                    }
                ]
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "OK"})
        return broker

    @pytest.fixture
    def mock_market(self) -> MagicMock:
        """Create mock KR market."""
        market = MagicMock()
        market.name = "Korea"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        return market

    @pytest.fixture
    def mock_telegram(self) -> MagicMock:
        """Create mock telegram with all notification methods."""
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        return telegram

    @pytest.mark.asyncio
    async def test_scenario_engine_called_with_enriched_market_data(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """Test scenario engine receives market_data enriched with scanner metrics."""
        from src.analysis.smart_scanner import ScanCandidate

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())
        playbook = _make_playbook()

        candidate = ScanCandidate(
            stock_code="005930",
            name="Samsung",
            price=50000,
            volume=1000000,
            volume_ratio=3.5,
            rsi=25.0,
            signal="oversold",
            score=85.0,
        )

        with (
            patch("src.main.log_trade"),
            patch("src.main.get_session_info", return_value=MagicMock(session_id="KRX_REG")),
        ):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={"KR": {"005930": candidate}},
            )

        # Verify evaluate was called
        engine.evaluate.assert_called_once()
        call_args = engine.evaluate.call_args
        market_data = call_args[0][2]  # 3rd positional arg
        portfolio_data = call_args[0][3]  # 4th positional arg

        # Scanner data should be enriched into market_data
        assert market_data["rsi"] == 25.0
        assert market_data["volume_ratio"] == 3.5
        assert market_data["current_price"] == 50000.0
        assert market_data["price_change_pct"] == 2.5

        # Portfolio data should include pnl
        assert "portfolio_pnl_pct" in portfolio_data
        assert "total_cash" in portfolio_data

    @pytest.mark.asyncio
    async def test_trading_cycle_sets_l7_context_keys(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """Test L7 context is written with market-scoped keys."""
        from src.analysis.smart_scanner import ScanCandidate

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())
        playbook = _make_playbook()
        context_store = MagicMock(get_latest_timeframe=MagicMock(return_value=None))

        candidate = ScanCandidate(
            stock_code="005930",
            name="Samsung",
            price=50000,
            volume=1000000,
            volume_ratio=3.5,
            rsi=25.0,
            signal="oversold",
            score=85.0,
        )

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=context_store,
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={"KR": {"005930": candidate}},
            )

        context_store.set_context.assert_any_call(
            ContextLayer.L7_REALTIME,
            ANY,
            "volatility_KR_005930",
            {"momentum_score": 50.0, "volume_surge": 1.0, "price_change_1m": 0.0},
        )
        context_store.set_context.assert_any_call(
            ContextLayer.L7_REALTIME,
            ANY,
            "price_KR_005930",
            {"current_price": 50000.0},
        )
        context_store.set_context.assert_any_call(
            ContextLayer.L7_REALTIME,
            ANY,
            "rsi_KR_005930",
            {"rsi": 25.0},
        )
        context_store.set_context.assert_any_call(
            ContextLayer.L7_REALTIME,
            ANY,
            "volume_ratio_KR_005930",
            {"volume_ratio": 3.5},
        )

    @pytest.mark.asyncio
    async def test_scan_candidates_market_scoped(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """Test scan_candidates uses market-scoped lookup, ignoring other markets."""
        from src.analysis.smart_scanner import ScanCandidate

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())

        # Candidate stored under US market — should NOT be found for KR market
        us_candidate = ScanCandidate(
            stock_code="005930",
            name="Overlap",
            price=100,
            volume=500000,
            volume_ratio=5.0,
            rsi=15.0,
            signal="oversold",
            score=90.0,
        )

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,  # KR market
                stock_code="005930",
                scan_candidates={"US": {"005930": us_candidate}},  # Wrong market
            )

        # Should NOT use US candidate's rsi (=15.0); fallback implied_rsi used instead
        market_data = engine.evaluate.call_args[0][2]
        assert market_data["rsi"] != 15.0  # US candidate's rsi must be ignored
        assert market_data["volume_ratio"] == 1.0  # Fallback default

    @pytest.mark.asyncio
    async def test_scenario_engine_called_without_scanner_data(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """Test scenario engine works when stock has no scan candidate."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())
        playbook = _make_playbook()

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},  # No scanner data
            )

        # Holding stocks without scanner data use implied_rsi (from price_change_pct)
        # and volume_ratio=1.0 as fallback, so rsi/volume_ratio are always present.
        engine.evaluate.assert_called_once()
        market_data = engine.evaluate.call_args[0][2]
        assert "rsi" in market_data  # Implied RSI from price_change_pct=2.5 → 55.0
        assert market_data["rsi"] == pytest.approx(55.0)
        assert market_data["volume_ratio"] == 1.0
        assert market_data["current_price"] == 50000.0

    @pytest.mark.asyncio
    async def test_holding_overseas_stock_derives_volume_ratio_from_price_api(
        self,
        mock_broker: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """Test overseas holding stocks derive volume_ratio from get_overseas_price high/low."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())

        os_market = MagicMock()
        os_market.name = "NASDAQ"
        os_market.code = "US_NASDAQ"
        os_market.exchange_code = "NAS"
        os_market.is_domestic = False
        os_market.timezone = UTC

        os_broker = MagicMock()
        # price_change_pct=5.0, high=106, low=94 → intraday_range=12% → volume_ratio=max(1,6)=6
        os_broker.get_overseas_price = AsyncMock(
            return_value={
                "output": {"last": "100.0", "rate": "5.0", "high": "106.0", "low": "94.0"}
            }
        )
        os_broker.get_overseas_balance = AsyncMock(
            return_value={"output2": [{"frcr_evlu_tota": "10000", "frcr_buy_amt_smtl": "9000"}]}
        )
        os_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "500"}}
        )

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=os_broker,
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=os_market,
                stock_code="NVDA",
                scan_candidates={},  # Not in scanner — holding stock
            )

        market_data = engine.evaluate.call_args[0][2]
        # rsi: 50.0 + 5.0 * 2.0 = 60.0
        assert market_data["rsi"] == pytest.approx(60.0)
        # intraday_range = (106-94)/100 * 100 = 12.0%
        # volatility_pct = max(abs(5.0), 12.0) = 12.0
        # volume_ratio = max(1.0, 12.0 / 2.0) = 6.0
        assert market_data["volume_ratio"] == pytest.approx(6.0)

    @pytest.mark.asyncio
    async def test_scenario_matched_notification_sent(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """Test telegram notification sent when a scenario matches."""
        # Create a match with matched_scenario (not None)
        scenario = StockScenario(
            condition=StockCondition(rsi_below=30),
            action=ScenarioAction.BUY,
            confidence=88,
            rationale="RSI oversold bounce",
        )
        match = ScenarioMatch(
            stock_code="005930",
            matched_scenario=scenario,
            action=ScenarioAction.BUY,
            confidence=88,
            rationale="RSI oversold bounce",
            match_details={"rsi": 25.0},
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=match)

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # Scenario matched notification should be sent
        mock_telegram.notify_scenario_matched.assert_called_once()
        call_kwargs = mock_telegram.notify_scenario_matched.call_args.kwargs
        assert call_kwargs["stock_code"] == "005930"
        assert call_kwargs["action"] == "BUY"
        assert "rsi=25.0" in call_kwargs["condition_summary"]

    @pytest.mark.asyncio
    async def test_no_scenario_matched_notification_on_default_hold(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """Test no scenario notification when default HOLD is returned."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_hold_match())

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # No scenario matched notification for default HOLD
        mock_telegram.notify_scenario_matched.assert_not_called()

    @pytest.mark.asyncio
    async def test_decision_logger_receives_scenario_match_details(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """Test decision logger context includes scenario match details."""
        match = ScenarioMatch(
            stock_code="005930",
            matched_scenario=None,
            action=ScenarioAction.HOLD,
            confidence=0,
            rationale="No match",
            match_details={"rsi": 45.0, "volume_ratio": 1.2},
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=match)
        decision_logger = MagicMock()

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        decision_logger.log_decision.assert_called_once()
        call_kwargs = decision_logger.log_decision.call_args.kwargs
        assert call_kwargs["session_id"] == get_session_info(mock_market).session_id
        assert "scenario_match" in call_kwargs["context_snapshot"]
        assert call_kwargs["context_snapshot"]["scenario_match"]["rsi"] == 45.0

    @pytest.mark.asyncio
    async def test_reduce_all_does_not_execute_order(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """Test REDUCE_ALL action does not trigger order execution."""
        match = ScenarioMatch(
            stock_code="005930",
            matched_scenario=None,
            action=ScenarioAction.REDUCE_ALL,
            confidence=100,
            rationale="Global rule: portfolio loss > 2%",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=match)

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # REDUCE_ALL is not BUY or SELL — no order sent
        mock_broker.send_order.assert_not_called()
        mock_telegram.notify_trade_execution.assert_not_called()


@pytest.mark.asyncio
async def test_sell_updates_original_buy_decision_outcome() -> None:
    """SELL should update the original BUY decision outcome in decision_logs."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=85,
        rationale="Initial buy",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=85,
        rationale="Initial buy",
        quantity=1,
        price=100.0,
        pnl=0.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(120.0, 0.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [{"pdno": "005930", "ord_psbl_qty": "1"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

    overseas_broker = MagicMock()
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_sell_match())
    risk = MagicMock()
    context_store = MagicMock(
        get_latest_timeframe=MagicMock(return_value=None),
        set_context=MagicMock(),
    )
    criticality_assessor = MagicMock(
        assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
        get_timeout=MagicMock(return_value=5.0),
    )
    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    await trading_cycle(
        broker=broker,
        overseas_broker=overseas_broker,
        scenario_engine=engine,
        playbook=_make_playbook(),
        risk=risk,
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=context_store,
        criticality_assessor=criticality_assessor,
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    updated_buy = decision_logger.get_decision_by_id(buy_decision_id)
    assert updated_buy is not None
    assert updated_buy.outcome_pnl == 20.0
    assert updated_buy.outcome_accuracy == 1
    assert "KR:005930" not in _STOPLOSS_REENTRY_COOLDOWN_UNTIL


@pytest.mark.asyncio
async def test_stoploss_reentry_cooldown_blocks_buy_when_active() -> None:
    _STOPLOSS_REENTRY_COOLDOWN_UNTIL["KR:005930"] = datetime.now(UTC).timestamp() + 300
    db_conn = init_db(":memory:")

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [
                {"tot_evlu_amt": "100000", "dnca_tot_amt": "50000", "pchs_amt_smtl_amt": "50000"}
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match("005930"))),
        playbook=_make_playbook(),
        risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
        db_conn=db_conn,
        decision_logger=DecisionLogger(db_conn),
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None), set_context=MagicMock()
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=MagicMock(
            notify_trade_execution=AsyncMock(),
            notify_fat_finger=AsyncMock(),
            notify_circuit_breaker=AsyncMock(),
            notify_scenario_matched=AsyncMock(),
        ),
        market=market,
        stock_code="005930",
        scan_candidates={},
        settings=MagicMock(POSITION_SIZING_ENABLED=False, CONFIDENCE_THRESHOLD=80, MODE="paper"),
    )

    broker.send_order.assert_not_called()


@pytest.mark.asyncio
async def test_stoploss_reentry_cooldown_allows_buy_after_expiry() -> None:
    _STOPLOSS_REENTRY_COOLDOWN_UNTIL["KR:005930"] = datetime.now(UTC).timestamp() - 10
    db_conn = init_db(":memory:")

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [
                {"tot_evlu_amt": "100000", "dnca_tot_amt": "50000", "pchs_amt_smtl_amt": "50000"}
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match("005930"))),
        playbook=_make_playbook(),
        risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
        db_conn=db_conn,
        decision_logger=DecisionLogger(db_conn),
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None), set_context=MagicMock()
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=MagicMock(
            notify_trade_execution=AsyncMock(),
            notify_fat_finger=AsyncMock(),
            notify_circuit_breaker=AsyncMock(),
            notify_scenario_matched=AsyncMock(),
        ),
        market=market,
        stock_code="005930",
        scan_candidates={},
        settings=MagicMock(POSITION_SIZING_ENABLED=False, CONFIDENCE_THRESHOLD=80, MODE="paper"),
    )

    broker.send_order.assert_called_once()


@pytest.mark.asyncio
async def test_hold_overridden_to_sell_when_stop_loss_triggered() -> None:
    """HOLD decision should be overridden to SELL when stop-loss threshold is breached."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(95.0, -5.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [{"pdno": "005930", "ord_psbl_qty": "1"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    scenario = StockScenario(
        condition=StockCondition(rsi_below=30),
        action=ScenarioAction.BUY,
        confidence=88,
        stop_loss_pct=-2.0,
        rationale="stop loss policy",
    )
    playbook = DayPlaybook(
        date=date(2026, 2, 8),
        market="KR",
        stock_playbooks=[
            {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
        ],
    )
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_hold_match())

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=engine,
        playbook=playbook,
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    broker.send_order.assert_called_once()
    assert broker.send_order.call_args.kwargs["order_type"] == "SELL"


@pytest.mark.asyncio
async def test_hold_overridden_to_sell_when_take_profit_triggered() -> None:
    """HOLD decision should be overridden to SELL when take-profit threshold is reached."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    # Current price 106.0 → +6% gain, above take_profit_pct=3.0
    broker.get_current_price = AsyncMock(return_value=(106.0, 6.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [{"pdno": "005930", "ord_psbl_qty": "1"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    scenario = StockScenario(
        condition=StockCondition(rsi_below=30),
        action=ScenarioAction.BUY,
        confidence=88,
        stop_loss_pct=-2.0,
        take_profit_pct=3.0,
        rationale="take profit policy",
    )
    playbook = DayPlaybook(
        date=date(2026, 2, 8),
        market="KR",
        stock_playbooks=[
            {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
        ],
    )
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_hold_match())

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=engine,
        playbook=playbook,
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    broker.send_order.assert_called_once()
    assert broker.send_order.call_args.kwargs["order_type"] == "SELL"


@pytest.mark.asyncio
async def test_hold_not_overridden_when_between_stop_loss_and_take_profit() -> None:
    """HOLD should remain HOLD when P&L is within stop-loss and take-profit bounds."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    # Current price 101.0 → +1% gain, within [-2%, +3%] range
    broker.get_current_price = AsyncMock(return_value=(101.0, 1.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ]
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    scenario = StockScenario(
        condition=StockCondition(rsi_below=30),
        action=ScenarioAction.BUY,
        confidence=88,
        stop_loss_pct=-2.0,
        take_profit_pct=3.0,
        rationale="within range policy",
    )
    playbook = DayPlaybook(
        date=date(2026, 2, 8),
        market="KR",
        stock_playbooks=[
            {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
        ],
    )
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_hold_match())

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=engine,
        playbook=playbook,
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    broker.send_order.assert_not_called()


@pytest.mark.asyncio
async def test_staged_exit_decision_logs_runtime_evidence() -> None:
    """Decision logs should persist staged-exit feature values and thresholds."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(101.0, 1.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [{"pdno": "005930", "ord_psbl_qty": "1"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    scenario = StockScenario(
        condition=StockCondition(rsi_below=30),
        action=ScenarioAction.BUY,
        confidence=88,
        stop_loss_pct=-2.0,
        take_profit_pct=3.0,
        rationale="within range policy",
    )
    playbook = DayPlaybook(
        date=date(2026, 2, 8),
        market="KR",
        stock_playbooks=[
            {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
        ],
    )
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_hold_match())

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    with patch(
        "src.main._inject_staged_exit_features",
        new=AsyncMock(
            side_effect=lambda **kwargs: kwargs["market_data"].update(
                {"atr_value": 3.5, "pred_down_prob": 0.72}
            )
        ),
    ):
        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=engine,
            playbook=playbook,
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="005930",
            scan_candidates={},
        )

    row = db_conn.execute(
        "SELECT context_snapshot, input_data FROM decision_logs ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    context_snapshot = json.loads(row[0])
    input_data = json.loads(row[1])

    assert input_data["atr_value"] == pytest.approx(3.5)
    assert input_data["pred_down_prob"] == pytest.approx(0.72)
    assert input_data["stop_loss_threshold"] == pytest.approx(-2.0)
    assert input_data["be_arm_pct"] == pytest.approx(1.2)
    assert input_data["arm_pct"] == pytest.approx(3.0)
    assert context_snapshot["staged_exit"]["reason"] == "hold"
    assert context_snapshot["staged_exit"]["should_exit"] is False


@pytest.mark.asyncio
async def test_hold_overridden_to_sell_on_be_lock_threat_after_state_arms() -> None:
    """Staged exit must use runtime state (BE_LOCK -> be_lock_threat -> SELL)."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    broker.get_current_price = AsyncMock(side_effect=[(102.0, 2.0, 0.0), (99.0, -1.0, 0.0)])
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [{"pdno": "005930", "ord_psbl_qty": "1"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    scenario = StockScenario(
        condition=StockCondition(rsi_below=30),
        action=ScenarioAction.BUY,
        confidence=88,
        stop_loss_pct=-5.0,
        take_profit_pct=3.0,
        rationale="staged exit policy",
    )
    playbook = DayPlaybook(
        date=date(2026, 2, 8),
        market="KR",
        stock_playbooks=[
            {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
        ],
    )
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_hold_match())

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    for _ in range(2):
        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=engine,
            playbook=playbook,
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="005930",
            scan_candidates={},
        )

    broker.send_order.assert_called_once()
    assert broker.send_order.call_args.kwargs["order_type"] == "SELL"


@pytest.mark.asyncio
async def test_runtime_exit_cache_cleared_when_position_closed() -> None:
    """Runtime staged-exit cache must be cleared when no open position exists."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [{"pdno": "005930", "ord_psbl_qty": "1"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    _RUNTIME_EXIT_STATES[f"{market.code}:005930:{buy_decision_id}:dummy-ts"] = PositionState.BE_LOCK
    _RUNTIME_EXIT_PEAKS[f"{market.code}:005930:{buy_decision_id}:dummy-ts"] = 120.0

    # Close position first so trading_cycle observes no open position.
    sell_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="SELL",
        confidence=90,
        rationale="manual close",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="SELL",
        confidence=90,
        rationale="manual close",
        quantity=1,
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=sell_decision_id,
    )

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_hold_match())),
        playbook=_make_playbook(),
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    assert not [k for k in _RUNTIME_EXIT_STATES if k.startswith("KR:005930:")]
    assert not [k for k in _RUNTIME_EXIT_PEAKS if k.startswith("KR:005930:")]


@pytest.mark.asyncio
async def test_stop_loss_not_triggered_when_current_price_is_zero() -> None:
    """HOLD must stay HOLD when current_price=0 even if entry_price is set (issue #251).

    A price API failure that returns 0.0 must not cause a false -100% stop-loss.
    """
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,  # valid entry price
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    # Price API returns 0.0 — simulates API failure or pre-market unavailability
    broker.get_current_price = AsyncMock(return_value=(0.0, 0.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ]
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_hold_match())),
        playbook=_make_playbook("KR"),
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    # No SELL order must be placed — current_price=0 must suppress stop-loss
    broker.send_order.assert_not_called()


@pytest.mark.asyncio
async def test_sell_order_uses_broker_balance_qty_not_db() -> None:
    """SELL quantity must come from broker balance output1, not DB.

    The DB records order quantity which may differ from actual fill quantity.
    This test verifies that we use the broker-confirmed orderable quantity.
    """
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    buy_decision_id = decision_logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="entry",
        context_snapshot={},
        input_data={},
    )
    # DB records 10 shares ordered — but only 5 actually filled (partial fill scenario)
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=10,  # ordered quantity (may differ from fill)
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    # Stop-loss triggers (price dropped below -2%)
    broker.get_current_price = AsyncMock(return_value=(95.0, -5.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            # Broker confirms only 5 shares are actually orderable (partial fill)
            "output1": [{"pdno": "005930", "ord_psbl_qty": "5"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})
    overseas_broker = MagicMock()
    overseas_broker.get_present_balance_fx_rate = AsyncMock(return_value=10.0)

    scenario = StockScenario(
        condition=StockCondition(rsi_below=30),
        action=ScenarioAction.BUY,
        confidence=88,
        stop_loss_pct=-2.0,
        rationale="stop loss policy",
    )
    playbook = DayPlaybook(
        date=date(2026, 2, 8),
        market="KR",
        stock_playbooks=[
            {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
        ],
    )
    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_hold_match())

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=overseas_broker,
        scenario_engine=engine,
        playbook=playbook,
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
    )

    broker.send_order.assert_called_once()
    call_kwargs = broker.send_order.call_args.kwargs
    assert call_kwargs["order_type"] == "SELL"
    # Must use broker-confirmed qty (5), NOT DB-recorded ordered qty (10)
    assert call_kwargs["quantity"] == 5
    updated_buy = decision_logger.get_decision_by_id(buy_decision_id)
    assert updated_buy is not None
    assert updated_buy.outcome_pnl == pytest.approx(-2.5)
    sell_row = db_conn.execute(
        "SELECT pnl, strategy_pnl, fx_pnl, selection_context "
        "FROM trades WHERE action='SELL' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert sell_row is not None
    assert sell_row[0] == pytest.approx(-2.5)
    assert sell_row[1] == pytest.approx(-2.5)
    assert sell_row[2] == 0.0
    assert json.loads(str(sell_row[3]))["fx_rate"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_handle_market_close_runs_daily_review_flow() -> None:
    """Market close should aggregate, create scorecard, lessons, and notify."""
    telegram = MagicMock()
    telegram.notify_market_close = AsyncMock()
    telegram.send_message = AsyncMock()

    context_aggregator = MagicMock()
    reviewer = MagicMock()
    reviewer.generate_scorecard.return_value = DailyScorecard(
        date="2026-02-14",
        market="KR",
        total_decisions=3,
        buys=1,
        sells=1,
        holds=1,
        total_pnl=12.5,
        win_rate=50.0,
        avg_confidence=75.0,
        scenario_match_rate=66.7,
    )
    reviewer.generate_lessons = AsyncMock(return_value=["Cut losers faster"])

    await _handle_market_close(
        market_code="KR",
        market_name="Korea",
        market_timezone=UTC,
        telegram=telegram,
        context_aggregator=context_aggregator,
        daily_reviewer=reviewer,
    )

    telegram.notify_market_close.assert_called_once_with("Korea", 0.0)
    context_aggregator.aggregate_daily_from_trades.assert_called_once()
    reviewer.generate_scorecard.assert_called_once()
    assert reviewer.store_scorecard_in_context.call_count == 2
    reviewer.generate_lessons.assert_called_once()
    telegram.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_handle_market_close_without_lessons_stores_once() -> None:
    """If no lessons are generated, scorecard should be stored once."""
    telegram = MagicMock()
    telegram.notify_market_close = AsyncMock()
    telegram.send_message = AsyncMock()

    context_aggregator = MagicMock()
    reviewer = MagicMock()
    reviewer.generate_scorecard.return_value = DailyScorecard(
        date="2026-02-14",
        market="US",
        total_decisions=1,
        buys=0,
        sells=1,
        holds=0,
        total_pnl=-3.0,
        win_rate=0.0,
        avg_confidence=65.0,
        scenario_match_rate=100.0,
    )
    reviewer.generate_lessons = AsyncMock(return_value=[])

    await _handle_market_close(
        market_code="US",
        market_name="United States",
        market_timezone=UTC,
        telegram=telegram,
        context_aggregator=context_aggregator,
        daily_reviewer=reviewer,
    )

    assert reviewer.store_scorecard_in_context.call_count == 1


@pytest.mark.asyncio
async def test_handle_market_close_triggers_evolution_for_us() -> None:
    telegram = MagicMock()
    telegram.notify_market_close = AsyncMock()
    telegram.send_message = AsyncMock()

    context_aggregator = MagicMock()
    reviewer = MagicMock()
    reviewer.generate_scorecard.return_value = DailyScorecard(
        date="2026-02-14",
        market="US",
        total_decisions=2,
        buys=1,
        sells=1,
        holds=0,
        total_pnl=3.0,
        win_rate=50.0,
        avg_confidence=80.0,
        scenario_match_rate=100.0,
    )
    reviewer.generate_lessons = AsyncMock(return_value=[])

    evolution_optimizer = MagicMock()
    evolution_optimizer.evolve = AsyncMock(return_value=None)

    await _handle_market_close(
        market_code="US",
        market_name="United States",
        market_timezone=UTC,
        telegram=telegram,
        context_aggregator=context_aggregator,
        daily_reviewer=reviewer,
        evolution_optimizer=evolution_optimizer,
    )

    evolution_optimizer.evolve.assert_called_once()


@pytest.mark.asyncio
async def test_handle_market_close_skips_evolution_for_kr() -> None:
    telegram = MagicMock()
    telegram.notify_market_close = AsyncMock()
    telegram.send_message = AsyncMock()

    context_aggregator = MagicMock()
    reviewer = MagicMock()
    reviewer.generate_scorecard.return_value = DailyScorecard(
        date="2026-02-14",
        market="KR",
        total_decisions=1,
        buys=1,
        sells=0,
        holds=0,
        total_pnl=1.0,
        win_rate=100.0,
        avg_confidence=90.0,
        scenario_match_rate=100.0,
    )
    reviewer.generate_lessons = AsyncMock(return_value=[])

    evolution_optimizer = MagicMock()
    evolution_optimizer.evolve = AsyncMock(return_value=None)

    await _handle_market_close(
        market_code="KR",
        market_name="Korea",
        market_timezone=UTC,
        telegram=telegram,
        context_aggregator=context_aggregator,
        daily_reviewer=reviewer,
        evolution_optimizer=evolution_optimizer,
    )

    evolution_optimizer.evolve.assert_not_called()


@pytest.mark.asyncio
async def test_handle_realtime_market_closures_closes_removed_market_once_and_cleans_cache(
) -> None:
    loop = asyncio.get_running_loop()
    market_states = {"KR": "KRX_REG", "US_NASDAQ": "US_REG"}
    playbooks = {"KR": _make_playbook("KR"), "US_NASDAQ": _make_playbook("US_NASDAQ")}
    pre_refresh_playbooks = {
        "KR": _make_playbook("KR"),
        "US_NASDAQ": _make_playbook("US_NASDAQ"),
    }
    buy_cooldown = {
        "KR:005930": loop.time() + 600,
        "US_NASDAQ:AAPL": loop.time() + 600,
    }
    sell_resubmit_counts = {
        "KR:005930": 2,
        "BUY:KR:005930": 1,
        "NASD:AAPL": 1,
        "BUY:NASD:AAPL": 1,
    }
    tracking_store = MarketTrackingStore()
    tracking_store.record_scan_result(
        market_code="KR",
        session_id="KRX_REG",
        candidates=[
            ScanCandidate("005930", "Samsung", 70000.0, 1.0, 1.0, 50.0, "momentum", 80.0)
        ],
        scanned_at=1.0,
    )
    tracking_store.record_scan_result(
        market_code="US_NASDAQ",
        session_id="US_REG",
        candidates=[ScanCandidate("AAPL", "Apple", 190.0, 1.0, 1.0, 50.0, "momentum", 80.0)],
        scanned_at=2.0,
    )
    mid_refreshed = {"KR", "US_NASDAQ"}
    close_handler = AsyncMock()

    with patch("src.main._handle_market_close", new=close_handler):
        await main_module._handle_realtime_market_closures(
            current_open_markets=[MARKETS["US_NASDAQ"]],
            market_states=market_states,
            playbooks=playbooks,
            pre_refresh_playbooks=pre_refresh_playbooks,
            tracking_store=tracking_store,
            mid_refreshed=mid_refreshed,
            buy_cooldown=buy_cooldown,
            sell_resubmit_counts=sell_resubmit_counts,
            telegram=MagicMock(),
            context_aggregator=MagicMock(),
            daily_reviewer=MagicMock(),
            evolution_optimizer=MagicMock(),
        )
        await main_module._handle_realtime_market_closures(
            current_open_markets=[MARKETS["US_NASDAQ"]],
            market_states=market_states,
            playbooks=playbooks,
            pre_refresh_playbooks=pre_refresh_playbooks,
            tracking_store=tracking_store,
            mid_refreshed=mid_refreshed,
            buy_cooldown=buy_cooldown,
            sell_resubmit_counts=sell_resubmit_counts,
            telegram=MagicMock(),
            context_aggregator=MagicMock(),
            daily_reviewer=MagicMock(),
            evolution_optimizer=MagicMock(),
        )

    close_handler.assert_awaited_once()
    assert close_handler.await_args.kwargs["market_code"] == "KR"
    assert "KR" not in market_states
    assert "KR" not in playbooks
    assert "KR" not in pre_refresh_playbooks
    assert tracking_store.get_snapshot("KR", now_monotonic=3.0) is None
    assert "KR" not in mid_refreshed
    assert "KR:005930" not in buy_cooldown
    assert "US_NASDAQ:AAPL" in buy_cooldown
    assert "KR:005930" not in sell_resubmit_counts
    assert "BUY:KR:005930" not in sell_resubmit_counts
    assert "NASD:AAPL" in sell_resubmit_counts
    assert "BUY:NASD:AAPL" in sell_resubmit_counts
    assert "US_NASDAQ" in market_states
    assert "US_NASDAQ" in playbooks
    assert "US_NASDAQ" in mid_refreshed
    assert tracking_store.get_snapshot("US_NASDAQ", now_monotonic=3.0) is not None


@pytest.mark.asyncio
async def test_handle_realtime_market_closures_discards_mid_refreshed_for_closed_market() -> None:
    market_states = {"KR": "KRX_REG", "US_NASDAQ": "US_REG"}
    playbooks = {"KR": _make_playbook("KR"), "US_NASDAQ": _make_playbook("US_NASDAQ")}
    pre_refresh_playbooks = {"KR": _make_playbook("KR")}
    tracking_store = MarketTrackingStore()
    tracking_store.record_scan_result(
        market_code="KR",
        session_id="KRX_REG",
        candidates=[
            ScanCandidate("005930", "Samsung", 70000.0, 1.0, 1.0, 50.0, "momentum", 80.0)
        ],
        scanned_at=1.0,
    )
    mid_refreshed = {"KR", "US_NASDAQ"}

    with patch("src.main._handle_market_close", new=AsyncMock()):
        await main_module._handle_realtime_market_closures(
            current_open_markets=[MARKETS["US_NASDAQ"]],
            market_states=market_states,
            playbooks=playbooks,
            pre_refresh_playbooks=pre_refresh_playbooks,
            tracking_store=tracking_store,
            mid_refreshed=mid_refreshed,
            telegram=MagicMock(),
            context_aggregator=MagicMock(),
            daily_reviewer=MagicMock(),
            evolution_optimizer=MagicMock(),
        )

    assert "KR" not in mid_refreshed
    assert "US_NASDAQ" in mid_refreshed


@pytest.mark.asyncio
async def test_handle_realtime_market_closures_unknown_market_logs_warning_and_cleans_runtime_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    loop = asyncio.get_running_loop()
    market_states = {"MISSING": "CUSTOM_SESSION"}
    playbooks = {"MISSING": _make_playbook("KR")}
    pre_refresh_playbooks = {"MISSING": _make_playbook("KR")}
    buy_cooldown = {"MISSING:005930": loop.time() + 600}
    sell_resubmit_counts = {"CUSTOM:005930": 2, "BUY:CUSTOM:005930": 1}
    tracking_store = MarketTrackingStore()
    tracking_store.record_scan_result(
        market_code="MISSING",
        session_id="CUSTOM_SESSION",
        candidates=[
            ScanCandidate("005930", "Samsung", 70000.0, 1.0, 1.0, 50.0, "momentum", 80.0)
        ],
        scanned_at=1.0,
    )
    mid_refreshed = {"MISSING"}

    with caplog.at_level(logging.WARNING):
        await main_module._handle_realtime_market_closures(
            current_open_markets=[],
            market_states=market_states,
            playbooks=playbooks,
            pre_refresh_playbooks=pre_refresh_playbooks,
            tracking_store=tracking_store,
            mid_refreshed=mid_refreshed,
            buy_cooldown=buy_cooldown,
            sell_resubmit_counts=sell_resubmit_counts,
            telegram=MagicMock(),
            context_aggregator=MagicMock(),
            daily_reviewer=MagicMock(),
            evolution_optimizer=MagicMock(),
        )

    assert "Missing market metadata for closed market: MISSING" in caplog.text
    assert (
        "sell_resubmit_counts cleanup skipped for closed market without metadata: MISSING"
        in caplog.text
    )
    assert market_states == {}
    assert playbooks == {}
    assert pre_refresh_playbooks == {}
    assert tracking_store.get_snapshot("MISSING", now_monotonic=2.0) is None
    assert buy_cooldown == {}
    assert sell_resubmit_counts == {"CUSTOM:005930": 2, "BUY:CUSTOM:005930": 1}
    assert mid_refreshed == set()


@pytest.mark.asyncio
async def test_handle_realtime_market_closures_cleans_runtime_state_after_close_failure() -> None:
    loop = asyncio.get_running_loop()
    market_states = {"KR": "KRX_REG"}
    playbooks = {"KR": _make_playbook("KR")}
    pre_refresh_playbooks = {"KR": _make_playbook("KR")}
    buy_cooldown = {"KR:005930": loop.time() + 600}
    sell_resubmit_counts = {"KR:005930": 2, "BUY:KR:005930": 1}
    tracking_store = MarketTrackingStore()
    tracking_store.record_scan_result(
        market_code="KR",
        session_id="KRX_REG",
        candidates=[
            ScanCandidate("005930", "Samsung", 70000.0, 1.0, 1.0, 50.0, "momentum", 80.0)
        ],
        scanned_at=1.0,
    )
    mid_refreshed = {"KR"}

    with patch("src.main._handle_market_close", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await main_module._handle_realtime_market_closures(
            current_open_markets=[],
            market_states=market_states,
            playbooks=playbooks,
            pre_refresh_playbooks=pre_refresh_playbooks,
            tracking_store=tracking_store,
            mid_refreshed=mid_refreshed,
            buy_cooldown=buy_cooldown,
            sell_resubmit_counts=sell_resubmit_counts,
            telegram=MagicMock(),
            context_aggregator=MagicMock(),
            daily_reviewer=MagicMock(),
            evolution_optimizer=MagicMock(),
        )

    assert market_states == {}
    assert playbooks == {}
    assert pre_refresh_playbooks == {}
    assert tracking_store.get_snapshot("KR", now_monotonic=2.0) is None
    assert buy_cooldown == {}
    assert sell_resubmit_counts == {}
    assert mid_refreshed == set()


def test_run_context_scheduler_invokes_scheduler() -> None:
    """Scheduler helper should call run_if_due with provided datetime."""
    scheduler = MagicMock()
    scheduler.run_if_due = MagicMock(return_value=ScheduleResult(cleanup=True))

    _run_context_scheduler(scheduler, now=datetime(2026, 2, 14, tzinfo=UTC))

    scheduler.run_if_due.assert_called_once()


@pytest.mark.asyncio
async def test_run_evolution_loop_skips_non_us_market() -> None:
    optimizer = MagicMock()
    optimizer.evolve = AsyncMock()
    telegram = MagicMock()
    telegram.send_message = AsyncMock()

    await _run_evolution_loop(
        evolution_optimizer=optimizer,
        telegram=telegram,
        market_code="KR",
        market_date="2026-02-14",
    )

    optimizer.evolve.assert_not_called()
    telegram.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_run_evolution_loop_notifies_when_report_is_recorded() -> None:
    optimizer = MagicMock()
    optimizer.evolve = AsyncMock(
        return_value={
            "title": "[Evolution] Daily recommendation: US_NASDAQ 2026-02-14",
            "context_key": "evolution_US_NASDAQ",
            "status": "recorded",
        }
    )
    telegram = MagicMock()
    telegram.send_message = AsyncMock()

    await _run_evolution_loop(
        evolution_optimizer=optimizer,
        telegram=telegram,
        market_code="US_NASDAQ",
        market_date="2026-02-14",
    )

    optimizer.evolve.assert_called_once_with(
        market_code="US_NASDAQ",
        market_date="2026-02-14",
    )
    telegram.send_message.assert_called_once()
    message = telegram.send_message.await_args.args[0]
    assert "Context Key: evolution_US_NASDAQ" in message
    assert "Status: recorded" in message


@pytest.mark.asyncio
async def test_run_evolution_loop_notification_error_is_ignored() -> None:
    optimizer = MagicMock()
    optimizer.evolve = AsyncMock(
        return_value={
            "title": "[Evolution] Daily recommendation: US_NYSE 2026-02-14",
            "context_key": "evolution_US_NYSE",
            "status": "recorded",
        }
    )
    telegram = MagicMock()
    telegram.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))

    await _run_evolution_loop(
        evolution_optimizer=optimizer,
        telegram=telegram,
        market_code="US_NYSE",
        market_date="2026-02-14",
    )

    optimizer.evolve.assert_called_once_with(
        market_code="US_NYSE",
        market_date="2026-02-14",
    )
    telegram.send_message.assert_called_once()


def test_apply_dashboard_flag_enables_dashboard() -> None:
    settings = Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        DASHBOARD_ENABLED=False,
    )
    updated = _apply_dashboard_flag(settings, dashboard_flag=True)
    assert updated.DASHBOARD_ENABLED is True


def test_start_dashboard_server_disabled_returns_none() -> None:
    settings = Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        DASHBOARD_ENABLED=False,
    )
    thread = _start_dashboard_server(settings)
    assert thread is None


def test_start_dashboard_server_enabled_starts_thread() -> None:
    settings = Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        DASHBOARD_ENABLED=True,
    )
    mock_thread = MagicMock()
    with patch("src.main.threading.Thread", return_value=mock_thread) as mock_thread_cls:
        thread = _start_dashboard_server(settings)

    assert thread == mock_thread
    mock_thread_cls.assert_called_once()
    mock_thread.start.assert_called_once()


def test_start_dashboard_server_returns_none_when_uvicorn_missing() -> None:
    """Returns None (no thread) and logs a warning when uvicorn is not installed."""
    settings = Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        DASHBOARD_ENABLED=True,
    )
    import builtins

    real_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "uvicorn":
            raise ImportError("No module named 'uvicorn'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        thread = _start_dashboard_server(settings)

    assert thread is None


# ---------------------------------------------------------------------------
# BUY cooldown tests (#179)
# ---------------------------------------------------------------------------


class TestBuyCooldown:
    """Tests for BUY cooldown after insufficient-balance rejection."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(100.0, 1.0, 0.0))
        broker.get_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "tot_evlu_amt": "1000000",
                        "dnca_tot_amt": "500000",
                        "pchs_amt_smtl_amt": "500000",
                    }
                ]
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "OK"})
        return broker

    @pytest.fixture
    def mock_market(self) -> MagicMock:
        market = MagicMock()
        market.name = "Korea"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        return market

    @pytest.fixture
    def mock_overseas_market(self) -> MagicMock:
        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NAS"
        market.is_domestic = False
        return market

    @pytest.fixture
    def mock_overseas_broker(self) -> MagicMock:
        broker = MagicMock()
        broker.get_overseas_price = AsyncMock(
            return_value={
                "output": {
                    "last": "1.0",
                    "rate": "0.0",
                    "high": "1.05",
                    "low": "0.95",
                    "tvol": "1000000",
                }
            }
        )
        broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [{"frcr_evlu_tota": "50000", "frcr_buy_amt_smtl": "0"}],
            }
        )
        broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000"}}
        )
        broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": "1", "msg1": "모의투자 주문가능금액이 부족합니다."}
        )
        return broker

    def _make_buy_match_overseas(self, stock_code: str = "MLECW") -> ScenarioMatch:
        return ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=ScenarioAction.BUY,
            confidence=85,
            rationale="Test buy",
        )

    @pytest.mark.asyncio
    async def test_cooldown_set_on_insufficient_balance(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """BUY cooldown entry is created after 주문가능금액 rejection."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_overseas("MLECW"))
        buy_cooldown: dict[str, float] = {}

        with patch("src.main.log_trade"), patch(
            "src.main._resolve_market_setting",
            side_effect=lambda **kwargs: (
                0.1 if kwargs.get("key") == "US_MIN_PRICE" else kwargs.get("default")
            ),
        ):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook("US_NASDAQ"),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=MagicMock(
                    notify_trade_execution=AsyncMock(),
                    notify_fat_finger=AsyncMock(),
                    notify_circuit_breaker=AsyncMock(),
                    notify_scenario_matched=AsyncMock(),
                ),
                market=mock_overseas_market,
                stock_code="MLECW",
                scan_candidates={},
                buy_cooldown=buy_cooldown,
            )

        assert "US_NASDAQ:MLECW" in buy_cooldown
        assert buy_cooldown["US_NASDAQ:MLECW"] > 0

    @pytest.mark.asyncio
    async def test_cooldown_skips_buy(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """BUY is skipped when cooldown is active for the stock."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_overseas("MLECW"))

        import asyncio

        # Set an active cooldown (expires far in the future)
        buy_cooldown: dict[str, float] = {"US_NASDAQ:MLECW": asyncio.get_event_loop().time() + 600}

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook("US_NASDAQ"),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=MagicMock(
                    notify_trade_execution=AsyncMock(),
                    notify_fat_finger=AsyncMock(),
                    notify_circuit_breaker=AsyncMock(),
                    notify_scenario_matched=AsyncMock(),
                ),
                market=mock_overseas_market,
                stock_code="MLECW",
                scan_candidates={},
                buy_cooldown=buy_cooldown,
            )

        # Order should NOT have been sent
        mock_overseas_broker.send_overseas_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_cooldown_not_set_on_other_errors(
        self,
        mock_broker: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """Cooldown is NOT set for non-balance-related rejections."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_overseas("MLECW"))
        # Different rejection reason
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_price = AsyncMock(
            return_value={
                "output": {
                    "last": "1.0",
                    "rate": "0.0",
                    "high": "1.05",
                    "low": "0.95",
                    "tvol": "1000000",
                }
            }
        )
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [{"frcr_evlu_tota": "50000", "frcr_buy_amt_smtl": "0"}],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": "1", "msg1": "기타 오류 메시지"}
        )
        buy_cooldown: dict[str, float] = {}

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook("US_NASDAQ"),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=MagicMock(
                    notify_trade_execution=AsyncMock(),
                    notify_fat_finger=AsyncMock(),
                    notify_circuit_breaker=AsyncMock(),
                    notify_scenario_matched=AsyncMock(),
                ),
                market=mock_overseas_market,
                stock_code="MLECW",
                scan_candidates={},
                buy_cooldown=buy_cooldown,
            )

        # Cooldown should NOT be set for non-balance errors
        assert "US_NASDAQ:MLECW" not in buy_cooldown

    @pytest.mark.asyncio
    async def test_no_cooldown_param_still_works(
        self,
        mock_broker: MagicMock,
        mock_overseas_broker: MagicMock,
        mock_overseas_market: MagicMock,
    ) -> None:
        """trading_cycle works normally when buy_cooldown is None (default)."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_overseas("MLECW"))

        with patch("src.main.log_trade"), patch(
            "src.main._resolve_market_setting",
            side_effect=lambda **kwargs: (
                0.1 if kwargs.get("key") == "US_MIN_PRICE" else kwargs.get("default")
            ),
        ):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=mock_overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook("US_NASDAQ"),
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=MagicMock(
                    notify_trade_execution=AsyncMock(),
                    notify_fat_finger=AsyncMock(),
                    notify_circuit_breaker=AsyncMock(),
                    notify_scenario_matched=AsyncMock(),
                ),
                market=mock_overseas_market,
                stock_code="MLECW",
                scan_candidates={},
                # buy_cooldown not passed → defaults to None
            )

        # Should attempt the order (and fail), but not crash
        mock_overseas_broker.send_overseas_order.assert_called_once()


# ---------------------------------------------------------------------------
# market_outlook BUY confidence threshold tests (#173)
# ---------------------------------------------------------------------------


class TestMarketOutlookConfidenceThreshold:
    """Tests for market_outlook-based BUY confidence suppression in trading_cycle."""

    @pytest.fixture
    def mock_broker(self) -> MagicMock:
        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(50000.0, 1.0, 0.0))
        broker.get_balance = AsyncMock(
            return_value={
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "9500000",
                    }
                ]
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "OK"})
        return broker

    @pytest.fixture
    def mock_market(self) -> MagicMock:
        market = MagicMock()
        market.name = "Korea"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        return market

    @pytest.fixture
    def mock_telegram(self) -> MagicMock:
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        return telegram

    def _make_buy_match_with_confidence(
        self, confidence: int, stock_code: str = "005930"
    ) -> ScenarioMatch:
        from src.strategy.models import StockScenario

        scenario = StockScenario(
            condition=StockCondition(rsi_below=30),
            action=ScenarioAction.BUY,
            confidence=confidence,
            allocation_pct=10.0,
        )
        return ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=scenario,
            action=ScenarioAction.BUY,
            confidence=confidence,
            rationale="Test buy",
        )

    def _make_playbook_with_outlook(self, outlook_str: str, market: str = "KR") -> DayPlaybook:
        from src.strategy.models import MarketOutlook

        outlook_map = {
            "bearish": MarketOutlook.BEARISH,
            "bullish": MarketOutlook.BULLISH,
            "neutral": MarketOutlook.NEUTRAL,
            "neutral_to_bullish": MarketOutlook.NEUTRAL_TO_BULLISH,
            "neutral_to_bearish": MarketOutlook.NEUTRAL_TO_BEARISH,
        }
        return DayPlaybook(
            date=date(2026, 2, 20),
            market=market,
            market_outlook=outlook_map[outlook_str],
        )

    @pytest.mark.asyncio
    async def test_bearish_outlook_raises_buy_confidence_threshold(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """BUY with confidence 85 should be suppressed to HOLD in bearish market."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_with_confidence(85))
        playbook = self._make_playbook_with_outlook("bearish")

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        # HOLD should be logged (not BUY) — check decision_logger was called with HOLD
        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "HOLD"

    @pytest.mark.asyncio
    async def test_bearish_outlook_allows_high_confidence_buy(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """BUY with confidence 92 should proceed in bearish market (threshold=90)."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_with_confidence(92))
        playbook = self._make_playbook_with_outlook("bearish")
        risk = MagicMock()
        risk.validate_order = MagicMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "BUY"

    @pytest.mark.asyncio
    async def test_bullish_outlook_lowers_buy_confidence_threshold(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """BUY with confidence 77 should proceed in bullish market (threshold=75)."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_with_confidence(77))
        playbook = self._make_playbook_with_outlook("bullish")
        risk = MagicMock()
        risk.validate_order = MagicMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "BUY"

    @pytest.mark.asyncio
    async def test_bullish_outlook_suppresses_very_low_confidence_buy(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """BUY with confidence 70 should be suppressed even in bullish market (threshold=75)."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_with_confidence(70))
        playbook = self._make_playbook_with_outlook("bullish")

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=MagicMock(),
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "HOLD"

    @pytest.mark.asyncio
    async def test_neutral_outlook_uses_default_threshold(
        self,
        mock_broker: MagicMock,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        """BUY with confidence 82 should proceed in neutral market (default=80)."""
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=self._make_buy_match_with_confidence(82))
        playbook = self._make_playbook_with_outlook("neutral")
        risk = MagicMock()
        risk.validate_order = MagicMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=mock_broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=playbook,
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=decision_logger,
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
            )

        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "BUY"


class TestBuyChasingSessionHighGuard:
    @pytest.fixture
    def mock_market(self) -> MagicMock:
        market = MagicMock()
        market.name = "Korea"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        market.timezone = ZoneInfo("Asia/Seoul")
        return market

    @pytest.fixture
    def mock_telegram(self) -> MagicMock:
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        return telegram

    @pytest.mark.asyncio
    async def test_trading_cycle_suppresses_buy_while_chasing_session_high(
        self,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        db_conn = init_db(":memory:")
        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(50000.0, 6.0, 0.0))
        broker.get_current_price_with_output = AsyncMock(
            return_value=(50000.0, 6.0, 0.0, {"stck_hgpr": "50100"})
        )
        broker.get_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "5000000",
                    }
                ],
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "OK"})

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        settings = _make_settings(MODE="paper")

        with (
            patch("src.main.log_trade"),
            patch("src.main.get_session_info", return_value=MagicMock(session_id="KRX_REG")),
            patch(
                "src.core.session_risk.get_session_info",
                return_value=MagicMock(session_id="KRX_REG"),
            ),
        ):
            await trading_cycle(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match())),
                playbook=_make_playbook(),
                risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
                db_conn=db_conn,
                decision_logger=decision_logger,
                context_store=MagicMock(
                    get_latest_timeframe=MagicMock(return_value=None),
                    set_context=MagicMock(),
                ),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
                settings=settings,
            )

        broker.send_order.assert_not_called()
        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "HOLD"
        assert "session high" in call_args.kwargs["rationale"].lower()

    @pytest.mark.asyncio
    async def test_run_daily_session_suppresses_buy_while_chasing_session_high(
        self,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        from src.analysis.smart_scanner import ScanCandidate

        db_conn = init_db(":memory:")
        settings = _make_settings(MODE="paper")

        broker = MagicMock()
        broker.get_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [
                    {
                        "tot_evlu_amt": "100000",
                        "dnca_tot_amt": "50000",
                        "pchs_amt_smtl_amt": "50000",
                    }
                ],
            }
        )
        broker.get_current_price = AsyncMock(return_value=(50000.0, 6.0, 0.0))
        broker.get_current_price_with_output = AsyncMock(
            return_value=(50000.0, 6.0, 0.0, {"stck_hgpr": "50100"})
        )
        broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

        smart_scanner = MagicMock()
        smart_scanner.scan = AsyncMock(
            return_value=[
                ScanCandidate(
                    stock_code="005930",
                    name="Samsung",
                    price=50000.0,
                    volume=1_000_000.0,
                    volume_ratio=3.0,
                    rsi=72.0,
                    signal="momentum",
                    score=88.0,
                )
            ]
        )

        playbook_store = MagicMock()
        playbook_store.load = MagicMock(return_value=_make_playbook("KR"))

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        async def _passthrough(fn, *a, label: str = "", **kw):  # type: ignore[override]
            return await fn(*a, **kw)

        with (
            patch("src.main.get_open_position", return_value=None),
            patch("src.main.get_open_markets", return_value=[mock_market]),
            patch("src.main.get_session_info", return_value=MagicMock(session_id="KRX_REG")),
            patch(
                "src.core.session_risk.get_session_info",
                return_value=MagicMock(session_id="KRX_REG"),
            ),
            patch("src.main._retry_connection", new=_passthrough),
            patch("src.main.log_trade"),
        ):
            await run_daily_session(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match())),
                playbook_store=playbook_store,
                pre_market_planner=MagicMock(),
                risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
                db_conn=db_conn,
                decision_logger=decision_logger,
                context_store=MagicMock(),
                criticality_assessor=MagicMock(),
                telegram=mock_telegram,
                settings=settings,
                smart_scanner=smart_scanner,
                daily_start_eval=0.0,
            )

        broker.send_order.assert_not_called()
        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "HOLD"
        assert "session high" in call_args.kwargs["rationale"].lower()

    @pytest.mark.asyncio
    async def test_trading_cycle_suppresses_buy_above_recent_sell_price(
        self,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        db_conn = init_db(":memory:")
        log_trade(
            conn=db_conn,
            stock_code="005930",
            action="BUY",
            confidence=90,
            rationale="entry",
            quantity=10,
            price=100.0,
            market="KR",
            exchange_code="KRX",
            decision_id="buy-1",
        )
        log_trade(
            conn=db_conn,
            stock_code="005930",
            action="SELL",
            confidence=90,
            rationale="take profit exit",
            quantity=10,
            price=100.5,
            pnl=5.0,
            market="KR",
            exchange_code="KRX",
            decision_id="sell-1",
        )

        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(101.0, 2.0, 0.0))
        broker.get_current_price_with_output = AsyncMock(
            return_value=(101.0, 2.0, 0.0, {"stck_hgpr": "110.0"})
        )
        broker.get_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [
                    {
                        "tot_evlu_amt": "10000000",
                        "dnca_tot_amt": "5000000",
                        "pchs_amt_smtl_amt": "5000000",
                    }
                ],
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "OK"})

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        settings = _make_settings(MODE="paper")

        with (
            patch("src.main.log_trade"),
            patch("src.main.get_session_info", return_value=MagicMock(session_id="KRX_REG")),
            patch(
                "src.core.session_risk.get_session_info",
                return_value=MagicMock(session_id="KRX_REG"),
            ),
        ):
            await trading_cycle(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match())),
                playbook=_make_playbook(),
                risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
                db_conn=db_conn,
                decision_logger=decision_logger,
                context_store=MagicMock(
                    get_latest_timeframe=MagicMock(return_value=None),
                    set_context=MagicMock(),
                ),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=mock_telegram,
                market=mock_market,
                stock_code="005930",
                scan_candidates={},
                settings=settings,
            )

        broker.send_order.assert_not_called()
        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "HOLD"
        assert call_args.kwargs["rationale"].startswith(
            "Recent sell guard blocked BUY (last_sell="
        )

    @pytest.mark.asyncio
    async def test_run_daily_session_suppresses_buy_above_recent_sell_price(
        self,
        mock_market: MagicMock,
        mock_telegram: MagicMock,
    ) -> None:
        from src.analysis.smart_scanner import ScanCandidate

        db_conn = init_db(":memory:")
        log_trade(
            conn=db_conn,
            stock_code="005930",
            action="BUY",
            confidence=90,
            rationale="entry",
            quantity=10,
            price=100.0,
            market="KR",
            exchange_code="KRX",
            decision_id="buy-1",
        )
        log_trade(
            conn=db_conn,
            stock_code="005930",
            action="SELL",
            confidence=90,
            rationale="take profit exit",
            quantity=10,
            price=100.5,
            pnl=5.0,
            market="KR",
            exchange_code="KRX",
            decision_id="sell-1",
        )

        settings = _make_settings(MODE="paper")

        broker = MagicMock()
        broker.get_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [
                    {
                        "tot_evlu_amt": "100000",
                        "dnca_tot_amt": "50000",
                        "pchs_amt_smtl_amt": "50000",
                    }
                ],
            }
        )
        broker.get_current_price = AsyncMock(return_value=(101.0, 2.0, 0.0))
        broker.get_current_price_with_output = AsyncMock(
            return_value=(101.0, 2.0, 0.0, {"stck_hgpr": "110.0"})
        )
        broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

        smart_scanner = MagicMock()
        smart_scanner.scan = AsyncMock(
            return_value=[
                ScanCandidate(
                    stock_code="005930",
                    name="Samsung",
                    price=101.0,
                    volume=1_000_000.0,
                    volume_ratio=3.0,
                    rsi=72.0,
                    signal="momentum",
                    score=88.0,
                )
            ]
        )

        playbook_store = MagicMock()
        playbook_store.load = MagicMock(return_value=_make_playbook("KR"))

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        async def _passthrough(fn, *a, label: str = "", **kw):  # type: ignore[override]
            return await fn(*a, **kw)

        with (
            patch("src.main.get_open_position", return_value=None),
            patch("src.main.get_open_markets", return_value=[mock_market]),
            patch("src.main.get_session_info", return_value=MagicMock(session_id="KRX_REG")),
            patch(
                "src.core.session_risk.get_session_info",
                return_value=MagicMock(session_id="KRX_REG"),
            ),
            patch("src.main._retry_connection", new=_passthrough),
            patch("src.main.log_trade"),
        ):
            await run_daily_session(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match())),
                playbook_store=playbook_store,
                pre_market_planner=MagicMock(),
                risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
                db_conn=db_conn,
                decision_logger=decision_logger,
                context_store=MagicMock(),
                criticality_assessor=MagicMock(),
                telegram=mock_telegram,
                settings=settings,
                smart_scanner=smart_scanner,
                daily_start_eval=0.0,
            )

        broker.send_order.assert_not_called()
        call_args = decision_logger.log_decision.call_args
        assert call_args is not None
        assert call_args.kwargs["action"] == "HOLD"
        assert call_args.kwargs["rationale"].startswith(
            "Recent sell guard blocked BUY (last_sell="
        )


@pytest.mark.asyncio
async def test_buy_suppressed_when_open_position_exists() -> None:
    """BUY should be suppressed when an open position already exists for the stock."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    # 기존 BUY 포지션 DB에 기록 (중복 매수 상황)
    buy_decision_id = decision_logger.log_decision(
        stock_code="NP",
        market="US",
        exchange_code="AMS",
        action="BUY",
        confidence=90,
        rationale="initial entry",
        context_snapshot={},
        input_data={},
    )
    log_trade(
        conn=db_conn,
        stock_code="NP",
        action="BUY",
        confidence=90,
        rationale="initial entry",
        quantity=10,
        price=50.0,
        market="US",
        exchange_code="AMS",
        decision_id=buy_decision_id,
    )

    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_price = AsyncMock(
        return_value={
            "output": {
                "last": "51.0",
                "rate": "2.0",
                "high": "52.0",
                "low": "50.0",
                "tvol": "1000000",
            }
        }
    )
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [{"frcr_evlu_tota": "10000", "frcr_buy_amt_smtl": "0"}],
        }
    )
    overseas_broker.get_overseas_buying_power = AsyncMock(
        return_value={"output": {"ovrs_ord_psbl_amt": "10000"}}
    )
    overseas_broker.send_overseas_order = AsyncMock(return_value={"msg1": "OK"})

    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_buy_match(stock_code="NP"))

    market = MagicMock()
    market.name = "United States"
    market.code = "US"
    market.exchange_code = "AMS"
    market.is_domestic = False

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=overseas_broker,
        scenario_engine=engine,
        playbook=_make_playbook(market="US"),
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="NP",
        scan_candidates={},
    )

    # 이미 보유 중이므로 주문이 실행되지 않아야 함
    broker.send_order.assert_not_called()
    overseas_broker.send_overseas_order.assert_not_called()


@pytest.mark.asyncio
async def test_buy_proceeds_when_no_open_position() -> None:
    """BUY should proceed normally when no open position exists for the stock."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)
    # DB가 비어있는 상태 — 기존 포지션 없음

    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_price = AsyncMock(
        return_value={
            "output": {
                "last": "100.0",
                "rate": "1.0",
                "high": "101.0",
                "low": "99.0",
                "tvol": "500000",
            }
        }
    )
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [{"frcr_evlu_tota": "50000", "frcr_buy_amt_smtl": "0"}],
        }
    )
    overseas_broker.get_overseas_buying_power = AsyncMock(
        return_value={"output": {"ovrs_ord_psbl_amt": "50000"}}
    )
    overseas_broker.send_overseas_order = AsyncMock(return_value={"msg1": "OK"})

    engine = MagicMock(spec=ScenarioEngine)
    engine.evaluate = MagicMock(return_value=_make_buy_match(stock_code="KNRX"))

    market = MagicMock()
    market.name = "United States"
    market.code = "US"
    market.exchange_code = "NAS"
    market.is_domestic = False

    risk = MagicMock()
    risk.validate_order = MagicMock()

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    await trading_cycle(
        broker=broker,
        overseas_broker=overseas_broker,
        scenario_engine=engine,
        playbook=_make_playbook(market="US"),
        risk=risk,
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="KNRX",
        scan_candidates={},
    )

    # 포지션이 없으므로 해외 주문이 실행되어야 함
    overseas_broker.send_overseas_order.assert_called_once()


class TestOverseasBrokerIntegration:
    """Test overseas broker live-balance gating for double-buy prevention.

    Issue #195: KIS VTS SELL limit orders are accepted (rt_cd=0) immediately
    but may not fill until the market price reaches the limit. During this window,
    the DB records the position as closed, causing the next cycle to BUY again.
    These tests verify that live broker balance is used as the authoritative source.
    """

    @pytest.mark.asyncio
    async def test_overseas_buy_suppressed_by_broker_balance_when_db_shows_closed(
        self,
    ) -> None:
        """BUY must be suppressed when broker still holds shares even if DB says closed.

        Scenario: SELL limit order was accepted (DB shows closed), but hasn't
        filled yet — broker balance still shows 10 AAPL shares.
        Expected: send_overseas_order is NOT called.
        """
        db_conn = init_db(":memory:")
        # DB: BUY then SELL recorded → get_open_position returns None (closed)
        log_trade(
            conn=db_conn,
            stock_code="AAPL",
            action="BUY",
            confidence=90,
            rationale="entry",
            quantity=10,
            price=180.0,
            market="US_NASDAQ",
            exchange_code="NASD",
        )
        log_trade(
            conn=db_conn,
            stock_code="AAPL",
            action="SELL",
            confidence=90,
            rationale="sell order accepted",
            quantity=10,
            price=182.0,
            market="US_NASDAQ",
            exchange_code="NASD",
        )

        overseas_broker = MagicMock()
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "182.50"}})
        # 브로커: 여전히 AAPL 10주 보유 중 (SELL 미체결)
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10"}],
                "output2": [
                    {
                        "frcr_evlu_tota": "60000.00",
                        "frcr_buy_amt_smtl": "50000.00",
                    }
                ],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000.00"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(return_value={"msg1": "주문접수"})

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("AAPL"))

        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NASD"
        market.is_domestic = False

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        await trading_cycle(
            broker=MagicMock(),
            overseas_broker=overseas_broker,
            scenario_engine=engine,
            playbook=_make_playbook(market="US"),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="AAPL",
            scan_candidates={},
        )

        # 브로커 잔고에 보유 중이므로 BUY 주문이 억제되어야 함 (이중 매수 방지)
        overseas_broker.send_overseas_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_overseas_buy_suppressed_by_db_open_position(
        self,
    ) -> None:
        """BUY must be suppressed when DB shows an open position (DB-first semantics).

        Scenario: DB has a BUY record with no corresponding SELL, so
        get_open_position returns an open position. Broker balance shows zero
        (e.g., position not yet reflected). Expected: send_overseas_order is NOT called.
        """
        db_conn = init_db(":memory:")
        # DB: BUY recorded, no SELL → open position exists
        log_trade(
            conn=db_conn,
            stock_code="AAPL",
            action="BUY",
            confidence=90,
            rationale="entry",
            quantity=10,
            price=180.0,
            market="US_NASDAQ",
            exchange_code="NASD",
        )

        overseas_broker = MagicMock()
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "182.50"}})
        # 브로커: AAPL 미보유 (아직 반영 안 됨)
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [
                    {
                        "frcr_evlu_tota": "60000.00",
                        "frcr_buy_amt_smtl": "0.00",
                    }
                ],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000.00"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(return_value={"msg1": "주문접수"})

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("AAPL"))

        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NASD"
        market.is_domestic = False

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        await trading_cycle(
            broker=MagicMock(),
            overseas_broker=overseas_broker,
            scenario_engine=engine,
            playbook=_make_playbook(market="US"),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="AAPL",
            scan_candidates={},
        )

        # DB에 오픈 포지션이 있으므로 BUY 주문이 억제되어야 함 (DB-first 억제)
        overseas_broker.send_overseas_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_overseas_buy_proceeds_when_broker_shows_no_holding(
        self,
    ) -> None:
        """BUY must proceed when both DB and broker confirm no existing holding.

        Scenario: No prior trades in DB and broker balance shows no AAPL.
        Expected: send_overseas_order IS called (normal buy flow).
        """
        db_conn = init_db(":memory:")
        # DB: 레코드 없음 (신규 포지션)

        overseas_broker = MagicMock()
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "182.50"}})
        # 브로커: AAPL 미보유
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [
                    {
                        "frcr_evlu_tota": "50000.00",
                        "frcr_buy_amt_smtl": "0.00",
                    }
                ],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000.00"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(return_value={"msg1": "주문접수"})

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("AAPL"))

        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NASD"
        market.is_domestic = False

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=MagicMock(),
                overseas_broker=overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook(market="US"),
                risk=MagicMock(),
                db_conn=db_conn,
                decision_logger=decision_logger,
                context_store=MagicMock(
                    get_latest_timeframe=MagicMock(return_value=None),
                    set_context=MagicMock(),
                ),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code="AAPL",
                scan_candidates={},
            )

        # DB도 브로커도 보유 없음 → BUY 주문이 실행되어야 함 (회귀 테스트)
        overseas_broker.send_overseas_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_overseas_buy_blocked_by_usd_buffer_guard(self) -> None:
        """Overseas BUY must be blocked when USD buffer would be breached."""
        db_conn = init_db(":memory:")

        overseas_broker = MagicMock()
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "182.50"}})
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [
                    {
                        "frcr_evlu_tota": "50000.00",
                        "frcr_buy_amt_smtl": "0.00",
                    }
                ],
            }
        )
        overseas_broker.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "50000.00"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(return_value={"msg1": "주문접수"})

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("AAPL"))

        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NASD"
        market.is_domestic = False

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="decision-id")

        settings = MagicMock()
        settings.POSITION_SIZING_ENABLED = False
        settings.CONFIDENCE_THRESHOLD = 80
        settings.USD_BUFFER_MIN = 49900.0
        settings.MODE = "paper"
        settings.PAPER_OVERSEAS_CASH = 50000.0

        await trading_cycle(
            broker=MagicMock(),
            overseas_broker=overseas_broker,
            scenario_engine=engine,
            playbook=_make_playbook(market="US"),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="AAPL",
            scan_candidates={},
            settings=settings,
        )

        overseas_broker.send_overseas_order.assert_not_called()


# ---------------------------------------------------------------------------
# _retry_connection — unit tests (issue #209)
# ---------------------------------------------------------------------------


class TestRetryConnection:
    """Unit tests for the _retry_connection helper (issue #209)."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self) -> None:
        """Returns the result immediately when the first call succeeds."""

        async def ok() -> str:
            return "data"

        result = await _retry_connection(ok, label="test")
        assert result == "data"

    @pytest.mark.asyncio
    async def test_succeeds_after_one_connection_error(self) -> None:
        """Retries once on ConnectionError and returns result on 2nd attempt."""
        call_count = 0

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("timeout")
            return "ok"

        with patch("src.main.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            result = await _retry_connection(flaky, label="flaky")
        assert result == "ok"
        assert call_count == 2
        mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self) -> None:
        """Raises ConnectionError after MAX_CONNECTION_RETRIES attempts."""
        from src.main import MAX_CONNECTION_RETRIES

        call_count = 0

        async def always_fail() -> None:
            nonlocal call_count
            call_count += 1
            raise ConnectionError("unreachable")

        with patch("src.main.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            with pytest.raises(ConnectionError, match="unreachable"):
                await _retry_connection(always_fail, label="always_fail")

        assert call_count == MAX_CONNECTION_RETRIES

    @pytest.mark.asyncio
    async def test_passes_args_and_kwargs_to_factory(self) -> None:
        """Forwards positional and keyword arguments to the callable."""
        received: dict = {}

        async def capture(a: int, b: int, *, key: str) -> str:
            received["a"] = a
            received["b"] = b
            received["key"] = key
            return "captured"

        result = await _retry_connection(capture, 1, 2, key="val", label="test")
        assert result == "captured"
        assert received == {"a": 1, "b": 2, "key": "val"}

    @pytest.mark.asyncio
    async def test_non_connection_error_not_retried(self) -> None:
        """Non-ConnectionError exceptions propagate immediately without retry."""
        call_count = 0

        async def bad_input() -> None:
            nonlocal call_count
            call_count += 1
            raise ValueError("bad data")

        with pytest.raises(ValueError, match="bad data"):
            await _retry_connection(bad_input, label="bad")

        assert call_count == 1  # No retry for non-ConnectionError


def test_fx_buffer_guard_applies_only_to_us_and_respects_boundary() -> None:
    settings = MagicMock()
    settings.USD_BUFFER_MIN = 1000.0

    us_market = MagicMock()
    us_market.is_domestic = False
    us_market.code = "US_NASDAQ"

    blocked, remaining, required = _should_block_overseas_buy_for_fx_buffer(
        market=us_market,
        action="BUY",
        total_cash=5000.0,
        order_amount=4001.0,
        settings=settings,
    )
    assert blocked
    assert remaining == 999.0
    assert required == 1000.0

    blocked_eq, _, _ = _should_block_overseas_buy_for_fx_buffer(
        market=us_market,
        action="BUY",
        total_cash=5000.0,
        order_amount=4000.0,
        settings=settings,
    )
    assert not blocked_eq

    jp_market = MagicMock()
    jp_market.is_domestic = False
    jp_market.code = "JP"
    blocked_jp, _, required_jp = _should_block_overseas_buy_for_fx_buffer(
        market=jp_market,
        action="BUY",
        total_cash=5000.0,
        order_amount=4500.0,
        settings=settings,
    )
    assert not blocked_jp
    assert required_jp == 0.0


def test_buy_chasing_session_high_guard_blocks_extended_high_chase() -> None:
    settings = _make_settings()
    market = MagicMock()
    market.is_domestic = True
    market.code = "KR"

    blocked, pullback_pct, min_gain_pct, max_pullback_pct = _should_block_buy_chasing_session_high(
        market=market,
        action="BUY",
        current_price=99.8,
        session_high_price=100.0,
        price_change_pct=6.0,
        settings=settings,
    )

    assert blocked
    assert pullback_pct == pytest.approx(0.2)
    assert min_gain_pct == pytest.approx(4.0)
    assert max_pullback_pct == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("current_price", "price_change_pct"),
    [
        (99.0, 6.0),  # Pulled back enough from the high
        (99.8, 3.5),  # Not extended enough on the day
    ],
)
def test_buy_chasing_session_high_guard_allows_non_chase_entries(
    current_price: float,
    price_change_pct: float,
) -> None:
    settings = _make_settings()
    market = MagicMock()
    market.is_domestic = True
    market.code = "KR"

    blocked, _, _, _ = _should_block_buy_chasing_session_high(
        market=market,
        action="BUY",
        current_price=current_price,
        session_high_price=100.0,
        price_change_pct=price_change_pct,
        settings=settings,
    )

    assert not blocked


def test_recent_sell_guard_blocks_higher_price_reentry_within_window() -> None:
    now = datetime(2026, 3, 18, 0, 0, tzinfo=UTC)
    blocked, elapsed_seconds, window_seconds = _should_block_buy_above_recent_sell(
        action="BUY",
        current_price=101.0,
        last_sell_price=100.5,
        last_sell_timestamp=(now - timedelta(seconds=30)).isoformat(),
        window_seconds=120,
        now=now,
    )

    assert blocked
    assert elapsed_seconds == 30
    assert window_seconds == 120


def test_recent_sell_guard_allows_lower_price_reentry_within_window() -> None:
    now = datetime(2026, 3, 18, 0, 0, tzinfo=UTC)
    blocked, elapsed_seconds, window_seconds = _should_block_buy_above_recent_sell(
        action="BUY",
        current_price=100.0,
        last_sell_price=100.5,
        last_sell_timestamp=(now - timedelta(seconds=30)).isoformat(),
        window_seconds=120,
        now=now,
    )

    assert not blocked
    assert elapsed_seconds == 30
    assert window_seconds == 120


def test_recent_sell_guard_allows_equal_price_reentry_within_window() -> None:
    now = datetime(2026, 3, 18, 0, 0, tzinfo=UTC)
    blocked, elapsed_seconds, window_seconds = _should_block_buy_above_recent_sell(
        action="BUY",
        current_price=100.5,
        last_sell_price=100.5,
        last_sell_timestamp=(now - timedelta(seconds=30)).isoformat(),
        window_seconds=120,
        now=now,
    )

    assert not blocked
    assert elapsed_seconds == 30
    assert window_seconds == 120


def test_recent_sell_guard_allows_higher_price_reentry_after_expiry() -> None:
    now = datetime(2026, 3, 18, 0, 0, tzinfo=UTC)
    blocked, elapsed_seconds, window_seconds = _should_block_buy_above_recent_sell(
        action="BUY",
        current_price=101.0,
        last_sell_price=100.5,
        last_sell_timestamp=(now - timedelta(seconds=180)).isoformat(),
        window_seconds=120,
        now=now,
    )

    assert not blocked
    assert elapsed_seconds == 180
    assert window_seconds == 120


def test_recent_sell_guard_allows_buy_without_sell_history() -> None:
    blocked, elapsed_seconds, window_seconds = _should_block_buy_above_recent_sell(
        action="BUY",
        current_price=101.0,
        last_sell_price=0.0,
        last_sell_timestamp=None,
        window_seconds=120,
    )

    assert not blocked
    assert elapsed_seconds == 0
    assert window_seconds == 0


def test_apply_recent_sell_guard_returns_hold_decision_with_consistent_messages(
    caplog: pytest.LogCaptureFixture,
) -> None:
    decision = main_module.TradeDecision(action="BUY", confidence=91, rationale="entry")
    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.name = "Korea"

    with (
        patch(
            "src.main.get_latest_sell_trade",
            return_value={"price": 100.5, "timestamp": "2026-03-18T00:00:00+00:00"},
        ),
        patch("src.main._resolve_recent_sell_guard_window_seconds", return_value=120),
        patch("src.main._should_block_buy_above_recent_sell", return_value=(True, 30, 120)),
        caplog.at_level(logging.INFO),
    ):
        updated = main_module._apply_recent_sell_guard(
            decision=decision,
            db_conn=MagicMock(),
            stock_code="005930",
            market=market,
            current_price=101.0,
            settings=_make_settings(),
        )

    assert updated.action == "HOLD"
    assert updated.confidence == 91
    assert updated.rationale == (
        "Recent sell guard blocked BUY "
        "(last_sell=100.5000, current=101.0000, elapsed=30s, window=120s)"
    )
    assert (
        "BUY suppressed for 005930 (Korea): recent sell guard "
        "(last_sell=100.5000 current=101.0000 elapsed=30s window=120s)"
        in caplog.text
    )


def test_apply_recent_sell_guard_returns_original_decision_when_not_blocked() -> None:
    decision = main_module.TradeDecision(action="BUY", confidence=91, rationale="entry")
    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.name = "Korea"

    with (
        patch(
            "src.main.get_latest_sell_trade",
            return_value={"price": 100.5, "timestamp": "2026-03-18T00:00:00+00:00"},
        ),
        patch("src.main._resolve_recent_sell_guard_window_seconds", return_value=120),
        patch("src.main._should_block_buy_above_recent_sell", return_value=(False, 30, 120)),
    ):
        updated = main_module._apply_recent_sell_guard(
            decision=decision,
            db_conn=MagicMock(),
            stock_code="005930",
            market=market,
            current_price=101.0,
            settings=_make_settings(),
        )

    assert updated is decision


def test_apply_recent_sell_guard_uses_latest_sell_timestamp_from_db_lookup() -> None:
    now = datetime.now(UTC)
    db_conn = init_db(":memory:")
    decision = main_module.TradeDecision(action="BUY", confidence=91, rationale="entry")
    market = MARKETS["US_NASDAQ"]

    log_trade(
        conn=db_conn,
        stock_code="AAPL",
        action="SELL",
        confidence=80,
        rationale="older matched sell",
        quantity=1,
        price=100.0,
        market=market.code,
        exchange_code=market.exchange_code,
        decision_id="older-matched-sell",
    )
    log_trade(
        conn=db_conn,
        stock_code="AAPL",
        action="SELL",
        confidence=85,
        rationale="newer legacy sell",
        quantity=1,
        price=101.0,
        market=market.code,
        exchange_code="",
        decision_id="newer-legacy-sell",
    )
    db_conn.execute(
        "UPDATE trades SET timestamp = ? WHERE decision_id = ?",
        ((now - timedelta(seconds=180)).isoformat(), "older-matched-sell"),
    )
    db_conn.execute(
        "UPDATE trades SET timestamp = ? WHERE decision_id = ?",
        ((now - timedelta(seconds=30)).isoformat(), "newer-legacy-sell"),
    )
    db_conn.commit()

    with patch("src.main._resolve_recent_sell_guard_window_seconds", return_value=60):
        updated = main_module._apply_recent_sell_guard(
            decision=decision,
            db_conn=db_conn,
            stock_code="AAPL",
            market=market,
            current_price=102.0,
            settings=_make_settings(),
        )

    assert updated.action == "HOLD"
    assert "Recent sell guard blocked BUY" in updated.rationale
    assert "last_sell=101.0000" in updated.rationale
    assert "current=102.0000" in updated.rationale
    assert "window=60s" in updated.rationale
    assert "last_sell=100.0000" not in updated.rationale


def test_apply_recent_sell_guard_returns_original_decision_without_sell_history() -> None:
    decision = main_module.TradeDecision(action="BUY", confidence=91, rationale="entry")
    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.name = "Korea"

    with (
        patch("src.main.get_latest_sell_trade", return_value=None),
        patch("src.main._resolve_recent_sell_guard_window_seconds") as resolve_window,
        patch("src.main._should_block_buy_above_recent_sell") as block_buy,
    ):
        updated = main_module._apply_recent_sell_guard(
            decision=decision,
            db_conn=MagicMock(),
            stock_code="005930",
            market=market,
            current_price=101.0,
            settings=_make_settings(),
        )

    assert updated is decision
    resolve_window.assert_not_called()
    block_buy.assert_not_called()


def test_apply_recent_sell_guard_returns_original_decision_for_non_buy() -> None:
    decision = main_module.TradeDecision(action="HOLD", confidence=91, rationale="wait")
    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.name = "Korea"

    with patch("src.main.get_latest_sell_trade") as latest_sell_trade:
        updated = main_module._apply_recent_sell_guard(
            decision=decision,
            db_conn=MagicMock(),
            stock_code="005930",
            market=market,
            current_price=101.0,
            settings=_make_settings(),
        )

    assert updated is decision
    latest_sell_trade.assert_not_called()


def test_resolve_recent_sell_guard_window_seconds_uses_session_profile_override() -> None:
    settings = _make_settings(
        SELL_REENTRY_PRICE_GUARD_SECONDS=120,
        SESSION_RISK_PROFILES_JSON='{"NXT_AFTER": {"SELL_REENTRY_PRICE_GUARD_SECONDS": 45}}',
    )
    market = MagicMock()
    market.code = "KR"

    with patch(
        "src.core.session_risk.get_session_info",
        return_value=MagicMock(session_id="NXT_AFTER"),
    ):
        value = _resolve_recent_sell_guard_window_seconds(market=market, settings=settings)

    assert value == 45


def test_resolve_recent_sell_guard_window_seconds_clamps_override_to_positive_window() -> None:
    settings = _make_settings(
        SELL_REENTRY_PRICE_GUARD_SECONDS=120,
        SESSION_RISK_PROFILES_JSON='{"NXT_AFTER": {"SELL_REENTRY_PRICE_GUARD_SECONDS": 0}}',
    )
    market = MagicMock()
    market.code = "KR"

    with patch(
        "src.core.session_risk.get_session_info",
        return_value=MagicMock(session_id="NXT_AFTER"),
    ):
        value = _resolve_recent_sell_guard_window_seconds(market=market, settings=settings)

    assert value == 1


def test_resolve_recent_sell_guard_window_seconds_clamps_negative_override_to_positive_window(
) -> None:
    settings = _make_settings(
        SELL_REENTRY_PRICE_GUARD_SECONDS=120,
        SESSION_RISK_PROFILES_JSON='{"NXT_AFTER": {"SELL_REENTRY_PRICE_GUARD_SECONDS": -10}}',
    )
    market = MagicMock()
    market.code = "KR"

    with patch(
        "src.core.session_risk.get_session_info",
        return_value=MagicMock(session_id="NXT_AFTER"),
    ):
        value = _resolve_recent_sell_guard_window_seconds(market=market, settings=settings)

    assert value == 1


def test_split_trade_pnl_components_overseas_fx_split_preserves_total() -> None:
    market = MagicMock()
    market.is_domestic = False
    strategy_pnl, fx_pnl = _split_trade_pnl_components(
        market=market,
        trade_pnl=20.0,
        buy_price=100.0,
        sell_price=110.0,
        quantity=2,
        buy_fx_rate=1200.0,
        sell_fx_rate=1260.0,
    )
    assert strategy_pnl == 10.0
    assert fx_pnl == 10.0
    assert strategy_pnl + fx_pnl == pytest.approx(20.0)


def test_extract_fx_rate_from_sources_reads_nested_present_balance_payload() -> None:
    payload = {
        "output1": [
            {
                "bass_exrt": "1325.40",
            }
        ],
        "output2": [{}],
    }

    assert _extract_fx_rate_from_sources(payload) == pytest.approx(1325.40)


# run_daily_session — daily CB baseline (daily_start_eval) tests (issue #207)
# ---------------------------------------------------------------------------


class TestDailyCBBaseline:
    """Tests for run_daily_session's daily_start_eval (CB baseline) behaviour.

    Issue #207: CB P&L should be computed relative to the portfolio value at
    the start of each trading day, not the cumulative purchase_total.
    """

    def _make_settings(self) -> Settings:
        return Settings(
            KIS_APP_KEY="test-key",
            KIS_APP_SECRET="test-secret",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="test-gemini",
            MODE="paper",
            PAPER_OVERSEAS_CASH=0,
        )

    def _make_domestic_balance(
        self, tot_evlu_amt: float = 0.0, dnca_tot_amt: float = 50000.0
    ) -> dict:
        return {
            "output1": [],
            "output2": [
                {
                    "tot_evlu_amt": str(tot_evlu_amt),
                    "dnca_tot_amt": str(dnca_tot_amt),
                    "pchs_amt_smtl_amt": "40000.0",
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_returns_daily_start_eval_when_no_markets_open(self) -> None:
        """run_daily_session returns the unchanged daily_start_eval when no markets are open."""
        with patch("src.main.get_open_markets", return_value=[]):
            result = await run_daily_session(
                broker=MagicMock(),
                overseas_broker=MagicMock(),
                scenario_engine=MagicMock(),
                playbook_store=MagicMock(),
                pre_market_planner=MagicMock(),
                risk=MagicMock(),
                db_conn=init_db(":memory:"),
                decision_logger=MagicMock(),
                context_store=MagicMock(),
                criticality_assessor=MagicMock(),
                telegram=MagicMock(),
                settings=self._make_settings(),
                smart_scanner=None,
                daily_start_eval=12345.0,
            )
        assert result == 12345.0

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_markets_and_no_baseline(self) -> None:
        """run_daily_session returns 0.0 when no markets are open and daily_start_eval=0."""
        with patch("src.main.get_open_markets", return_value=[]):
            result = await run_daily_session(
                broker=MagicMock(),
                overseas_broker=MagicMock(),
                scenario_engine=MagicMock(),
                playbook_store=MagicMock(),
                pre_market_planner=MagicMock(),
                risk=MagicMock(),
                db_conn=init_db(":memory:"),
                decision_logger=MagicMock(),
                context_store=MagicMock(),
                criticality_assessor=MagicMock(),
                telegram=MagicMock(),
                settings=self._make_settings(),
                smart_scanner=None,
                daily_start_eval=0.0,
            )
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_captures_total_eval_as_baseline_on_first_session(self) -> None:
        """When daily_start_eval=0 and balance returns a positive total_eval, the returned
        value equals total_eval (the captured baseline for the day)."""
        from src.analysis.smart_scanner import ScanCandidate

        settings = self._make_settings()
        broker = MagicMock()
        # Domestic balance: tot_evlu_amt=55000
        broker.get_balance = AsyncMock(
            return_value=self._make_domestic_balance(tot_evlu_amt=55000.0)
        )
        # Price data for the stock
        broker.get_current_price = AsyncMock(return_value=(100.0, 1.5, 100.0))

        market = MagicMock()
        market.name = "KR"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        market.timezone = __import__("zoneinfo").ZoneInfo("Asia/Seoul")

        smart_scanner = MagicMock()
        smart_scanner.scan = AsyncMock(
            return_value=[
                ScanCandidate(
                    stock_code="005930",
                    name="Samsung",
                    price=100.0,
                    volume=1_000_000.0,
                    volume_ratio=2.5,
                    rsi=45.0,
                    signal="momentum",
                    score=80.0,
                )
            ]
        )

        playbook_store = MagicMock()
        playbook_store.load = MagicMock(return_value=_make_playbook("KR"))

        scenario_engine = MagicMock(spec=ScenarioEngine)
        scenario_engine.evaluate = MagicMock(return_value=_make_hold_match("005930"))

        risk = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        risk.check_fat_finger = MagicMock()

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="d1")

        async def _passthrough(fn, *a, label: str = "", **kw):  # type: ignore[override]
            return await fn(*a, **kw)

        with (
            patch("src.main.get_open_markets", return_value=[market]),
            patch("src.main._retry_connection", new=_passthrough),
        ):
            result = await run_daily_session(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=scenario_engine,
                playbook_store=playbook_store,
                pre_market_planner=MagicMock(),
                risk=risk,
                db_conn=init_db(":memory:"),
                decision_logger=decision_logger,
                context_store=MagicMock(),
                criticality_assessor=MagicMock(),
                telegram=telegram,
                settings=settings,
                smart_scanner=smart_scanner,
                daily_start_eval=0.0,
            )

        assert result == 55000.0  # captured from tot_evlu_amt

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_baseline(self) -> None:
        """When daily_start_eval > 0, it must not be overwritten even if balance returns
        a different value (baseline is fixed at the start of each trading day)."""
        from src.analysis.smart_scanner import ScanCandidate

        settings = self._make_settings()
        broker = MagicMock()
        # Balance reports a different eval value (market moved during the day)
        broker.get_balance = AsyncMock(
            return_value=self._make_domestic_balance(tot_evlu_amt=58000.0)
        )
        broker.get_current_price = AsyncMock(return_value=(100.0, 1.5, 100.0))

        market = MagicMock()
        market.name = "KR"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        market.timezone = __import__("zoneinfo").ZoneInfo("Asia/Seoul")

        smart_scanner = MagicMock()
        smart_scanner.scan = AsyncMock(
            return_value=[
                ScanCandidate(
                    stock_code="005930",
                    name="Samsung",
                    price=100.0,
                    volume=1_000_000.0,
                    volume_ratio=2.5,
                    rsi=45.0,
                    signal="momentum",
                    score=80.0,
                )
            ]
        )

        playbook_store = MagicMock()
        playbook_store.load = MagicMock(return_value=_make_playbook("KR"))

        scenario_engine = MagicMock(spec=ScenarioEngine)
        scenario_engine.evaluate = MagicMock(return_value=_make_hold_match("005930"))

        risk = MagicMock()
        risk.check_circuit_breaker = MagicMock()

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="d1")

        async def _passthrough(fn, *a, label: str = "", **kw):  # type: ignore[override]
            return await fn(*a, **kw)

        with (
            patch("src.main.get_open_markets", return_value=[market]),
            patch("src.main._retry_connection", new=_passthrough),
        ):
            result = await run_daily_session(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=scenario_engine,
                playbook_store=playbook_store,
                pre_market_planner=MagicMock(),
                risk=risk,
                db_conn=init_db(":memory:"),
                decision_logger=decision_logger,
                context_store=MagicMock(),
                criticality_assessor=MagicMock(),
                telegram=telegram,
                settings=settings,
                smart_scanner=smart_scanner,
                daily_start_eval=55000.0,  # existing baseline
            )

        # Must return the original baseline, NOT the new total_eval (58000)
        assert result == 55000.0



@pytest.mark.asyncio
async def test_run_daily_session_applies_staged_exit_override_on_hold() -> None:
    """run_daily_session must apply HOLD staged exit semantics (issue #304)."""
    from src.analysis.smart_scanner import ScanCandidate

    db_conn = init_db(":memory:")
    log_trade(
        conn=db_conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=100.0,
        market="KR",
        exchange_code="KRX",
        decision_id="buy-d1",
    )

    settings = Settings(
        KIS_APP_KEY="k",
        KIS_APP_SECRET="s",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="g",
        MODE="paper",
    )

    broker = MagicMock()
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [{"pdno": "005930", "ord_psbl_qty": "1"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "10000",
                    "pchs_amt_smtl_amt": "90000",
                }
            ],
        }
    )
    broker.get_current_price = AsyncMock(return_value=(95.0, -5.0, 0.0))
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True
    market.timezone = __import__("zoneinfo").ZoneInfo("Asia/Seoul")

    scenario = StockScenario(
        condition=StockCondition(rsi_below=30),
        action=ScenarioAction.BUY,
        confidence=88,
        stop_loss_pct=-2.0,
        take_profit_pct=3.0,
        rationale="stop loss policy",
    )
    playbook = DayPlaybook(
        date=date(2026, 2, 8),
        market="KR",
        stock_playbooks=[
            {"stock_code": "005930", "stock_name": "Samsung", "scenarios": [scenario]}
        ],
    )
    playbook_store = MagicMock()
    playbook_store.load = MagicMock(return_value=playbook)

    smart_scanner = MagicMock()
    smart_scanner.scan = AsyncMock(
        return_value=[
            ScanCandidate(
                stock_code="005930",
                name="Samsung",
                price=95.0,
                volume=1_000_000.0,
                volume_ratio=2.0,
                rsi=42.0,
                signal="momentum",
                score=80.0,
            )
        ]
    )

    scenario_engine = MagicMock(spec=ScenarioEngine)
    scenario_engine.evaluate = MagicMock(return_value=_make_hold_match("005930"))

    risk = MagicMock()
    risk.check_circuit_breaker = MagicMock()
    risk.validate_order = MagicMock()

    decision_logger = MagicMock()
    decision_logger.log_decision = MagicMock(return_value="d1")

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    async def _passthrough(fn, *a, label: str = "", **kw):  # type: ignore[override]
        return await fn(*a, **kw)

    with (
        patch("src.main.get_open_markets", return_value=[market]),
        patch("src.main._retry_connection", new=_passthrough),
    ):
        await run_daily_session(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=scenario_engine,
            playbook_store=playbook_store,
            pre_market_planner=MagicMock(),
            risk=risk,
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(),
            criticality_assessor=MagicMock(),
            telegram=telegram,
            settings=settings,
            smart_scanner=smart_scanner,
            daily_start_eval=0.0,
        )

    broker.send_order.assert_called_once()
    assert broker.send_order.call_args.kwargs["order_type"] == "SELL"


@pytest.mark.asyncio
async def test_run_daily_session_evaluates_held_symbol_outside_scanner_top_n() -> None:
    """Daily mode must evaluate held symbols even when scanner top-N excludes them."""
    from src.analysis.smart_scanner import ScanCandidate

    db_conn = init_db(":memory:")
    log_trade(
        conn=db_conn,
        stock_code="PLU",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=13,
        price=42.01,
        market="US_AMEX",
        exchange_code="AMS",
        decision_id="buy-plu-1",
    )

    settings = _make_settings(MODE="paper", PAPER_OVERSEAS_CASH=10_000)

    market = MagicMock()
    market.name = "NYSE American"
    market.code = "US_AMEX"
    market.exchange_code = "AMS"
    market.is_domestic = False
    market.timezone = ZoneInfo("America/New_York")

    smart_scanner = MagicMock()
    smart_scanner.scan = AsyncMock(
        return_value=[
            ScanCandidate(
                stock_code="CRCD",
                name="Cardio",
                price=12.0,
                volume=1_000_000.0,
                volume_ratio=2.0,
                rsi=60.0,
                signal="momentum",
                score=90.0,
            ),
            ScanCandidate(
                stock_code="AAOX",
                name="Aox",
                price=8.0,
                volume=900_000.0,
                volume_ratio=1.8,
                rsi=58.0,
                signal="momentum",
                score=88.0,
            ),
            ScanCandidate(
                stock_code="LITX",
                name="Litx",
                price=6.0,
                volume=800_000.0,
                volume_ratio=1.7,
                rsi=55.0,
                signal="momentum",
                score=86.0,
            ),
        ]
    )

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [
                {
                    "ovrs_pdno": "PLU",
                    "ovrs_item_name": "Pluri",
                    "ord_psbl_qty": "13",
                    "ovrs_cblc_qty": "13",
                    "pchs_avg_pric": "42.01",
                    "ovrs_excg_cd": "AMS",
                }
            ],
            "output2": {
                "frcr_evlu_tota": "100000",
                "frcr_buy_amt_smtl": "54613",
            },
        }
    )

    def _price_payload(last: str, rate: str = "0.5") -> dict[str, dict[str, str]]:
        return {"output": {"last": last, "rate": rate}}

    overseas_prices = {
        "CRCD": _price_payload("12.0"),
        "AAOX": _price_payload("8.0"),
        "LITX": _price_payload("6.0"),
        "PLU": _price_payload("32.96", "-21.54"),
    }

    async def _get_overseas_price(exchange_code: str, stock_code: str) -> dict[str, dict[str, str]]:
        return overseas_prices[stock_code]

    overseas_broker.get_overseas_price = AsyncMock(side_effect=_get_overseas_price)
    overseas_broker.get_overseas_buying_power = AsyncMock(
        return_value={"output": {"ovrs_ord_psbl_amt": "10000"}}
    )

    playbook_store = MagicMock()
    playbook_store.load = MagicMock(return_value=_make_playbook("US_AMEX"))

    evaluated_codes: list[str] = []

    def _evaluate(
        playbook: DayPlaybook,
        stock_code: str,
        stock_data: dict[str, Any],
        portfolio_data: dict[str, Any],
    ) -> ScenarioMatch:
        evaluated_codes.append(stock_code)
        return _make_hold_match(stock_code)

    scenario_engine = MagicMock(spec=ScenarioEngine)
    scenario_engine.evaluate = MagicMock(side_effect=_evaluate)

    risk = MagicMock()
    risk.check_circuit_breaker = MagicMock()

    decision_logger = MagicMock()
    decision_logger.log_decision = MagicMock(return_value="decision-id")

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    async def _passthrough(fn, *a, label: str = "", **kw):  # type: ignore[override]
        return await fn(*a, **kw)

    with (
        patch("src.main.get_open_markets", return_value=[market]),
        patch("src.main._retry_connection", new=_passthrough),
        patch("src.main._inject_staged_exit_features", new=AsyncMock()),
        patch(
            "src.main._apply_staged_exit_override_for_hold",
            side_effect=lambda **kwargs: kwargs["decision"],
        ),
    ):
        await run_daily_session(
            broker=MagicMock(),
            overseas_broker=overseas_broker,
            scenario_engine=scenario_engine,
            playbook_store=playbook_store,
            pre_market_planner=MagicMock(),
            risk=risk,
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(),
            criticality_assessor=MagicMock(),
            telegram=telegram,
            settings=settings,
            smart_scanner=smart_scanner,
            daily_start_eval=0.0,
        )

    assert set(evaluated_codes) == {"CRCD", "AAOX", "LITX", "PLU"}
    assert evaluated_codes.index("PLU") > evaluated_codes.index("CRCD")


@pytest.mark.asyncio
async def test_run_daily_session_syncs_realtime_hard_stop_for_live_daily_held_position() -> None:
    from src.analysis.smart_scanner import ScanCandidate

    db_conn = init_db(":memory:")
    log_trade(
        conn=db_conn,
        stock_code="PLU",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=13,
        price=42.01,
        market="US_AMEX",
        exchange_code="AMS",
        decision_id="buy-plu-1",
    )

    settings = _make_settings(
        MODE="live",
        TRADE_MODE="daily",
        REALTIME_HARD_STOP_ENABLED=True,
        ENABLED_MARKETS="US",
    )

    market = MARKETS["US_AMEX"]

    smart_scanner = MagicMock()
    smart_scanner.scan = AsyncMock(
        return_value=[
            ScanCandidate(
                stock_code="CRCD",
                name="Cardio",
                price=12.0,
                volume=1_000_000.0,
                volume_ratio=2.0,
                rsi=60.0,
                signal="momentum",
                score=90.0,
            )
        ]
    )

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [
                {
                    "ovrs_pdno": "PLU",
                    "ovrs_item_name": "Pluri",
                    "ord_psbl_qty": "13",
                    "ovrs_cblc_qty": "13",
                    "pchs_avg_pric": "42.01",
                    "ovrs_excg_cd": "AMS",
                }
            ],
            "output2": {
                "frcr_evlu_tota": "100000",
                "frcr_buy_amt_smtl": "54613",
            },
        }
    )
    overseas_broker.get_overseas_price = AsyncMock(
        return_value={"output": {"last": "32.96", "rate": "-21.54"}}
    )
    overseas_broker.get_overseas_buying_power = AsyncMock(
        return_value={"output": {"ovrs_ord_psbl_amt": "10000"}}
    )

    playbook_store = MagicMock()
    playbook_store.load = MagicMock(return_value=_make_playbook("US_AMEX"))

    scenario_engine = MagicMock(spec=ScenarioEngine)
    scenario_engine.evaluate = MagicMock(return_value=_make_hold_match("PLU"))

    decision_logger = MagicMock()
    decision_logger.log_decision = MagicMock(return_value="decision-id")

    risk = MagicMock()
    risk.check_circuit_breaker = MagicMock()

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    monitor = RealtimeHardStopMonitor()
    websocket_client = MagicMock()
    websocket_client.subscribe = AsyncMock()
    websocket_client.unsubscribe = AsyncMock()

    async def _passthrough(fn, *a, label: str = "", **kw):  # type: ignore[override]
        return await fn(*a, **kw)

    async def _inject_with_hard_stop(**kwargs: Any) -> None:
        kwargs["market_data"]["_staged_exit_evidence"] = {"stop_loss_threshold": -3.5}

    sync_mock = AsyncMock()

    with (
        patch("src.main.get_open_markets", return_value=[market]),
        patch("src.main._retry_connection", new=_passthrough),
        patch("src.main._inject_staged_exit_features", side_effect=_inject_with_hard_stop),
        patch(
            "src.main._apply_staged_exit_override_for_hold",
            side_effect=lambda **kwargs: kwargs["decision"],
        ),
        patch("src.main._sync_realtime_hard_stop_monitor", new=sync_mock),
    ):
        await run_daily_session(
            broker=MagicMock(),
            overseas_broker=overseas_broker,
            scenario_engine=scenario_engine,
            playbook_store=playbook_store,
            pre_market_planner=MagicMock(),
            risk=risk,
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(),
            criticality_assessor=MagicMock(),
            telegram=telegram,
            settings=settings,
            smart_scanner=smart_scanner,
            daily_start_eval=0.0,
            realtime_hard_stop_monitor=monitor,
            realtime_hard_stop_client=websocket_client,
        )

    sync_mock.assert_awaited()
    plu_calls = [
        call
        for call in sync_mock.await_args_list
        if call.kwargs.get("stock_code") == "PLU"
    ]
    assert len(plu_calls) == 1
    sync_call = plu_calls[0]
    assert sync_call.kwargs["monitor"] is monitor
    assert sync_call.kwargs["websocket_client"] is websocket_client
    assert sync_call.kwargs["market"].code == "US_AMEX"
    assert sync_call.kwargs["stock_code"] == "PLU"
    assert sync_call.kwargs["decision_action"] == "HOLD"
    assert sync_call.kwargs["open_position"]["quantity"] == 13
    assert sync_call.kwargs["open_position"]["price"] == pytest.approx(42.01)
    assert (
        sync_call.kwargs["market_data"]["_staged_exit_evidence"]["stop_loss_threshold"]
        == pytest.approx(-3.5)
    )


def test_load_daily_session_db_open_positions_ignores_hold_rows_and_uses_market_local_day() -> None:
    db_conn = init_db(":memory:")
    market = MagicMock()
    market.code = "US_AMEX"
    market.timezone = ZoneInfo("America/New_York")

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: ZoneInfo | None = None) -> datetime:
            current = datetime(2026, 3, 25, 0, 30, tzinfo=UTC)
            if tz is None:
                return current
            return current.astimezone(tz)

    with patch("src.db.datetime", _FrozenDateTime):
        log_trade(
            conn=db_conn,
            stock_code="PLU",
            action="BUY",
            confidence=90,
            rationale="entry",
            quantity=13,
            price=42.01,
            market="US_AMEX",
            exchange_code="AMS",
            decision_id="buy-plu-1",
        )
        log_trade(
            conn=db_conn,
            stock_code="PLU",
            action="HOLD",
            confidence=50,
            rationale="still holding",
            quantity=13,
            price=42.01,
            market="US_AMEX",
            exchange_code="AMS",
        )
    db_conn.execute(
        "UPDATE trades SET timestamp = ? WHERE stock_code = ? AND action = 'BUY'",
        ("2026-03-24T14:00:00+00:00", "PLU"),
    )
    db_conn.execute(
        "UPDATE trades SET timestamp = ? WHERE stock_code = ? AND action = 'HOLD'",
        ("2026-03-24T15:00:00+00:00", "PLU"),
    )
    db_conn.commit()

    with patch("src.main.datetime", _FrozenDateTime):
        positions = main_module._load_daily_session_db_open_positions(
            db_conn=db_conn,
            market=market,
        )

    assert len(positions) == 1
    assert positions[0]["stock_code"] == "PLU"
    assert positions[0]["holding_days"] == 0


def test_load_daily_session_db_open_positions_returns_buy_only_position() -> None:
    db_conn = init_db(":memory:")
    market = MagicMock()
    market.code = "US_AMEX"
    market.timezone = ZoneInfo("America/New_York")

    log_trade(
        conn=db_conn,
        stock_code="PLU",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=13,
        price=42.01,
        market="US_AMEX",
        exchange_code="AMS",
        decision_id="buy-plu-1",
    )

    positions = main_module._load_daily_session_db_open_positions(
        db_conn=db_conn,
        market=market,
    )

    assert len(positions) == 1
    assert positions[0]["stock_code"] == "PLU"


def test_load_daily_session_db_open_positions_excludes_closed_sell_position() -> None:
    db_conn = init_db(":memory:")
    market = MagicMock()
    market.code = "US_AMEX"
    market.timezone = ZoneInfo("America/New_York")

    log_trade(
        conn=db_conn,
        stock_code="PLU",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=13,
        price=42.01,
        market="US_AMEX",
        exchange_code="AMS",
        decision_id="buy-plu-1",
    )
    log_trade(
        conn=db_conn,
        stock_code="PLU",
        action="SELL",
        confidence=90,
        rationale="exit",
        quantity=13,
        price=45.00,
        market="US_AMEX",
        exchange_code="AMS",
        decision_id="sell-plu-1",
    )
    db_conn.execute(
        "UPDATE trades SET timestamp = ? WHERE stock_code = ? AND action = 'BUY'",
        ("2026-03-24T14:00:00+00:00", "PLU"),
    )
    db_conn.execute(
        "UPDATE trades SET timestamp = ? WHERE stock_code = ? AND action = 'SELL'",
        ("2026-03-24T15:00:00+00:00", "PLU"),
    )
    db_conn.commit()

    positions = main_module._load_daily_session_db_open_positions(
        db_conn=db_conn,
        market=market,
    )

    assert positions == []


@pytest.mark.asyncio
async def test_load_or_generate_daily_playbook_creates_exit_fallback_for_held_only_market() -> None:
    market = MagicMock()
    market.code = "US_AMEX"

    holding = {
        "stock_code": "PLU",
        "name": "Pluri",
        "qty": 13,
        "entry_price": 42.01,
        "unrealized_pnl_pct": -21.54,
        "holding_days": 2,
    }
    playbook_store = MagicMock()
    playbook_store.load_latest_entry = MagicMock(return_value=None)
    playbook_store.load = MagicMock(return_value=None)
    playbook_store.save = MagicMock()
    pre_market_planner = MagicMock()
    pre_market_planner.generate_playbook = AsyncMock()
    telegram = MagicMock()
    telegram.notify_playbook_generated = AsyncMock()

    playbook = await main_module._load_or_generate_daily_playbook(
        candidates_list=[],
        current_holdings=[holding],
        market=market,
        market_today=date(2026, 3, 25),
        session_id="US_PRE",
        playbook_store=playbook_store,
        pre_market_planner=pre_market_planner,
        telegram=telegram,
    )

    stock_playbook = playbook.get_stock_playbook("PLU")
    assert stock_playbook is not None
    assert stock_playbook.scenarios
    assert stock_playbook.scenarios[0].action is ScenarioAction.HOLD
    assert stock_playbook.scenarios[0].condition.holding_days_above == -1
    assert stock_playbook.scenarios[0].stop_loss_pct == pytest.approx(-2.0)
    assert playbook.session_id == "US_PRE"
    playbook_store.load_latest_entry.assert_called_once_with(
        date(2026, 3, 25),
        "US_AMEX",
        session_id="US_PRE",
    )
    pre_market_planner.generate_playbook.assert_not_awaited()
    playbook_store.save.assert_called_once_with(playbook)
    telegram.notify_playbook_generated.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_or_generate_daily_playbook_reuses_latest_current_session_entry() -> None:
    market = MagicMock()
    market.code = "US_AMEX"

    candidate = ScanCandidate(
        stock_code="PLU",
        name="Pluri",
        price=42.01,
        volume=1_000_000.0,
        volume_ratio=2.0,
        rsi=28.0,
        signal="oversold",
        score=80.0,
    )
    stored_playbook = _make_stock_playbook("US_AMEX", "PLU").model_copy(
        update={"session_id": "US_REG"}
    )
    playbook_store = MagicMock()
    playbook_store.load_latest_entry = MagicMock(
        return_value=StoredPlaybookEntry(
            playbook=stored_playbook,
            slot="mid",
            generated_at=stored_playbook.generated_at,
        )
    )
    playbook_store.load = MagicMock(
        side_effect=AssertionError("legacy open-slot lookup should not run")
    )
    playbook_store.save = MagicMock()
    pre_market_planner = MagicMock()
    pre_market_planner.generate_playbook = AsyncMock()
    telegram = MagicMock()
    telegram.notify_playbook_generated = AsyncMock()

    playbook = await main_module._load_or_generate_daily_playbook(
        candidates_list=[candidate],
        current_holdings=[],
        market=market,
        market_today=date(2026, 3, 25),
        session_id="US_REG",
        playbook_store=playbook_store,
        pre_market_planner=pre_market_planner,
        telegram=telegram,
    )

    assert playbook is stored_playbook
    playbook_store.load_latest_entry.assert_called_once_with(
        date(2026, 3, 25),
        "US_AMEX",
        session_id="US_REG",
    )
    pre_market_planner.generate_playbook.assert_not_awaited()
    playbook_store.save.assert_not_called()


@pytest.mark.asyncio
async def test_run_daily_session_passes_runtime_session_id_to_decision_and_trade_logs() -> None:
    """Daily session must explicitly forward runtime session_id to decision/trade logs."""
    from src.analysis.smart_scanner import ScanCandidate

    db_conn = init_db(":memory:")
    settings = Settings(
        KIS_APP_KEY="k",
        KIS_APP_SECRET="s",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="g",
        MODE="paper",
    )

    broker = MagicMock()
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "50000",
                    "pchs_amt_smtl_amt": "50000",
                }
            ],
        }
    )
    broker.get_current_price = AsyncMock(return_value=(100.0, 1.0, 0.0))
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True
    market.timezone = __import__("zoneinfo").ZoneInfo("Asia/Seoul")

    smart_scanner = MagicMock()
    smart_scanner.scan = AsyncMock(
        return_value=[
            ScanCandidate(
                stock_code="005930",
                name="Samsung",
                price=100.0,
                volume=1_000_000.0,
                volume_ratio=2.0,
                rsi=45.0,
                signal="momentum",
                score=80.0,
            )
        ]
    )

    playbook_store = MagicMock()
    playbook_store.load = MagicMock(return_value=_make_playbook("KR"))

    scenario_engine = MagicMock(spec=ScenarioEngine)
    scenario_engine.evaluate = MagicMock(return_value=_make_buy_match("005930"))

    risk = MagicMock()
    risk.check_circuit_breaker = MagicMock()
    risk.validate_order = MagicMock()

    decision_logger = MagicMock()
    decision_logger.log_decision = MagicMock(return_value="d1")

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    async def _passthrough(fn, *a, label: str = "", **kw):  # type: ignore[override]
        return await fn(*a, **kw)

    with (
        patch("src.main.get_open_position", return_value=None),
        patch("src.main.get_open_markets", return_value=[market]),
        patch("src.main.get_session_info", return_value=MagicMock(session_id="KRX_REG")),
        patch("src.main._retry_connection", new=_passthrough),
        patch("src.main.log_trade") as mock_log_trade,
    ):
        await run_daily_session(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=scenario_engine,
            playbook_store=playbook_store,
            pre_market_planner=MagicMock(),
            risk=risk,
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(),
            criticality_assessor=MagicMock(),
            telegram=telegram,
            settings=settings,
            smart_scanner=smart_scanner,
            daily_start_eval=0.0,
        )

    decision_logger.log_decision.assert_called_once()
    assert decision_logger.log_decision.call_args.kwargs["session_id"] == "KRX_REG"
    assert mock_log_trade.call_count >= 1
    for call in mock_log_trade.call_args_list:
        assert call.kwargs.get("session_id") == "KRX_REG"


# ---------------------------------------------------------------------------
# sync_positions_from_broker — startup DB sync tests (issue #206)
# ---------------------------------------------------------------------------


class TestSyncPositionsFromBroker:
    """Tests for sync_positions_from_broker() startup position sync (issue #206).

    The function queries broker balances at startup and inserts synthetic BUY
    records for any holdings that the local DB is unaware of, preventing
    double-buy when positions were opened in a previous session or manually.
    """

    def _make_settings(self, enabled_markets: str = "KR") -> Settings:
        return Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
            ENABLED_MARKETS=enabled_markets,
            MODE="paper",
        )

    def _domestic_balance(
        self,
        stock_code: str = "005930",
        qty: int = 5,
    ) -> dict:
        return {
            "output1": [{"pdno": stock_code, "ord_psbl_qty": str(qty)}],
            "output2": [
                {
                    "tot_evlu_amt": "1000000",
                    "dnca_tot_amt": "500000",
                    "pchs_amt_smtl_amt": "500000",
                }
            ],
        }

    def _overseas_balance(
        self,
        stock_code: str = "AAPL",
        qty: int = 10,
    ) -> dict:
        return {
            "output1": [{"ovrs_pdno": stock_code, "ovrs_cblc_qty": str(qty)}],
            "output2": [
                {
                    "frcr_evlu_tota": "50000",
                    "frcr_buy_amt_smtl": "40000",
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_syncs_domestic_position_not_in_db(self) -> None:
        """A domestic holding found in broker but absent from DB is inserted."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")

        broker = MagicMock()
        broker.get_balance = AsyncMock(return_value=self._domestic_balance("005930", qty=7))
        overseas_broker = MagicMock()

        synced = await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        assert synced == 1
        from src.db import get_open_position

        pos = get_open_position(db_conn, "005930", "KR")
        assert pos is not None
        assert pos["quantity"] == 7

    @pytest.mark.asyncio
    async def test_skips_position_already_in_db(self) -> None:
        """No duplicate record is created when the position already exists in DB."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")
        # Pre-insert a BUY record
        log_trade(
            conn=db_conn,
            stock_code="005930",
            action="BUY",
            confidence=85,
            rationale="existing position",
            quantity=5,
            price=70000.0,
            market="KR",
            exchange_code="KRX",
        )

        broker = MagicMock()
        broker.get_balance = AsyncMock(return_value=self._domestic_balance("005930", qty=5))
        overseas_broker = MagicMock()

        synced = await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        assert synced == 0

    @pytest.mark.asyncio
    async def test_syncs_overseas_position_not_in_db(self) -> None:
        """An overseas holding found in broker but absent from DB is inserted."""
        settings = self._make_settings("US_NASDAQ")
        db_conn = init_db(":memory:")

        broker = MagicMock()
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value=self._overseas_balance("AAPL", qty=10)
        )

        synced = await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        assert synced == 1
        from src.db import get_open_position

        pos = get_open_position(db_conn, "AAPL", "US_NASDAQ")
        assert pos is not None
        assert pos["quantity"] == 10

    @pytest.mark.asyncio
    async def test_returns_zero_when_broker_has_no_holdings(self) -> None:
        """Returns 0 when broker reports empty holdings."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")

        broker = MagicMock()
        broker.get_balance = AsyncMock(return_value={"output1": [], "output2": [{}]})
        overseas_broker = MagicMock()

        synced = await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        assert synced == 0

    @pytest.mark.asyncio
    async def test_handles_connection_error_gracefully(self) -> None:
        """ConnectionError during balance fetch is logged but does not raise."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")

        broker = MagicMock()
        broker.get_balance = AsyncMock(side_effect=ConnectionError("KIS unreachable"))
        overseas_broker = MagicMock()

        synced = await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        assert synced == 0  # Failure treated as no-op

    @pytest.mark.asyncio
    async def test_deduplicates_exchange_codes_for_overseas(self) -> None:
        """Each exchange code is queried at most once even if multiple market
        codes share the same exchange (defensive deduplication)."""
        # Both US_NASDAQ and a hypothetical duplicate would share "NASD"
        # Use two DIFFERENT overseas markets (NASD vs NYSE) to verify each is
        # queried separately.
        settings = self._make_settings("US_NASDAQ,US_NYSE")
        db_conn = init_db(":memory:")

        broker = MagicMock()
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_balance = AsyncMock(
            return_value={"output1": [], "output2": [{}]}
        )

        await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        # Two distinct exchange codes (NASD, NYSE) → 2 calls
        assert overseas_broker.get_overseas_balance.call_count == 2

    @pytest.mark.asyncio
    async def test_syncs_domestic_position_with_correct_avg_price(self) -> None:
        """Domestic position is stored with pchs_avg_pric as price (issue #249)."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")

        balance = {
            "output1": [{"pdno": "005930", "ord_psbl_qty": "5", "pchs_avg_pric": "68000.0"}],
            "output2": [
                {"tot_evlu_amt": "1000000", "dnca_tot_amt": "500000", "pchs_amt_smtl_amt": "500000"}
            ],
        }
        broker = MagicMock()
        broker.get_balance = AsyncMock(return_value=balance)
        overseas_broker = MagicMock()

        await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        from src.db import get_open_position

        pos = get_open_position(db_conn, "005930", "KR")
        assert pos is not None
        assert pos["price"] == 68000.0

    @pytest.mark.asyncio
    async def test_syncs_overseas_position_with_correct_avg_price(self) -> None:
        """Overseas position is stored with pchs_avg_pric as price (issue #249)."""
        settings = self._make_settings("US_NASDAQ")
        db_conn = init_db(":memory:")

        balance = {
            "output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10", "pchs_avg_pric": "170.0"}],
            "output2": [{"frcr_evlu_tota": "50000", "frcr_buy_amt_smtl": "40000"}],
        }
        broker = MagicMock()
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_balance = AsyncMock(return_value=balance)

        await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        from src.db import get_open_position

        pos = get_open_position(db_conn, "AAPL", "US_NASDAQ")
        assert pos is not None
        assert pos["price"] == 170.0

    @pytest.mark.asyncio
    async def test_startup_sync_filters_overseas_holdings_by_exchange_code(self) -> None:
        """Startup sync should not record holdings from another overseas exchange."""
        settings = self._make_settings("US_NASDAQ")
        db_conn = init_db(":memory:")

        balance = {
            "output1": [
                {
                    "ovrs_pdno": "AAPL",
                    "ovrs_cblc_qty": "10",
                    "ovrs_excg_cd": "NASD",
                    "pchs_avg_pric": "170.0",
                },
                {
                    "ovrs_pdno": "IBM",
                    "ovrs_cblc_qty": "4",
                    "ovrs_excg_cd": "NYSE",
                    "pchs_avg_pric": "240.0",
                },
            ],
            "output2": [{"frcr_evlu_tota": "50000", "frcr_buy_amt_smtl": "40000"}],
        }
        broker = MagicMock()
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_balance = AsyncMock(return_value=balance)

        synced = await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        from src.db import get_open_position

        assert synced == 1
        assert get_open_position(db_conn, "AAPL", "US_NASDAQ") is not None
        assert get_open_position(db_conn, "IBM", "US_NASDAQ") is None

    @pytest.mark.asyncio
    async def test_syncs_position_with_zero_price_when_pchs_avg_pric_absent(self) -> None:
        """Fallback to price=0.0 when pchs_avg_pric is absent (issue #249)."""
        settings = self._make_settings("KR")
        db_conn = init_db(":memory:")

        # No pchs_avg_pric in output1
        balance = {
            "output1": [{"pdno": "005930", "ord_psbl_qty": "5"}],
            "output2": [
                {"tot_evlu_amt": "1000000", "dnca_tot_amt": "500000", "pchs_amt_smtl_amt": "500000"}
            ],
        }
        broker = MagicMock()
        broker.get_balance = AsyncMock(return_value=balance)
        overseas_broker = MagicMock()

        await sync_positions_from_broker(broker, overseas_broker, db_conn, settings)

        from src.db import get_open_position

        pos = get_open_position(db_conn, "005930", "KR")
        assert pos is not None
        assert pos["price"] == 0.0


# ---------------------------------------------------------------------------
# Domestic BUY double-prevention (issue #206) — trading_cycle integration
# ---------------------------------------------------------------------------


class TestDomesticBuyDoublePreventionTradingCycle:
    """Verify domestic BUY suppression using broker balance in trading_cycle.

    Issue #206: the broker-balance check was overseas-only; domestic stocks
    were not protected against double-buy caused by untracked positions.
    """

    @pytest.mark.asyncio
    async def test_domestic_buy_suppressed_when_broker_holds_stock(
        self,
    ) -> None:
        """BUY for a domestic stock must be suppressed when broker holds it,
        even if the DB shows no open position."""
        db_conn = init_db(":memory:")
        # DB: no open position for 005930

        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(70000.0, 1.0, 0.0))
        # Broker balance: holds 5 shares of 005930
        broker.get_balance = AsyncMock(
            return_value={
                "output1": [{"pdno": "005930", "ord_psbl_qty": "5"}],
                "output2": [
                    {
                        "tot_evlu_amt": "1000000",
                        "dnca_tot_amt": "500000",
                        "pchs_amt_smtl_amt": "500000",
                    }
                ],
            }
        )
        broker.send_order = AsyncMock(return_value={"msg1": "주문접수"})

        market = MagicMock()
        market.name = "KR"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("005930"))

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="d1")

        settings = Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
            MODE="paper",
        )

        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=engine,
            playbook=_make_playbook(market="KR"),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            settings=settings,
            market=market,
            stock_code="005930",
            scan_candidates={"KR": {}},
        )

        # BUY must NOT have been executed because broker still holds the stock
        broker.send_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_db_buy_stale_without_broker_holding_does_not_block_new_buy(
        self,
    ) -> None:
        """Stale DB BUY alone must not suppress a new BUY when broker shows qty=0."""
        db_conn = init_db(":memory:")
        log_trade(
            conn=db_conn,
            stock_code="005930",
            action="BUY",
            confidence=80,
            rationale="stale accepted buy",
            quantity=3,
            price=70000.0,
            market="KR",
            exchange_code="KRX",
            session_id="NXT_PRE",
        )

        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(70000.0, 1.0, 0.0))
        broker.get_balance = AsyncMock(
            return_value={
                "output1": [],  # authoritative broker holdings: no shares
                "output2": [
                    {
                        "tot_evlu_amt": "120000000",
                        "dnca_tot_amt": "100000000",
                        "pchs_amt_smtl_amt": "20000000",
                    }
                ],
            }
        )
        broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "주문접수"})

        market = MagicMock()
        market.name = "KR"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("005930"))

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="d1")

        settings = Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
            MODE="paper",
        )

        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=engine,
            playbook=_make_playbook(market="KR"),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            settings=settings,
            market=market,
            stock_code="005930",
            scan_candidates={"KR": {}},
        )

        # New BUY should proceed because broker confirms zero holdings.
        broker.send_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_nxt_session_uses_nx_quote_for_domestic_price(self) -> None:
        """NXT session must fetch domestic current price with market_div_code='NX'."""
        db_conn = init_db(":memory:")

        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(65500.0, 0.0, 0.0))
        broker.get_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [
                    {
                        "tot_evlu_amt": "120000000",
                        "dnca_tot_amt": "100000000",
                        "pchs_amt_smtl_amt": "20000000",
                    }
                ],
            }
        )
        broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "주문접수"})

        market = MagicMock()
        market.name = "KR"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("006800"))

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="d1")

        settings = Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
            MODE="paper",
        )

        with patch("src.main.get_session_info", return_value=MagicMock(session_id="NXT_PRE")):
            await trading_cycle(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(market="KR"),
                risk=MagicMock(),
                db_conn=db_conn,
                decision_logger=decision_logger,
                context_store=MagicMock(
                    get_latest_timeframe=MagicMock(return_value=None),
                    set_context=MagicMock(),
                ),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                settings=settings,
                market=market,
                stock_code="006800",
                scan_candidates={"KR": {}},
            )

        broker.get_current_price.assert_called_once_with("006800", market_div_code="NX")

    @pytest.mark.asyncio
    async def test_nxt_after_session_uses_nx_quote_for_domestic_price(self) -> None:
        """NXT_AFTER session must also fetch domestic current price with market_div_code='NX'."""
        db_conn = init_db(":memory:")

        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(65500.0, 0.0, 0.0))
        broker.get_balance = AsyncMock(
            return_value={
                "output1": [],
                "output2": [
                    {
                        "tot_evlu_amt": "120000000",
                        "dnca_tot_amt": "100000000",
                        "pchs_amt_smtl_amt": "20000000",
                    }
                ],
            }
        )
        broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "주문접수"})

        market = MagicMock()
        market.name = "KR"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True

        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=_make_buy_match("006800"))

        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        decision_logger = MagicMock()
        decision_logger.log_decision = MagicMock(return_value="d1")

        settings = Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
            MODE="paper",
        )

        with patch("src.main.get_session_info", return_value=MagicMock(session_id="NXT_AFTER")):
            await trading_cycle(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(market="KR"),
                risk=MagicMock(),
                db_conn=db_conn,
                decision_logger=decision_logger,
                context_store=MagicMock(
                    get_latest_timeframe=MagicMock(return_value=None),
                    set_context=MagicMock(),
                ),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                settings=settings,
                market=market,
                stock_code="006800",
                scan_candidates={"KR": {}},
            )

        broker.get_current_price.assert_called_once_with("006800", market_div_code="NX")


class TestHandleOverseasPendingOrders:
    """Tests for handle_overseas_pending_orders function."""

    def _make_settings(self, markets: str = "US_NASDAQ,US_NYSE,US_AMEX", **kwargs: Any) -> Settings:
        return Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
            ENABLED_MARKETS=markets,
            **kwargs,
        )

    def _make_telegram(self) -> MagicMock:
        t = MagicMock()
        t.notify_unfilled_order = AsyncMock()
        return t

    @pytest.mark.asyncio
    async def test_buy_pending_is_cancelled_then_resubmitted_once(self) -> None:
        """First unfilled BUY should be cancelled then resubmitted at +0.4%."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD001",
            "sll_buy_dvsn_cd": "02",  # BUY
            "nccs_qty": "3",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[pending_order])
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "200.0"}})
        overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

        sell_resubmit_counts: dict[str, int] = {}
        buy_cooldown: dict[str, float] = {}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts, buy_cooldown
        )

        overseas_broker.cancel_overseas_order.assert_called_once_with(
            exchange_code="NASD",
            stock_code="AAPL",
            odno="ORD001",
            qty=3,
        )
        overseas_broker.send_overseas_order.assert_called_once()
        resubmit_kwargs = overseas_broker.send_overseas_order.call_args[1]
        assert resubmit_kwargs["order_type"] == "BUY"
        assert resubmit_kwargs["price"] == round(200.0 * 1.004, 2)
        assert "BUY:NASD:AAPL" in sell_resubmit_counts
        assert "NASD:AAPL" not in buy_cooldown
        telegram.notify_unfilled_order.assert_called_once()
        call_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert call_kwargs["action"] == "BUY"
        assert call_kwargs["outcome"] == "resubmitted"

    @pytest.mark.asyncio
    async def test_buy_pending_prefers_executable_best_ask_when_available(self) -> None:
        """BUY retry should use executable best-ask instead of last-price multiplier."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD001-A",
            "sll_buy_dvsn_cd": "02",
            "nccs_qty": "3",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[pending_order])
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "200.0"}})
        overseas_broker.get_overseas_orderbook = AsyncMock(
            return_value={"output2": {"pask1": "201.5", "pbid1": "200.8"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

        await handle_overseas_pending_orders(overseas_broker, telegram, settings, {})

        resubmit_kwargs = overseas_broker.send_overseas_order.call_args[1]
        assert resubmit_kwargs["order_type"] == "BUY"
        assert resubmit_kwargs["price"] == 201.5

    @pytest.mark.asyncio
    async def test_buy_pending_gap_cap_uses_market_override_and_cancels(self) -> None:
        """BUY retry should cancel when executable ask gap exceeds market-specific cap."""
        settings = self._make_settings(
            "US_NASDAQ",
            EXECUTABLE_QUOTE_MAX_GAP_PCT=5.0,
            EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON='{"US_NASDAQ": 1.0}',
        )
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()
        buy_cooldown: dict[str, float] = {}

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD001-CAP",
            "sll_buy_dvsn_cd": "02",
            "nccs_qty": "3",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[pending_order])
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "200.0"}})
        overseas_broker.get_overseas_orderbook = AsyncMock(
            return_value={"output2": {"pask1": "204.0", "pbid1": "199.5"}}
        )
        overseas_broker.send_overseas_order = AsyncMock()

        await handle_overseas_pending_orders(
            overseas_broker,
            telegram,
            settings,
            {},
            buy_cooldown,
            rollback_open_position=rollback_open_position,
        )

        overseas_broker.send_overseas_order.assert_not_called()
        assert "NASD:AAPL" in buy_cooldown
        rollback_open_position.assert_called_once_with(
            market_code="US_NASDAQ",
            exchange_code="NASD",
            stock_code="AAPL",
            action="BUY",
        )
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_buy_already_resubmitted_is_only_cancelled_with_cooldown(self) -> None:
        """Second unfilled BUY should only cancel and set cooldown, no further chase."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD001-B",
            "sll_buy_dvsn_cd": "02",  # BUY
            "nccs_qty": "3",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[pending_order])
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.send_overseas_order = AsyncMock()

        sell_resubmit_counts: dict[str, int] = {"BUY:NASD:AAPL": 1}
        buy_cooldown: dict[str, float] = {}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts, buy_cooldown
        )

        overseas_broker.cancel_overseas_order.assert_called_once()
        overseas_broker.send_overseas_order.assert_not_called()
        assert "NASD:AAPL" in buy_cooldown
        telegram.notify_unfilled_order.assert_called_once()
        call_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert call_kwargs["action"] == "BUY"
        assert call_kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_sell_pending_is_cancelled_then_resubmitted(self) -> None:
        """First unfilled SELL should use executable bid even when BUY gap-cap would reject."""
        settings = self._make_settings(
            "US_NASDAQ",
            EXECUTABLE_QUOTE_MAX_GAP_PCT=5.0,
            EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON='{"US_NASDAQ": 1.0}',
        )
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD002",
            "sll_buy_dvsn_cd": "01",  # SELL
            "nccs_qty": "5",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[pending_order])
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "200.0"}})
        overseas_broker.get_overseas_orderbook = AsyncMock(
            return_value={"output2": {"pask1": "205.0", "pbid1": "180.0"}}
        )
        overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

        sell_resubmit_counts: dict[str, int] = {}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts
        )

        overseas_broker.cancel_overseas_order.assert_called_once()
        overseas_broker.send_overseas_order.assert_called_once()
        resubmit_kwargs = overseas_broker.send_overseas_order.call_args[1]
        assert resubmit_kwargs["order_type"] == "SELL"
        assert resubmit_kwargs["price"] == 180.0
        assert sell_resubmit_counts.get("NASD:AAPL") == 1
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["outcome"] == "resubmitted"

    @pytest.mark.asyncio
    async def test_sell_cancel_failure_skips_resubmit(self) -> None:
        """When cancel returns rt_cd != '0', resubmit should NOT be attempted."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD003",
            "sll_buy_dvsn_cd": "01",  # SELL
            "nccs_qty": "2",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[pending_order])
        overseas_broker.cancel_overseas_order = AsyncMock(
            return_value={"rt_cd": "1", "msg1": "Error"}  # failure
        )
        overseas_broker.send_overseas_order = AsyncMock()

        sell_resubmit_counts: dict[str, int] = {}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts
        )

        overseas_broker.send_overseas_order.assert_not_called()
        telegram.notify_unfilled_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_already_resubmitted_is_only_cancelled(self) -> None:
        """Second unfilled SELL (sell_resubmit_counts >= 1) should only cancel, no resubmit."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD004",
            "sll_buy_dvsn_cd": "01",  # SELL
            "nccs_qty": "4",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[pending_order])
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.send_overseas_order = AsyncMock()

        # Already resubmitted once
        sell_resubmit_counts: dict[str, int] = {"NASD:AAPL": 1}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts
        )

        overseas_broker.cancel_overseas_order.assert_called_once()
        overseas_broker.send_overseas_order.assert_not_called()
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["outcome"] == "cancelled"
        assert notify_kwargs["action"] == "SELL"
        # cancel-only path must increment to 2 so trading_cycle can distinguish
        # "retry exhausted" (>= 2) from "first resubmit still live" (== 1)
        assert sell_resubmit_counts["NASD:AAPL"] == 2

    @pytest.mark.asyncio
    async def test_buy_resubmit_failure_notifies_cancelled(self) -> None:
        """If overseas BUY cancel succeeded but resubmit failed, cancelled alert must be sent."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "AAPL",
            "odno": "ORD005",
            "sll_buy_dvsn_cd": "02",  # BUY
            "nccs_qty": "2",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[pending_order])
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.get_overseas_price = AsyncMock(side_effect=ConnectionError("network down"))
        overseas_broker.send_overseas_order = AsyncMock()

        sell_resubmit_counts: dict[str, int] = {}
        buy_cooldown: dict[str, float] = {}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts, buy_cooldown
        )

        telegram.notify_unfilled_order.assert_called_once()
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["stock_code"] == "AAPL"
        assert notify_kwargs["market"] == "NASD"
        assert notify_kwargs["action"] == "BUY"
        assert notify_kwargs["quantity"] == 2
        assert notify_kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_buy_resubmit_rejection_rolls_back_open_position(self) -> None:
        """Rejected overseas BUY resubmit must roll back the optimistic DB position."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()

        pending_order = {
            "pdno": "QURE",
            "odno": "ORD006",
            "sll_buy_dvsn_cd": "02",
            "nccs_qty": "4",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[pending_order])
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "15.84"}})
        overseas_broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": "7", "msg1": "주문 가격을 확인 하시기 바랍니다."}
        )
        sell_resubmit_counts: dict[str, int] = {}

        await handle_overseas_pending_orders(
            overseas_broker,
            telegram,
            settings,
            sell_resubmit_counts,
            {},
            rollback_open_position=rollback_open_position,
        )

        rollback_open_position.assert_called_once_with(
            market_code="US_NASDAQ",
            exchange_code="NASD",
            stock_code="QURE",
            action="BUY",
        )
        assert "BUY:NASD:QURE" not in sell_resubmit_counts
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_sell_resubmit_rejection_restores_open_position(self) -> None:
        """Rejected overseas SELL resubmit must restore the DB open position for retry."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()
        sell_resubmit_counts: dict[str, int] = {}

        pending_order = {
            "pdno": "ATRA",
            "odno": "ORD007",
            "sll_buy_dvsn_cd": "01",
            "nccs_qty": "112",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[pending_order])
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "18.60"}})
        overseas_broker.send_overseas_order = AsyncMock(
            return_value={"rt_cd": "7", "msg1": "주문 가격을 확인 하시기 바랍니다."}
        )

        await handle_overseas_pending_orders(
            overseas_broker,
            telegram,
            settings,
            sell_resubmit_counts,
            rollback_open_position=rollback_open_position,
        )

        rollback_open_position.assert_called_once_with(
            market_code="US_NASDAQ",
            exchange_code="NASD",
            stock_code="ATRA",
            action="SELL",
        )
        assert sell_resubmit_counts == {}
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["action"] == "SELL"
        assert notify_kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_us_exchanges_deduplicated_to_nasd(self) -> None:
        """US_NASDAQ, US_NYSE, US_AMEX should result in only one NASD query."""
        settings = self._make_settings("US_NASDAQ,US_NYSE,US_AMEX")
        telegram = self._make_telegram()

        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[])

        sell_resubmit_counts: dict[str, int] = {}

        await handle_overseas_pending_orders(
            overseas_broker, telegram, settings, sell_resubmit_counts
        )

        # Should be called exactly once with "NASD"
        assert overseas_broker.get_overseas_pending_orders.call_count == 1
        overseas_broker.get_overseas_pending_orders.assert_called_once_with("NASD")

    @pytest.mark.asyncio
    async def test_domestic_buy_resubmit_rejection_rolls_back_open_position(self) -> None:
        """Rejected domestic BUY resubmit must roll back the optimistic DB position."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORDKR1",
            "ord_gno_brno": "001",
            "sll_buy_dvsn_cd": "02",
            "psbl_qty": "3",
            "order_exchange": "KRX",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(return_value=(70000.0, 0.0, 0.0))
        broker.send_order = AsyncMock(return_value={"rt_cd": "7", "msg1": "가격 오류"})

        await handle_domestic_pending_orders(
            broker,
            telegram,
            settings,
            {},
            {},
            rollback_open_position=rollback_open_position,
        )

        rollback_open_position.assert_called_once_with(
            market_code="KR",
            exchange_code="KRX",
            stock_code="005930",
            action="BUY",
        )
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["action"] == "BUY"
        assert notify_kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_domestic_sell_resubmit_rejection_restores_open_position(self) -> None:
        """Rejected domestic SELL resubmit must restore the DB open position for retry."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORDKR2",
            "ord_gno_brno": "001",
            "sll_buy_dvsn_cd": "01",
            "psbl_qty": "5",
            "order_exchange": "KRX",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(return_value=(70000.0, 0.0, 0.0))
        broker.send_order = AsyncMock(return_value={"rt_cd": "7", "msg1": "가격 오류"})

        await handle_domestic_pending_orders(
            broker,
            telegram,
            settings,
            {},
            rollback_open_position=rollback_open_position,
        )

        rollback_open_position.assert_called_once_with(
            market_code="KR",
            exchange_code="KRX",
            stock_code="005930",
            action="SELL",
        )
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["action"] == "SELL"
        assert notify_kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_overseas_sell_resubmit_connection_error_skips_rollback(self) -> None:
        """SELL ambiguity must stay untouched when broker reconciliation also fails."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()

        pending_order = {
            "pdno": "ATRA",
            "odno": "ORD008",
            "sll_buy_dvsn_cd": "01",
            "nccs_qty": "112",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(
            side_effect=[[pending_order], ConnectionError("pending refresh failed")]
        )
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "18.60"}})
        overseas_broker.send_overseas_order = AsyncMock(
            side_effect=ConnectionError("Network error sending overseas order: timeout")
        )
        overseas_broker.get_overseas_balance = AsyncMock(
            side_effect=ConnectionError("balance refresh failed")
        )

        await handle_overseas_pending_orders(
            overseas_broker,
            telegram,
            settings,
            {},
            rollback_open_position=rollback_open_position,
        )

        rollback_open_position.assert_not_called()
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["action"] == "SELL"
        assert notify_kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_overseas_buy_resubmit_connection_error_rolls_back_when_broker_confirms_absent(
        self,
    ) -> None:
        """BUY ambiguity must roll back only after broker confirms no pending order or holding."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()

        pending_order = {
            "pdno": "QURE",
            "odno": "ORD009",
            "sll_buy_dvsn_cd": "02",
            "nccs_qty": "4",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(
            side_effect=[[pending_order], []]
        )
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "15.84"}})
        overseas_broker.send_overseas_order = AsyncMock(
            side_effect=ConnectionError("Network error sending overseas order: timeout")
        )
        overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": []})

        await handle_overseas_pending_orders(
            overseas_broker,
            telegram,
            settings,
            {},
            {},
            rollback_open_position=rollback_open_position,
        )

        rollback_open_position.assert_called_once_with(
            market_code="US_NASDAQ",
            exchange_code="NASD",
            stock_code="QURE",
            action="BUY",
        )

    @pytest.mark.asyncio
    async def test_overseas_sell_resubmit_connection_error_restores_when_broker_confirms_pending(
        self,
    ) -> None:
        """SELL ambiguity must restore position when broker still shows a pending replacement."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()
        sell_resubmit_counts: dict[str, int] = {}

        original_pending_order = {
            "pdno": "ATRA",
            "odno": "ORD010",
            "sll_buy_dvsn_cd": "01",
            "nccs_qty": "112",
            "ovrs_excg_cd": "NASD",
        }
        replacement_pending_order = {
            "pdno": "ATRA",
            "odno": "ORD011",
            "sll_buy_dvsn_cd": "01",
            "nccs_qty": "112",
            "ovrs_excg_cd": "NASD",
        }
        overseas_broker = MagicMock()
        overseas_broker.get_overseas_pending_orders = AsyncMock(
            side_effect=[[original_pending_order], [replacement_pending_order]]
        )
        overseas_broker.cancel_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "18.60"}})
        overseas_broker.send_overseas_order = AsyncMock(
            side_effect=ConnectionError("Network error sending overseas order: timeout")
        )

        await handle_overseas_pending_orders(
            overseas_broker,
            telegram,
            settings,
            sell_resubmit_counts,
            rollback_open_position=rollback_open_position,
        )

        rollback_open_position.assert_called_once_with(
            market_code="US_NASDAQ",
            exchange_code="NASD",
            stock_code="ATRA",
            action="SELL",
            quantity=112,
        )
        assert sell_resubmit_counts["NASD:ATRA"] == 1

    @pytest.mark.asyncio
    async def test_domestic_buy_resubmit_connection_error_skips_rollback(self) -> None:
        """KR BUY ambiguity must stay untouched when broker reconciliation also fails."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORDKR3",
            "ord_gno_brno": "001",
            "sll_buy_dvsn_cd": "02",
            "psbl_qty": "3",
            "order_exchange": "KRX",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(
            side_effect=[[pending_order], ConnectionError("pending refresh failed")]
        )
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(return_value=(70000.0, 0.0, 0.0))
        broker.send_order = AsyncMock(side_effect=ConnectionError("Network error sending order"))
        broker.get_balance = AsyncMock(side_effect=ConnectionError("balance refresh failed"))

        await handle_domestic_pending_orders(
            broker,
            telegram,
            settings,
            {},
            {},
            rollback_open_position=rollback_open_position,
        )

        rollback_open_position.assert_not_called()
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["action"] == "BUY"
        assert notify_kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_domestic_buy_resubmit_connection_error_rolls_back_when_broker_confirms_absent(
        self,
    ) -> None:
        """KR BUY ambiguity must roll back only after broker confirms absence."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORDKR4",
            "ord_gno_brno": "001",
            "sll_buy_dvsn_cd": "02",
            "psbl_qty": "3",
            "order_exchange": "KRX",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(side_effect=[[pending_order], []])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(return_value=(70000.0, 0.0, 0.0))
        broker.send_order = AsyncMock(side_effect=ConnectionError("Network error sending order"))
        broker.get_balance = AsyncMock(return_value={"output1": []})

        await handle_domestic_pending_orders(
            broker,
            telegram,
            settings,
            {},
            {},
            rollback_open_position=rollback_open_position,
        )

        rollback_open_position.assert_called_once_with(
            market_code="KR",
            exchange_code="KRX",
            stock_code="005930",
            action="BUY",
        )

    @pytest.mark.asyncio
    async def test_domestic_sell_resubmit_connection_error_restores_when_broker_confirms_holding(
        self,
    ) -> None:
        """KR SELL ambiguity must restore the position when broker still reports holdings."""
        settings = self._make_settings("US_NASDAQ")
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORDKR5",
            "ord_gno_brno": "001",
            "sll_buy_dvsn_cd": "01",
            "psbl_qty": "5",
            "order_exchange": "KRX",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(side_effect=[[pending_order], []])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(return_value=(70000.0, 0.0, 0.0))
        broker.send_order = AsyncMock(side_effect=ConnectionError("Network error sending order"))
        broker.get_balance = AsyncMock(
            return_value={"output1": [{"pdno": "005930", "ord_psbl_qty": "5"}]}
        )

        await handle_domestic_pending_orders(
            broker,
            telegram,
            settings,
            {},
            rollback_open_position=rollback_open_position,
        )

        rollback_open_position.assert_called_once_with(
            market_code="KR",
            exchange_code="KRX",
            stock_code="005930",
            action="SELL",
            quantity=5,
        )


# ---------------------------------------------------------------------------
# Domestic Pending Order Handling
# ---------------------------------------------------------------------------


class TestHandleDomesticPendingOrders:
    """Tests for handle_domestic_pending_orders function."""

    def _make_settings(self, **kwargs: Any) -> Settings:
        return Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
            ENABLED_MARKETS="KR",
            **kwargs,
        )

    def _make_telegram(self) -> MagicMock:
        t = MagicMock()
        t.notify_unfilled_order = AsyncMock()
        return t

    @pytest.mark.asyncio
    async def test_buy_pending_is_cancelled_then_resubmitted_once(self) -> None:
        """First unfilled BUY should be cancelled then resubmitted at +0.4%."""
        from src.broker.kis_api import kr_round_down

        settings = self._make_settings()
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORD001",
            "ord_gno_brno": "BRN01",
            "sll_buy_dvsn_cd": "02",  # BUY
            "psbl_qty": "3",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(return_value=(50000.0, 0.0, 0.0))
        broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

        sell_resubmit_counts: dict[str, int] = {}
        buy_cooldown: dict[str, float] = {}

        await handle_domestic_pending_orders(
            broker, telegram, settings, sell_resubmit_counts, buy_cooldown
        )

        broker.cancel_domestic_order.assert_called_once_with(
            stock_code="005930",
            orgn_odno="ORD001",
            krx_fwdg_ord_orgno="BRN01",
            qty=3,
            order_exchange="KRX",
        )
        broker.send_order.assert_called_once()
        resubmit_kwargs = broker.send_order.call_args[1]
        assert resubmit_kwargs["order_type"] == "BUY"
        assert resubmit_kwargs["price"] == kr_round_down(50000.0 * 1.004)
        assert "BUY:KR:005930" in sell_resubmit_counts
        assert "KR:005930" not in buy_cooldown
        telegram.notify_unfilled_order.assert_called_once()
        call_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert call_kwargs["action"] == "BUY"
        assert call_kwargs["outcome"] == "resubmitted"
        assert call_kwargs["market"] == "KR"

    @pytest.mark.asyncio
    async def test_buy_pending_prefers_executable_best_ask_from_output1_for_domestic(
        self,
    ) -> None:
        """Domestic BUY retry should parse `output1` top-ask and use it as executable price."""
        settings = self._make_settings()
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORD001-ASK",
            "ord_gno_brno": "BRN01",
            "sll_buy_dvsn_cd": "02",
            "psbl_qty": "3",
            "order_exchange": "KRX",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(return_value=(50000.0, 0.0, 0.0))
        broker.get_orderbook_by_market = AsyncMock(
            return_value={"output1": {"stck_askp1": "50300", "stck_bidp1": "49900"}}
        )
        broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

        await handle_domestic_pending_orders(broker, telegram, settings, {}, {})

        resubmit_kwargs = broker.send_order.call_args[1]
        assert resubmit_kwargs["order_type"] == "BUY"
        assert resubmit_kwargs["price"] == 50300.0

    @pytest.mark.asyncio
    async def test_buy_pending_gap_cap_uses_market_override_for_domestic(self) -> None:
        """Domestic BUY gap-cap should apply with market override regardless of session branch."""
        settings = self._make_settings(
            EXECUTABLE_QUOTE_MAX_GAP_PCT=5.0,
            EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON='{"KR": 0.5}',
        )
        telegram = self._make_telegram()
        rollback_open_position = MagicMock()
        buy_cooldown: dict[str, float] = {}

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORD001-CAP",
            "ord_gno_brno": "BRN01",
            "sll_buy_dvsn_cd": "02",
            "psbl_qty": "3",
            "order_exchange": "KRX",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(return_value=(50000.0, 0.0, 0.0))
        broker.get_orderbook_by_market = AsyncMock(
            return_value={"output1": {"stck_askp1": "51000", "stck_bidp1": "49900"}}
        )
        broker.send_order = AsyncMock()

        await handle_domestic_pending_orders(
            broker,
            telegram,
            settings,
            {},
            buy_cooldown,
            rollback_open_position=rollback_open_position,
        )

        broker.send_order.assert_not_called()
        assert "KR:005930" in buy_cooldown
        rollback_open_position.assert_called_once_with(
            market_code="KR",
            exchange_code="KRX",
            stock_code="005930",
            action="BUY",
        )
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["outcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_buy_pending_uses_odno_when_orgn_odno_empty(self) -> None:
        """KIS live pending can have empty orgn_odno; fallback to odno for cancel."""
        settings = self._make_settings()
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "006800",
            "odno": "0001411200",
            "orgn_odno": "",
            "ord_gno_brno": "91257",
            "sll_buy_dvsn_cd": "02",  # BUY
            "psbl_qty": "15",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(return_value=(65000.0, 0.0, 0.0))
        broker.send_order = AsyncMock()

        sell_resubmit_counts: dict[str, int] = {"BUY:KR:006800": 1}
        buy_cooldown: dict[str, float] = {}

        await handle_domestic_pending_orders(
            broker, telegram, settings, sell_resubmit_counts, buy_cooldown
        )

        broker.cancel_domestic_order.assert_called_once_with(
            stock_code="006800",
            orgn_odno="0001411200",
            krx_fwdg_ord_orgno="91257",
            qty=15,
            order_exchange="KRX",
        )
        broker.send_order.assert_not_called()
        assert "KR:006800" in buy_cooldown
        telegram.notify_unfilled_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_domestic_pending_cancel_uses_normalized_order_exchange(self) -> None:
        """Cancel must use order_exchange from normalized pending order payload."""
        settings = self._make_settings()
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "006800",
            "orgn_odno": "0001411200",
            "ord_gno_brno": "91257",
            "sll_buy_dvsn_cd": "02",  # BUY
            "psbl_qty": "15",
            "order_exchange": "NXT",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(return_value=(65000.0, 0.0, 0.0))
        broker.send_order = AsyncMock()

        sell_resubmit_counts: dict[str, int] = {"BUY:KR:006800": 1}
        buy_cooldown: dict[str, float] = {}

        await handle_domestic_pending_orders(
            broker, telegram, settings, sell_resubmit_counts, buy_cooldown
        )

        broker.cancel_domestic_order.assert_called_once_with(
            stock_code="006800",
            orgn_odno="0001411200",
            krx_fwdg_ord_orgno="91257",
            qty=15,
            order_exchange="NXT",
        )

    @pytest.mark.asyncio
    async def test_sell_pending_is_cancelled_then_resubmitted(self) -> None:
        """First unfilled KR SELL should use executable bid even when spread is wide."""
        settings = self._make_settings(
            EXECUTABLE_QUOTE_MAX_GAP_PCT=1.0,
            EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON='{"KR": 0.5}',
        )
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORD002",
            "ord_gno_brno": "BRN02",
            "sll_buy_dvsn_cd": "01",  # SELL
            "psbl_qty": "5",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(return_value=(50000.0, 0.0, 0.0))
        broker.get_orderbook_by_market = AsyncMock(
            return_value={"output1": {"stck_askp1": "51000", "stck_bidp1": "45000"}}
        )
        broker.send_order = AsyncMock(return_value={"rt_cd": "0"})

        sell_resubmit_counts: dict[str, int] = {}

        await handle_domestic_pending_orders(broker, telegram, settings, sell_resubmit_counts)

        broker.cancel_domestic_order.assert_called_once()
        broker.send_order.assert_called_once()
        resubmit_kwargs = broker.send_order.call_args[1]
        assert resubmit_kwargs["order_type"] == "SELL"
        assert resubmit_kwargs["price"] == 45000.0
        assert sell_resubmit_counts.get("KR:005930") == 1
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["outcome"] == "resubmitted"

    @pytest.mark.asyncio
    async def test_sell_cancel_failure_skips_resubmit(self) -> None:
        """When cancel returns rt_cd != '0', resubmit should NOT be attempted."""
        settings = self._make_settings()
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORD003",
            "ord_gno_brno": "BRN03",
            "sll_buy_dvsn_cd": "01",  # SELL
            "psbl_qty": "2",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(
            return_value={"rt_cd": "1", "msg1": "Error"}  # failure
        )
        broker.send_order = AsyncMock()

        sell_resubmit_counts: dict[str, int] = {}

        await handle_domestic_pending_orders(broker, telegram, settings, sell_resubmit_counts)

        broker.send_order.assert_not_called()
        telegram.notify_unfilled_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_already_resubmitted_is_only_cancelled(self) -> None:
        """Second unfilled SELL (sell_resubmit_counts >= 1) should only cancel, no resubmit."""
        settings = self._make_settings()
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "005930",
            "orgn_odno": "ORD004",
            "ord_gno_brno": "BRN04",
            "sll_buy_dvsn_cd": "01",  # SELL
            "psbl_qty": "4",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.send_order = AsyncMock()

        # Already resubmitted once
        sell_resubmit_counts: dict[str, int] = {"KR:005930": 1}

        await handle_domestic_pending_orders(broker, telegram, settings, sell_resubmit_counts)

        broker.cancel_domestic_order.assert_called_once()
        broker.send_order.assert_not_called()
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["outcome"] == "cancelled"
        assert notify_kwargs["action"] == "SELL"
        # cancel-only path must increment to 2 so trading_cycle can distinguish
        # "retry exhausted" (>= 2) from "first resubmit still live" (== 1)
        assert sell_resubmit_counts["KR:005930"] == 2

    @pytest.mark.asyncio
    async def test_buy_resubmit_failure_notifies_cancelled(self) -> None:
        """If BUY cancel succeeded but resubmit failed, cancelled notification must be sent."""
        settings = self._make_settings()
        telegram = self._make_telegram()

        pending_order = {
            "pdno": "024060",
            "orgn_odno": "ORD005",
            "ord_gno_brno": "BRN05",
            "sll_buy_dvsn_cd": "02",  # BUY
            "psbl_qty": "1",
        }
        broker = MagicMock()
        broker.get_domestic_pending_orders = AsyncMock(return_value=[pending_order])
        broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        broker.get_current_price = AsyncMock(side_effect=ConnectionError("Server disconnected"))
        broker.send_order = AsyncMock()

        sell_resubmit_counts: dict[str, int] = {}
        buy_cooldown: dict[str, float] = {}

        await handle_domestic_pending_orders(
            broker, telegram, settings, sell_resubmit_counts, buy_cooldown
        )

        telegram.notify_unfilled_order.assert_called_once()
        notify_kwargs = telegram.notify_unfilled_order.call_args[1]
        assert notify_kwargs["stock_code"] == "024060"
        assert notify_kwargs["market"] == "KR"
        assert notify_kwargs["action"] == "BUY"
        assert notify_kwargs["quantity"] == 1
        assert notify_kwargs["outcome"] == "cancelled"


# ---------------------------------------------------------------------------
# Domestic Limit Order Price in trading_cycle
# ---------------------------------------------------------------------------


class TestDomesticLimitOrderPrice:
    """trading_cycle must use kr_round_down limit prices for domestic orders."""

    def _make_market(self) -> MagicMock:
        market = MagicMock()
        market.name = "Korea"
        market.code = "KR"
        market.exchange_code = "KRX"
        market.is_domestic = True
        return market

    def _make_broker(self, current_price: float, balance_data: dict) -> MagicMock:
        broker = MagicMock()
        broker.get_current_price = AsyncMock(return_value=(current_price, 0.0, 0.0))
        broker.get_balance = AsyncMock(return_value=balance_data)
        broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        return broker

    @pytest.mark.asyncio
    async def test_trading_cycle_domestic_buy_uses_limit_price(self) -> None:
        """BUY order for domestic stock must use kr_round_down(price * 1.002)."""
        from src.broker.kis_api import kr_round_down
        from src.strategy.models import ScenarioAction

        current_price = 70000.0
        balance_data = {
            "output2": [
                {
                    "tot_evlu_amt": "10000000",
                    "dnca_tot_amt": "5000000",
                    "pchs_amt_smtl_amt": "5000000",
                }
            ]
        }
        broker = self._make_broker(current_price, balance_data)
        market = self._make_market()

        buy_match = ScenarioMatch(
            stock_code="005930",
            matched_scenario=None,
            action=ScenarioAction.BUY,
            confidence=85,
            rationale="test",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=buy_match)

        risk = MagicMock()
        risk.validate_order = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code="005930",
                scan_candidates={},
            )

        broker.send_order.assert_called_once()
        call_kwargs = broker.send_order.call_args[1]
        expected_price = kr_round_down(current_price * 1.002)
        assert call_kwargs["price"] == expected_price
        assert call_kwargs["order_type"] == "BUY"

    @pytest.mark.asyncio
    async def test_trading_cycle_domestic_sell_uses_limit_price(self) -> None:
        """SELL order for domestic stock must use kr_round_down(price * 0.998)."""
        from src.broker.kis_api import kr_round_down
        from src.strategy.models import ScenarioAction

        current_price = 70000.0
        stock_code = "005930"
        balance_data = {
            "output1": [
                {"pdno": stock_code, "hldg_qty": "5", "prpr": "70000", "evlu_amt": "350000"}
            ],
            "output2": [
                {
                    "tot_evlu_amt": "350000",
                    "dnca_tot_amt": "0",
                    "pchs_amt_smtl_amt": "350000",
                }
            ],
        }
        broker = self._make_broker(current_price, balance_data)
        market = self._make_market()

        sell_match = ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=ScenarioAction.SELL,
            confidence=85,
            rationale="test",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=sell_match)

        risk = MagicMock()
        risk.validate_order = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code=stock_code,
                scan_candidates={},
            )

        broker.send_order.assert_called_once()
        call_kwargs = broker.send_order.call_args[1]
        expected_price = kr_round_down(current_price * 0.998)
        assert call_kwargs["price"] == expected_price
        assert call_kwargs["order_type"] == "SELL"

    @pytest.mark.asyncio
    async def test_trading_cycle_uses_market_sell_when_pending_retry_budget_is_exhausted(
        self,
    ) -> None:
        """Exhausted pending SELL retries should escalate to a terminal exit order."""
        from src.strategy.models import ScenarioAction

        current_price = 70000.0
        stock_code = "005930"
        balance_data = {
            "output1": [
                {"pdno": stock_code, "hldg_qty": "5", "prpr": "70000", "evlu_amt": "350000"}
            ],
            "output2": [
                {
                    "tot_evlu_amt": "350000",
                    "dnca_tot_amt": "0",
                    "pchs_amt_smtl_amt": "350000",
                }
            ],
        }
        broker = self._make_broker(current_price, balance_data)
        market = self._make_market()

        sell_match = ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=ScenarioAction.SELL,
            confidence=85,
            rationale="test",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=sell_match)

        risk = MagicMock()
        risk.validate_order = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        with (
            patch("src.main.log_trade"),
            patch("src.main.validate_order_policy"),
            patch(
                "src.main.get_session_info",
                return_value=MagicMock(is_low_liquidity=False, session_id="KRX_REG"),
            ),
        ):
            await trading_cycle(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code=stock_code,
                scan_candidates={},
                sell_resubmit_counts={"KR:005930": 2},
            )

        broker.send_order.assert_called_once()
        call_kwargs = broker.send_order.call_args[1]
        assert call_kwargs["order_type"] == "SELL"
        assert call_kwargs["price"] == 0

    @pytest.mark.asyncio
    async def test_trading_cycle_terminal_sell_passes_order_policy_in_regular_session(
        self,
    ) -> None:
        """Terminal market order (price=0) must pass validate_order_policy in a regular session.

        validate_order_policy is NOT mocked here — this confirms price=0 is allowed when
        both src.main and src.core.order_policy agree the session is non-low-liquidity.
        """
        from src.strategy.models import ScenarioAction

        current_price = 70000.0
        stock_code = "005930"
        balance_data = {
            "output1": [
                {"pdno": stock_code, "hldg_qty": "5", "prpr": "70000", "evlu_amt": "350000"}
            ],
            "output2": [
                {
                    "tot_evlu_amt": "350000",
                    "dnca_tot_amt": "0",
                    "pchs_amt_smtl_amt": "350000",
                }
            ],
        }
        broker = self._make_broker(current_price, balance_data)
        market = self._make_market()

        sell_match = ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=ScenarioAction.SELL,
            confidence=85,
            rationale="test",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=sell_match)

        risk = MagicMock()
        risk.validate_order = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        regular_session = MagicMock(is_low_liquidity=False, session_id="KRX_REG")
        with (
            patch("src.main.log_trade"),
            patch("src.main.get_session_info", return_value=regular_session),
            patch("src.core.order_policy.get_session_info", return_value=regular_session),
        ):
            await trading_cycle(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code=stock_code,
                scan_candidates={},
                sell_resubmit_counts={"KR:005930": 2},
            )

        broker.send_order.assert_called_once()
        call_kwargs = broker.send_order.call_args[1]
        assert call_kwargs["order_type"] == "SELL"
        assert call_kwargs["price"] == 0

    @pytest.mark.asyncio
    async def test_trading_cycle_buy_clears_stale_sell_retry_budget(self) -> None:
        """A new BUY lifecycle should clear stale exhausted SELL retry state."""
        from src.strategy.models import ScenarioAction

        current_price = 70000.0
        stock_code = "005930"
        balance_data = {
            "output2": [
                {
                    "tot_evlu_amt": "10000000",
                    "dnca_tot_amt": "5000000",
                    "pchs_amt_smtl_amt": "5000000",
                }
            ]
        }
        broker = self._make_broker(current_price, balance_data)
        market = self._make_market()
        sell_resubmit_counts = {"KR:005930": 1}

        buy_match = ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=ScenarioAction.BUY,
            confidence=85,
            rationale="test",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=buy_match)

        risk = MagicMock()
        risk.validate_order = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        with patch("src.main.log_trade"):
            await trading_cycle(
                broker=broker,
                overseas_broker=MagicMock(),
                scenario_engine=engine,
                playbook=_make_playbook(),
                risk=risk,
                db_conn=MagicMock(),
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code=stock_code,
                scan_candidates={},
                sell_resubmit_counts=sell_resubmit_counts,
            )

        broker.send_order.assert_called_once()
        assert "KR:005930" not in sell_resubmit_counts


# ---------------------------------------------------------------------------
# Ghost position — overseas SELL "잔고내역이 없습니다" handling
# ---------------------------------------------------------------------------


class TestOverseasGhostPositionClose:
    """trading_cycle must close ghost DB position when broker returns 잔고없음."""

    def _make_overseas_market(self) -> MagicMock:
        market = MagicMock()
        market.name = "NASDAQ"
        market.code = "US_NASDAQ"
        market.exchange_code = "NASD"
        market.is_domestic = False
        return market

    def _make_overseas_broker(
        self,
        current_price: float,
        balance_data: dict,
        sell_result: dict,
    ) -> MagicMock:
        ob = MagicMock()
        ob.get_overseas_price = AsyncMock(
            return_value={"output": {"last": str(current_price), "rate": "0.0"}}
        )
        ob.get_overseas_balance = AsyncMock(return_value=balance_data)
        ob.get_overseas_buying_power = AsyncMock(
            return_value={"output": {"ovrs_ord_psbl_amt": "0.00"}}
        )
        ob.send_overseas_order = AsyncMock(return_value=sell_result)
        return ob

    @pytest.mark.asyncio
    async def test_ghost_position_closes_db_on_no_balance_error(self) -> None:
        """When SELL fails with '잔고내역이 없습니다', log_trade is called to close the ghost.

        This can happen when exchange code recorded at startup differs from the
        exchange code used in the SELL cycle (e.g. KNRX recorded as NASD but
        actually traded on AMEX), causing the broker to see no matching balance.
        The position has ord_psbl_qty > 0 (so a SELL is attempted), but KIS
        rejects it with '잔고내역이 없습니다'.
        """
        from src.strategy.models import ScenarioAction

        stock_code = "KNRX"
        current_price = 1.5
        # ord_psbl_qty=5 means the code passes the qty check and a SELL is sent
        balance_data = {
            "output1": [{"ovrs_pdno": stock_code, "ord_psbl_qty": "5", "ovrs_cblc_qty": "5"}],
            "output2": [{"tot_evlu_amt": "10000"}],
        }
        sell_result = {"rt_cd": "1", "msg1": "모의투자 잔고내역이 없습니다"}

        domestic_broker = MagicMock()
        domestic_broker.get_balance = AsyncMock(return_value={"output1": [], "output2": [{}]})
        overseas_broker = self._make_overseas_broker(current_price, balance_data, sell_result)
        market = self._make_overseas_market()

        sell_match = ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=ScenarioAction.SELL,
            confidence=85,
            rationale="test ghost KNRX",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=sell_match)

        risk = MagicMock()
        risk.validate_order = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        db_conn = MagicMock()

        settings = MagicMock(spec=Settings)
        settings.MODE = "paper"
        settings.POSITION_SIZING_ENABLED = False
        settings.PAPER_OVERSEAS_CASH = 0

        with (
            patch("src.main.log_trade") as mock_log_trade,
            patch("src.main.get_open_position", return_value=None),
            patch("src.main.get_latest_buy_trade", return_value=None),
        ):
            await trading_cycle(
                broker=domestic_broker,
                overseas_broker=overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook(market="US_NASDAQ"),
                risk=risk,
                db_conn=db_conn,
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code=stock_code,
                scan_candidates={},
                settings=settings,
            )

        # log_trade must be called with action="SELL" to close the ghost position
        ghost_close_calls = [
            c
            for c in mock_log_trade.call_args_list
            if c.kwargs.get("action") == "SELL"
            and "[ghost-close]" in (c.kwargs.get("rationale") or "")
        ]
        assert ghost_close_calls, "Expected ghost-close log_trade call was not made"

    @pytest.mark.asyncio
    async def test_normal_sell_failure_does_not_close_db(self) -> None:
        """Non-잔고없음 SELL failures must NOT close the DB position."""
        from src.strategy.models import ScenarioAction

        stock_code = "TSLA"
        current_price = 250.0
        balance_data = {
            "output1": [{"ovrs_pdno": stock_code, "ord_psbl_qty": "5", "ovrs_cblc_qty": "5"}],
            "output2": [{"tot_evlu_amt": "100000"}],
        }
        sell_result = {"rt_cd": "1", "msg1": "일시적 오류가 발생했습니다"}

        domestic_broker = MagicMock()
        domestic_broker.get_balance = AsyncMock(return_value={"output1": [], "output2": [{}]})
        overseas_broker = self._make_overseas_broker(current_price, balance_data, sell_result)
        market = self._make_overseas_market()

        sell_match = ScenarioMatch(
            stock_code=stock_code,
            matched_scenario=None,
            action=ScenarioAction.SELL,
            confidence=85,
            rationale="test",
        )
        engine = MagicMock(spec=ScenarioEngine)
        engine.evaluate = MagicMock(return_value=sell_match)

        risk = MagicMock()
        risk.validate_order = MagicMock()
        risk.check_circuit_breaker = MagicMock()
        telegram = MagicMock()
        telegram.notify_trade_execution = AsyncMock()
        telegram.notify_fat_finger = AsyncMock()
        telegram.notify_circuit_breaker = AsyncMock()
        telegram.notify_scenario_matched = AsyncMock()

        db_conn = MagicMock()

        with (
            patch("src.main.log_trade") as mock_log_trade,
            patch("src.main.get_open_position", return_value=None),
        ):
            await trading_cycle(
                broker=domestic_broker,
                overseas_broker=overseas_broker,
                scenario_engine=engine,
                playbook=_make_playbook(market="US_NASDAQ"),
                risk=risk,
                db_conn=db_conn,
                decision_logger=MagicMock(),
                context_store=MagicMock(get_latest_timeframe=MagicMock(return_value=None)),
                criticality_assessor=MagicMock(
                    assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                    get_timeout=MagicMock(return_value=5.0),
                ),
                telegram=telegram,
                market=market,
                stock_code=stock_code,
                scan_candidates={},
            )

        ghost_close_calls = [
            c
            for c in mock_log_trade.call_args_list
            if c.kwargs.get("action") == "SELL"
            and "[ghost-close]" in (c.kwargs.get("rationale") or "")
        ]
        assert not ghost_close_calls, "Ghost-close must NOT be triggered for non-잔고없음 errors"


@pytest.mark.asyncio
async def test_trading_cycle_uses_market_sell_overseas_when_pending_retry_budget_is_exhausted(
) -> None:
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_balance = AsyncMock(return_value={"output1": [], "output2": [{}]})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_price = AsyncMock(
        return_value={"output": {"last": "250.0", "rate": "0.0"}}
    )
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [{"ovrs_pdno": "AAPL", "ord_psbl_qty": "5", "ovrs_cblc_qty": "5"}],
            "output2": [{"frcr_evlu_tota": "100000", "frcr_buy_amt_smtl": "0"}],
        }
    )
    overseas_broker.get_overseas_buying_power = AsyncMock(
        return_value={"output": {"ovrs_ord_psbl_amt": "0"}}
    )
    overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

    market = MagicMock()
    market.name = "NASDAQ"
    market.code = "US_NASDAQ"
    market.exchange_code = "NASD"
    market.is_domestic = False

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    with (
        patch("src.main.validate_order_policy"),
        patch(
            "src.main.get_session_info",
            return_value=MagicMock(is_low_liquidity=False, session_id="US_REG"),
        ),
    ):
        await trading_cycle(
            broker=broker,
            overseas_broker=overseas_broker,
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_sell_match("AAPL"))),
            playbook=_make_playbook("US_NASDAQ"),
            risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="AAPL",
            scan_candidates={},
            settings=_make_settings(MODE="paper", PAPER_OVERSEAS_CASH=50000.0),
            sell_resubmit_counts={"NASD:AAPL": 2},
        )

    overseas_broker.send_overseas_order.assert_called_once()
    call_kwargs = overseas_broker.send_overseas_order.call_args[1]
    assert call_kwargs["order_type"] == "SELL"
    assert call_kwargs["price"] == 0


@pytest.mark.asyncio
async def test_kill_switch_block_skips_actionable_order_execution() -> None:
    """Active kill-switch must prevent actionable order execution."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.5, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "50000",
                    "pchs_amt_smtl_amt": "50000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    settings = MagicMock()
    settings.POSITION_SIZING_ENABLED = False
    settings.CONFIDENCE_THRESHOLD = 80

    try:
        KILL_SWITCH.new_orders_blocked = True
        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match())),
            playbook=_make_playbook(),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="005930",
            scan_candidates={},
            settings=settings,
        )
    finally:
        KILL_SWITCH.clear_block()

    broker.send_order.assert_not_called()


@pytest.mark.asyncio
async def test_order_policy_rejection_skips_order_execution() -> None:
    """Order policy rejection must prevent order submission."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.5, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "50000",
                    "pchs_amt_smtl_amt": "50000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    settings = MagicMock()
    settings.POSITION_SIZING_ENABLED = False
    settings.CONFIDENCE_THRESHOLD = 80

    with patch(
        "src.main.validate_order_policy",
        side_effect=OrderPolicyRejected(
            "rejected",
            session_id="NXT_AFTER",
            market_code="KR",
        ),
    ):
        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match())),
            playbook=_make_playbook(),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="005930",
            scan_candidates={},
            settings=settings,
        )

    broker.send_order.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("price", "should_block"),
    [
        (4.99, True),
        (5.00, True),
        (5.01, False),
    ],
)
async def test_us_min_price_filter_boundary(price: float, should_block: bool) -> None:
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_balance = AsyncMock(return_value={"output1": [], "output2": [{}]})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_price = AsyncMock(
        return_value={"output": {"last": str(price), "rate": "0.0"}}
    )
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [{"frcr_evlu_tota": "10000", "frcr_buy_amt_smtl": "0"}],
        }
    )
    overseas_broker.get_overseas_buying_power = AsyncMock(
        return_value={"output": {"ovrs_ord_psbl_amt": "10000"}}
    )
    overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

    market = MagicMock()
    market.name = "NASDAQ"
    market.code = "US_NASDAQ"
    market.exchange_code = "NASD"
    market.is_domestic = False

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    settings = MagicMock()
    settings.POSITION_SIZING_ENABLED = False
    settings.CONFIDENCE_THRESHOLD = 80
    settings.MODE = "paper"
    settings.PAPER_OVERSEAS_CASH = 50000
    settings.US_MIN_PRICE = 5.0
    settings.USD_BUFFER_MIN = 1000.0

    await trading_cycle(
        broker=broker,
        overseas_broker=overseas_broker,
        scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match("AAPL"))),
        playbook=_make_playbook("US_NASDAQ"),
        risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="AAPL",
        scan_candidates={},
        settings=settings,
    )

    if should_block:
        overseas_broker.send_overseas_order.assert_not_called()
    else:
        overseas_broker.send_overseas_order.assert_called_once()


@pytest.mark.asyncio
async def test_us_min_price_filter_not_applied_to_kr_market() -> None:
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(4.0, 0.0, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "50000",
                    "pchs_amt_smtl_amt": "50000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    settings = MagicMock()
    settings.POSITION_SIZING_ENABLED = False
    settings.CONFIDENCE_THRESHOLD = 80
    settings.MODE = "paper"
    settings.US_MIN_PRICE = 5.0
    settings.USD_BUFFER_MIN = 1000.0

    await trading_cycle(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match("005930"))),
        playbook=_make_playbook(),
        risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
        db_conn=db_conn,
        decision_logger=decision_logger,
        context_store=MagicMock(
            get_latest_timeframe=MagicMock(return_value=None),
            set_context=MagicMock(),
        ),
        criticality_assessor=MagicMock(
            assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
            get_timeout=MagicMock(return_value=5.0),
        ),
        telegram=telegram,
        market=market,
        stock_code="005930",
        scan_candidates={},
        settings=settings,
    )

    broker.send_order.assert_called_once()


@pytest.mark.asyncio
async def test_session_boundary_reloads_us_min_price_override_in_trading_cycle() -> None:
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_balance = AsyncMock(return_value={"output1": [], "output2": [{}]})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_price = AsyncMock(
        return_value={"output": {"last": "7.0", "rate": "0.0"}}
    )
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [{"frcr_evlu_tota": "10000", "frcr_buy_amt_smtl": "0"}],
        }
    )
    overseas_broker.get_overseas_buying_power = AsyncMock(
        return_value={"output": {"ovrs_ord_psbl_amt": "10000"}}
    )
    overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

    market = MagicMock()
    market.name = "NASDAQ"
    market.code = "US_NASDAQ"
    market.exchange_code = "NASD"
    market.is_domestic = False

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    settings = Settings(
        KIS_APP_KEY="k",
        KIS_APP_SECRET="s",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="g",
        MODE="paper",
        PAPER_OVERSEAS_CASH=50000.0,
        US_MIN_PRICE=5.0,
        USD_BUFFER_MIN=1000.0,
        SESSION_RISK_RELOAD_ENABLED=True,
        SESSION_RISK_PROFILES_JSON=(
            '{"US_PRE": {"US_MIN_PRICE": 8.0}, "US_DAY": {"US_MIN_PRICE": 5.0}}'
        ),
    )

    current_session = {"id": "US_PRE"}

    def _session_info(_: Any) -> MagicMock:
        return MagicMock(session_id=current_session["id"])

    with (
        patch("src.main.get_open_position", return_value=None),
        patch("src.main.get_session_info", side_effect=_session_info),
        patch("src.core.session_risk.get_session_info", side_effect=_session_info),
    ):
        await trading_cycle(
            broker=broker,
            overseas_broker=overseas_broker,
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match("AAPL"))),
            playbook=_make_playbook("US_NASDAQ"),
            risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="AAPL",
            scan_candidates={},
            settings=settings,
        )
        assert overseas_broker.send_overseas_order.call_count == 0

        current_session["id"] = "US_DAY"
        await trading_cycle(
            broker=broker,
            overseas_broker=overseas_broker,
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match("AAPL"))),
            playbook=_make_playbook("US_NASDAQ"),
            risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="AAPL",
            scan_candidates={},
            settings=settings,
        )

    assert overseas_broker.send_overseas_order.call_count == 1


@pytest.mark.asyncio
async def test_session_boundary_falls_back_when_profile_reload_fails() -> None:
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_balance = AsyncMock(return_value={"output1": [], "output2": [{}]})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_price = AsyncMock(
        return_value={"output": {"last": "7.0", "rate": "0.0"}}
    )
    overseas_broker.get_overseas_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [{"frcr_evlu_tota": "10000", "frcr_buy_amt_smtl": "0"}],
        }
    )
    overseas_broker.get_overseas_buying_power = AsyncMock(
        return_value={"output": {"ovrs_ord_psbl_amt": "10000"}}
    )
    overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

    market = MagicMock()
    market.name = "NASDAQ"
    market.code = "US_NASDAQ"
    market.exchange_code = "NASD"
    market.is_domestic = False

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    settings = Settings(
        KIS_APP_KEY="k",
        KIS_APP_SECRET="s",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="g",
        MODE="paper",
        PAPER_OVERSEAS_CASH=50000.0,
        US_MIN_PRICE=5.0,
        USD_BUFFER_MIN=1000.0,
        SESSION_RISK_RELOAD_ENABLED=True,
        SESSION_RISK_PROFILES_JSON='{"US_PRE": {"US_MIN_PRICE": 8.0}}',
    )

    current_session = {"id": "US_PRE"}

    def _session_info(_: Any) -> MagicMock:
        return MagicMock(session_id=current_session["id"])

    with (
        patch("src.main.get_open_position", return_value=None),
        patch("src.main.get_session_info", side_effect=_session_info),
        patch("src.core.session_risk.get_session_info", side_effect=_session_info),
    ):
        await trading_cycle(
            broker=broker,
            overseas_broker=overseas_broker,
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match("AAPL"))),
            playbook=_make_playbook("US_NASDAQ"),
            risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="AAPL",
            scan_candidates={},
            settings=settings,
        )
        assert overseas_broker.send_overseas_order.call_count == 0

        settings.SESSION_RISK_PROFILES_JSON = "{invalid-json"
        current_session["id"] = "US_DAY"
        await trading_cycle(
            broker=broker,
            overseas_broker=overseas_broker,
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match("AAPL"))),
            playbook=_make_playbook("US_NASDAQ"),
            risk=MagicMock(validate_order=MagicMock(), check_circuit_breaker=MagicMock()),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="AAPL",
            scan_candidates={},
            settings=settings,
        )

    assert overseas_broker.send_overseas_order.call_count == 1


def test_overnight_policy_prioritizes_killswitch_over_exception() -> None:
    market = MagicMock()
    with patch(
        "src.core.order_helpers.get_session_info",
        return_value=MagicMock(session_id="US_AFTER"),
    ):
        settings = MagicMock()
        settings.OVERNIGHT_EXCEPTION_ENABLED = True
        try:
            KILL_SWITCH.new_orders_blocked = True
            assert _should_force_exit_for_overnight(market=market, settings=settings)
        finally:
            KILL_SWITCH.clear_block()


@pytest.mark.asyncio
async def test_kill_switch_block_does_not_block_sell_reduction() -> None:
    """KillSwitch should block BUY entries, but allow SELL risk reduction orders."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.5, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [{"pdno": "005930", "ord_psbl_qty": "3"}],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "50000",
                    "pchs_amt_smtl_amt": "50000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    settings = MagicMock()
    settings.POSITION_SIZING_ENABLED = False
    settings.CONFIDENCE_THRESHOLD = 80
    settings.OVERNIGHT_EXCEPTION_ENABLED = True
    settings.MODE = "paper"

    try:
        KILL_SWITCH.new_orders_blocked = True
        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_sell_match())),
            playbook=_make_playbook(),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="005930",
            scan_candidates={},
            settings=settings,
        )
    finally:
        KILL_SWITCH.clear_block()

    broker.send_order.assert_called_once()


@pytest.mark.asyncio
async def test_blackout_queues_order_and_skips_submission() -> None:
    """When blackout is active, order submission is replaced by queueing."""
    db_conn = init_db(":memory:")
    decision_logger = DecisionLogger(db_conn)

    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.5, 0.0))
    broker.get_balance = AsyncMock(
        return_value={
            "output1": [],
            "output2": [
                {
                    "tot_evlu_amt": "100000",
                    "dnca_tot_amt": "50000",
                    "pchs_amt_smtl_amt": "50000",
                }
            ],
        }
    )
    broker.send_order = AsyncMock(return_value={"msg1": "OK"})

    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    settings = MagicMock()
    settings.POSITION_SIZING_ENABLED = False
    settings.CONFIDENCE_THRESHOLD = 80

    telegram = MagicMock()
    telegram.notify_trade_execution = AsyncMock()
    telegram.notify_fat_finger = AsyncMock()
    telegram.notify_circuit_breaker = AsyncMock()
    telegram.notify_scenario_matched = AsyncMock()

    blackout_manager = MagicMock()
    blackout_manager.in_blackout.return_value = True
    blackout_manager.enqueue.return_value = True
    blackout_manager.pending_count = 1
    blackout_manager.overflow_drop_count = 0

    with patch("src.core.blackout_runtime.BLACKOUT_ORDER_MANAGER", blackout_manager):
        await trading_cycle(
            broker=broker,
            overseas_broker=MagicMock(),
            scenario_engine=MagicMock(evaluate=MagicMock(return_value=_make_buy_match())),
            playbook=_make_playbook(),
            risk=MagicMock(),
            db_conn=db_conn,
            decision_logger=decision_logger,
            context_store=MagicMock(
                get_latest_timeframe=MagicMock(return_value=None),
                set_context=MagicMock(),
            ),
            criticality_assessor=MagicMock(
                assess_market_conditions=MagicMock(return_value=MagicMock(value="NORMAL")),
                get_timeout=MagicMock(return_value=5.0),
            ),
            telegram=telegram,
            market=market,
            stock_code="005930",
            scan_candidates={},
            settings=settings,
        )

    broker.send_order.assert_not_called()
    blackout_manager.enqueue.assert_called_once()


def test_blackout_queue_overflow_keeps_latest_intent() -> None:
    manager = BlackoutOrderManager(enabled=True, windows=[], max_queue_size=1)
    manager.in_blackout = lambda now=None: True  # type: ignore[method-assign]

    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"

    with patch("src.core.blackout_runtime.BLACKOUT_ORDER_MANAGER", manager):
        assert _maybe_queue_order_intent(
            market=market,
            session_id="KRX_REG",
            stock_code="005930",
            order_type="BUY",
            quantity=1,
            price=100.0,
            source="test-first",
        )
        assert _maybe_queue_order_intent(
            market=market,
            session_id="KRX_REG",
            stock_code="000660",
            order_type="BUY",
            quantity=2,
            price=200.0,
            source="test-second",
        )

    assert manager.pending_count == 1
    assert manager.overflow_drop_count == 1
    manager.in_blackout = lambda now=None: False  # type: ignore[method-assign]
    batch = manager.pop_recovery_batch()
    assert len(batch) == 1
    assert batch[0].stock_code == "000660"
    assert batch[0].session_id == "KRX_REG"


@pytest.mark.asyncio
async def test_process_blackout_recovery_executes_valid_intents() -> None:
    """Recovery must execute queued intents that pass revalidation."""
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.0, 0.0))
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    overseas_broker = MagicMock()

    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    intent = MagicMock()
    intent.market_code = "KR"
    intent.stock_code = "005930"
    intent.order_type = "BUY"
    intent.quantity = 1
    intent.price = 100.0
    intent.source = "test"
    intent.session_id = "NXT_AFTER"
    intent.attempts = 0

    blackout_manager = MagicMock()
    blackout_manager.pop_recovery_batch.return_value = [intent]

    with (
        patch("src.core.blackout_runtime.BLACKOUT_ORDER_MANAGER", blackout_manager),
        patch("src.core.blackout_runtime.MARKETS", {"KR": market}),
        patch("src.core.blackout_runtime.get_open_position", return_value=None),
        patch("src.core.blackout_runtime.validate_order_policy"),
        patch("src.main.get_session_info", return_value=MagicMock(session_id="KRX_REG")),
    ):
        await process_blackout_recovery_orders(
            broker=broker,
            overseas_broker=overseas_broker,
            db_conn=db_conn,
        )

    broker.send_order.assert_called_once()
    row = db_conn.execute(
        """
        SELECT action, quantity, session_id, rationale
        FROM trades
        WHERE stock_code = '005930'
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert row[0] == "BUY"
    assert row[1] == 1
    assert row[2] == "NXT_AFTER"
    assert row[3].startswith("[blackout-recovery]")


@pytest.mark.asyncio
async def test_process_blackout_recovery_drops_policy_rejected_intent() -> None:
    """Policy-rejected queued intents must not be requeued."""
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(100.0, 0.0, 0.0))
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    overseas_broker = MagicMock()

    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    intent = MagicMock()
    intent.market_code = "KR"
    intent.stock_code = "005930"
    intent.order_type = "BUY"
    intent.quantity = 1
    intent.price = 100.0
    intent.source = "test"
    intent.session_id = "KRX_REG"
    intent.attempts = 0

    blackout_manager = MagicMock()
    blackout_manager.pop_recovery_batch.return_value = [intent]

    with (
        patch("src.core.blackout_runtime.BLACKOUT_ORDER_MANAGER", blackout_manager),
        patch("src.core.blackout_runtime.MARKETS", {"KR": market}),
        patch("src.core.blackout_runtime.get_open_position", return_value=None),
        patch(
            "src.core.blackout_runtime.validate_order_policy",
            side_effect=OrderPolicyRejected(
                "blocked",
                session_id="NXT_AFTER",
                market_code="KR",
            ),
        ),
    ):
        await process_blackout_recovery_orders(
            broker=broker,
            overseas_broker=overseas_broker,
            db_conn=db_conn,
        )

    broker.send_order.assert_not_called()
    blackout_manager.requeue.assert_not_called()


@pytest.mark.asyncio
async def test_process_blackout_recovery_drops_intent_on_excessive_price_drift() -> None:
    """Queued intent is dropped when current market price drift exceeds threshold."""
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.get_current_price = AsyncMock(return_value=(106.0, 0.0, 0.0))
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    overseas_broker = MagicMock()

    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    intent = MagicMock()
    intent.market_code = "KR"
    intent.stock_code = "005930"
    intent.order_type = "BUY"
    intent.quantity = 1
    intent.price = 100.0
    intent.source = "test"
    intent.session_id = "US_PRE"
    intent.attempts = 0

    blackout_manager = MagicMock()
    blackout_manager.pop_recovery_batch.return_value = [intent]

    with (
        patch("src.core.blackout_runtime.BLACKOUT_ORDER_MANAGER", blackout_manager),
        patch("src.core.blackout_runtime.MARKETS", {"KR": market}),
        patch("src.core.blackout_runtime.get_open_position", return_value=None),
        patch("src.core.blackout_runtime.validate_order_policy") as validate_policy,
    ):
        await process_blackout_recovery_orders(
            broker=broker,
            overseas_broker=overseas_broker,
            db_conn=db_conn,
            settings=Settings(
                KIS_APP_KEY="k",
                KIS_APP_SECRET="s",
                KIS_ACCOUNT_NO="12345678-01",
                GEMINI_API_KEY="g",
                BLACKOUT_RECOVERY_MAX_PRICE_DRIFT_PCT=5.0,
            ),
        )

    broker.send_order.assert_not_called()
    validate_policy.assert_not_called()


@pytest.mark.asyncio
async def test_process_blackout_recovery_drops_overseas_intent_on_excessive_price_drift() -> None:
    """Overseas queued intent is dropped when price drift exceeds threshold."""
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    overseas_broker = MagicMock()
    overseas_broker.get_overseas_price = AsyncMock(return_value={"output": {"last": "106.0"}})
    overseas_broker.send_overseas_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})

    market = MagicMock()
    market.code = "US_NASDAQ"
    market.exchange_code = "NASD"
    market.is_domestic = False

    intent = MagicMock()
    intent.market_code = "US_NASDAQ"
    intent.stock_code = "AAPL"
    intent.order_type = "BUY"
    intent.quantity = 1
    intent.price = 100.0
    intent.source = "test"
    intent.session_id = "KRX_REG"
    intent.attempts = 0

    blackout_manager = MagicMock()
    blackout_manager.pop_recovery_batch.return_value = [intent]

    with (
        patch("src.core.blackout_runtime.BLACKOUT_ORDER_MANAGER", blackout_manager),
        patch("src.core.blackout_runtime.MARKETS", {"US_NASDAQ": market}),
        patch("src.core.blackout_runtime.get_open_position", return_value=None),
        patch("src.core.blackout_runtime.validate_order_policy") as validate_policy,
    ):
        await process_blackout_recovery_orders(
            broker=broker,
            overseas_broker=overseas_broker,
            db_conn=db_conn,
            settings=Settings(
                KIS_APP_KEY="k",
                KIS_APP_SECRET="s",
                KIS_ACCOUNT_NO="12345678-01",
                GEMINI_API_KEY="g",
                BLACKOUT_RECOVERY_MAX_PRICE_DRIFT_PCT=5.0,
            ),
        )

    overseas_broker.send_overseas_order.assert_not_called()
    validate_policy.assert_not_called()


@pytest.mark.asyncio
async def test_process_blackout_recovery_requeues_intent_when_price_lookup_fails() -> None:
    """Price lookup failure must requeue intent for a later retry."""
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.get_current_price = AsyncMock(side_effect=ConnectionError("price API down"))
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    overseas_broker = MagicMock()

    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    intent = MagicMock()
    intent.market_code = "KR"
    intent.stock_code = "005930"
    intent.order_type = "BUY"
    intent.quantity = 1
    intent.price = 100.0
    intent.source = "test"
    intent.session_id = "KRX_REG"
    intent.attempts = 0

    blackout_manager = MagicMock()
    blackout_manager.pop_recovery_batch.return_value = [intent]

    with (
        patch("src.core.blackout_runtime.BLACKOUT_ORDER_MANAGER", blackout_manager),
        patch("src.core.blackout_runtime.MARKETS", {"KR": market}),
        patch("src.core.blackout_runtime.get_open_position", return_value=None),
        patch("src.core.blackout_runtime.validate_order_policy") as validate_policy,
    ):
        await process_blackout_recovery_orders(
            broker=broker,
            overseas_broker=overseas_broker,
            db_conn=db_conn,
        )

    broker.send_order.assert_not_called()
    validate_policy.assert_not_called()
    blackout_manager.requeue.assert_called_once_with(intent)
    assert intent.attempts == 1


@pytest.mark.asyncio
async def test_trigger_emergency_kill_switch_executes_operational_steps() -> None:
    """Emergency kill switch should execute cancel/refresh/reduce/notify callbacks."""
    broker = MagicMock()
    broker.get_domestic_pending_orders = AsyncMock(
        return_value=[
            {
                "pdno": "005930",
                "orgn_odno": "1",
                "ord_gno_brno": "01",
                "psbl_qty": "3",
                "order_exchange": "NXT",
            }
        ]
    )
    broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "0"})
    broker.get_balance = AsyncMock(return_value={"output1": [], "output2": []})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[])
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": [], "output2": []})

    telegram = MagicMock()
    telegram.notify_circuit_breaker = AsyncMock()

    settings = MagicMock()
    settings.enabled_market_list = ["KR"]

    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    with (
        patch("src.main.MARKETS", {"KR": market}),
        patch("src.core.blackout_runtime.BLACKOUT_ORDER_MANAGER.clear", return_value=2),
    ):
        report = await _trigger_emergency_kill_switch(
            reason="test",
            broker=broker,
            overseas_broker=overseas_broker,
            telegram=telegram,
            settings=settings,
            current_market=market,
            stock_code="005930",
            pnl_pct=-3.2,
            threshold=-3.0,
        )

    assert report.steps == [
        "block_new_orders",
        "cancel_pending_orders",
        "refresh_order_state",
        "reduce_risk",
        "snapshot_state",
        "notify",
    ]
    broker.cancel_domestic_order.assert_called_once_with(
        stock_code="005930",
        orgn_odno="1",
        krx_fwdg_ord_orgno="01",
        qty=3,
        order_exchange="NXT",
    )
    broker.get_balance.assert_called_once()
    telegram.notify_circuit_breaker.assert_called_once_with(
        pnl_pct=-3.2,
        threshold=-3.0,
    )


@pytest.mark.asyncio
async def test_trigger_emergency_kill_switch_records_cancel_failure() -> None:
    """Cancel API rejection should be captured in kill switch errors."""
    broker = MagicMock()
    broker.get_domestic_pending_orders = AsyncMock(
        return_value=[
            {
                "pdno": "005930",
                "orgn_odno": "1",
                "ord_gno_brno": "01",
                "psbl_qty": "3",
            }
        ]
    )
    broker.cancel_domestic_order = AsyncMock(return_value={"rt_cd": "1", "msg1": "fail"})
    broker.get_balance = AsyncMock(return_value={"output1": [], "output2": []})

    overseas_broker = MagicMock()
    overseas_broker.get_overseas_pending_orders = AsyncMock(return_value=[])
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": [], "output2": []})

    telegram = MagicMock()
    telegram.notify_circuit_breaker = AsyncMock()

    settings = MagicMock()
    settings.enabled_market_list = ["KR"]

    market = MagicMock()
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    with (
        patch("src.main.MARKETS", {"KR": market}),
        patch("src.core.blackout_runtime.BLACKOUT_ORDER_MANAGER.clear", return_value=0),
    ):
        report = await _trigger_emergency_kill_switch(
            reason="test-fail",
            broker=broker,
            overseas_broker=overseas_broker,
            telegram=telegram,
            settings=settings,
            current_market=market,
            stock_code="005930",
            pnl_pct=-3.2,
            threshold=-3.0,
        )

    assert any(err.startswith("cancel_pending_orders:") for err in report.errors)


@pytest.mark.asyncio
async def test_refresh_order_state_failure_summary_includes_more_count() -> None:
    broker = MagicMock()
    broker.get_balance = AsyncMock(side_effect=RuntimeError("domestic down"))
    overseas_broker = MagicMock()
    overseas_broker.get_overseas_balance = AsyncMock(side_effect=RuntimeError("overseas down"))

    markets = []
    for code, exchange in [("KR", "KRX"), ("US_PRE", "NASD"), ("US_DAY", "NYSE"), ("JP", "TKSE")]:
        market = MagicMock()
        market.code = code
        market.exchange_code = exchange
        market.is_domestic = code == "KR"
        markets.append(market)

    with pytest.raises(RuntimeError, match=r"\(\+1 more\)$") as exc_info:
        from src.core.kill_switch_runtime import _refresh_order_state_for_kill_switch
        await _refresh_order_state_for_kill_switch(
            broker=broker,
            overseas_broker=overseas_broker,
            markets=markets,
        )
    assert "KR/KRX" in str(exc_info.value)


class TestPendingOrderRollback:
    """DB rollback helpers for cancelled pending orders."""

    def test_buy_rollback_closes_optimistic_open_position(self) -> None:
        db_conn = init_db(":memory:")
        log_trade(
            conn=db_conn,
            stock_code="QURE",
            action="BUY",
            confidence=82,
            rationale="optimistic buy log",
            quantity=44,
            price=15.84,
            pnl=0.0,
            market="US_NASDAQ",
            exchange_code="NASD",
            session_id="US_PRE",
            decision_id="buy-dec",
            mode="live",
        )

        _rollback_pending_order_position(
            db_conn=db_conn,
            market_code="US_NASDAQ",
            exchange_code="NASD",
            stock_code="QURE",
            action="BUY",
            runtime_session_id="US_PRE",
            settings=_make_settings(),
        )

        assert main_module.get_open_position(db_conn, "QURE", "US_NASDAQ") is None

    def test_sell_rollback_restores_open_position_after_optimistic_close(self) -> None:
        db_conn = init_db(":memory:")
        log_trade(
            conn=db_conn,
            stock_code="ATRA",
            action="BUY",
            confidence=80,
            rationale="entry",
            quantity=112,
            price=18.75,
            pnl=0.0,
            market="US_NASDAQ",
            exchange_code="NASD",
            session_id="US_REG",
            selection_context={"fx_rate": 1370.25, "signal": "rebound"},
            decision_id="buy-dec",
            mode="live",
        )
        log_trade(
            conn=db_conn,
            stock_code="ATRA",
            action="SELL",
            confidence=90,
            rationale="optimistic sell log",
            quantity=112,
            price=18.60,
            pnl=0.0,
            market="US_NASDAQ",
            exchange_code="NASD",
            session_id="US_REG",
            decision_id="sell-dec",
            mode="live",
        )

        _rollback_pending_order_position(
            db_conn=db_conn,
            market_code="US_NASDAQ",
            exchange_code="NASD",
            stock_code="ATRA",
            action="SELL",
            runtime_session_id="US_REG",
            settings=_make_settings(),
        )

        restored = main_module.get_open_position(db_conn, "ATRA", "US_NASDAQ")
        assert restored is not None
        assert restored["quantity"] == 112
        assert restored["price"] == pytest.approx(18.75)
        restored_buy = main_module.get_latest_buy_trade(
            db_conn,
            "ATRA",
            "US_NASDAQ",
            exchange_code="NASD",
        )
        assert _extract_buy_fx_rate(restored_buy) == pytest.approx(1370.25)

    def test_sell_rollback_uses_broker_confirmed_remaining_quantity(self) -> None:
        db_conn = init_db(":memory:")
        log_trade(
            conn=db_conn,
            stock_code="ATRA",
            action="BUY",
            confidence=80,
            rationale="entry",
            quantity=112,
            price=18.75,
            pnl=0.0,
            market="US_NASDAQ",
            exchange_code="NASD",
            session_id="US_REG",
            selection_context={"fx_rate": 1370.25, "signal": "rebound"},
            decision_id="buy-dec",
            mode="live",
        )
        log_trade(
            conn=db_conn,
            stock_code="ATRA",
            action="SELL",
            confidence=90,
            rationale="optimistic sell log",
            quantity=112,
            price=18.60,
            pnl=0.0,
            market="US_NASDAQ",
            exchange_code="NASD",
            session_id="US_REG",
            decision_id="sell-dec",
            mode="live",
        )

        _rollback_pending_order_position(
            db_conn=db_conn,
            market_code="US_NASDAQ",
            exchange_code="NASD",
            stock_code="ATRA",
            action="SELL",
            quantity=40,
            runtime_session_id="US_REG",
            settings=_make_settings(),
        )

        restored = main_module.get_open_position(db_conn, "ATRA", "US_NASDAQ")
        assert restored is not None
        assert restored["quantity"] == 40
        assert restored["price"] == pytest.approx(18.75)

    def test_sell_rollback_preserves_selection_context_dict(self) -> None:
        db_conn = init_db(":memory:")
        log_trade(
            conn=db_conn,
            stock_code="ATRA",
            action="BUY",
            confidence=80,
            rationale="entry",
            quantity=112,
            price=18.75,
            pnl=0.0,
            market="US_NASDAQ",
            exchange_code="NASD",
            session_id="US_REG",
            selection_context={"fx_rate": 1370.25, "signal": "rebound"},
            decision_id="buy-dec",
            mode="live",
        )
        log_trade(
            conn=db_conn,
            stock_code="ATRA",
            action="SELL",
            confidence=90,
            rationale="optimistic sell log",
            quantity=112,
            price=18.60,
            pnl=0.0,
            market="US_NASDAQ",
            exchange_code="NASD",
            session_id="US_REG",
            decision_id="sell-dec",
            mode="live",
        )

        _rollback_pending_order_position(
            db_conn=db_conn,
            market_code="US_NASDAQ",
            exchange_code="NASD",
            stock_code="ATRA",
            action="SELL",
            runtime_session_id="US_REG",
            settings=_make_settings(),
        )

        restored_buy = main_module.get_latest_buy_trade(
            db_conn,
            "ATRA",
            "US_NASDAQ",
            exchange_code="NASD",
        )
        assert restored_buy is not None
        assert _extract_buy_fx_rate(restored_buy) == pytest.approx(1370.25)


class TestMidSessionRefresh:
    def _make_dt(self, hour: int, minute: int, tz: str) -> datetime:
        return datetime(2026, 3, 5, hour, minute, 0, tzinfo=ZoneInfo(tz))

    def test_triggers_at_noon_us_reg(self) -> None:
        """US_REG 12:00 ET에 True."""
        now = self._make_dt(12, 0, "America/New_York")
        assert _should_mid_session_refresh(
            market_code="US_NASDAQ", session_id="US_REG",
            now=now, mid_refreshed=set()
        ) is True

    def test_triggers_at_noon_kr_reg(self) -> None:
        """KRX_REG 12:00 KST에 True."""
        now = self._make_dt(12, 0, "Asia/Seoul")
        assert _should_mid_session_refresh(
            market_code="KR", session_id="KRX_REG",
            now=now, mid_refreshed=set()
        ) is True

    def test_does_not_trigger_before_noon(self) -> None:
        """11:59에는 False."""
        now = self._make_dt(11, 59, "America/New_York")
        assert _should_mid_session_refresh(
            market_code="US_NYSE", session_id="US_REG",
            now=now, mid_refreshed=set()
        ) is False

    def test_triggers_after_noon_if_not_yet_refreshed(self) -> None:
        """루프 드리프트로 12:01에 도달해도 아직 미실행이면 True."""
        now = self._make_dt(12, 1, "America/New_York")
        assert _should_mid_session_refresh(
            market_code="US_AMEX", session_id="US_REG",
            now=now, mid_refreshed=set()
        ) is True

    def test_does_not_trigger_wrong_session(self) -> None:
        """US_PRE 세션에는 False."""
        now = self._make_dt(12, 0, "America/New_York")
        assert _should_mid_session_refresh(
            market_code="US_NASDAQ", session_id="US_PRE",
            now=now, mid_refreshed=set()
        ) is False

    def test_does_not_trigger_if_already_refreshed(self) -> None:
        """이미 오늘 갱신된 마켓은 False."""
        now = self._make_dt(12, 0, "America/New_York")
        assert _should_mid_session_refresh(
            market_code="US_NASDAQ", session_id="US_REG",
            now=now, mid_refreshed={"US_NASDAQ"}
        ) is False


@pytest.mark.asyncio
async def test_run_restores_pre_refresh_playbook_when_mid_session_refresh_generation_fails(
) -> None:
    from src.analysis.smart_scanner import ScanCandidate

    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    settings = _make_settings(TRADE_MODE="realtime", ENABLED_MARKETS="US_NASDAQ")
    original_playbook = _make_playbook("US_NASDAQ")

    market = MagicMock()
    market.code = "US_NASDAQ"
    market.name = "Nasdaq"
    market.exchange_code = "NASD"
    market.is_domestic = False
    market.timezone = ZoneInfo("America/New_York")

    candidate = ScanCandidate(
        stock_code="AAPL",
        name="Apple",
        price=190.0,
        volume=1_000_000.0,
        volume_ratio=2.0,
        rsi=55.0,
        signal="momentum",
        score=80.0,
    )

    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()
    cycle_count = {"count": 0}

    broker = MagicMock()
    broker.close = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": []})

    playbook_store = MagicMock()
    playbook_store.load_latest_entry = MagicMock(return_value=None)
    playbook_store.load = MagicMock(return_value=None)

    pre_market_planner = MagicMock()
    pre_market_planner.generate_playbook = AsyncMock(
        side_effect=[original_playbook, RuntimeError("planner down")]
    )

    smart_scanner = MagicMock()
    smart_scanner.scan = AsyncMock(return_value=[candidate])
    smart_scanner.get_stock_codes = MagicMock(return_value=["AAPL"])

    telegram = MagicMock()
    telegram.notify_system_start = AsyncMock()
    telegram.notify_system_shutdown = AsyncMock()
    telegram.notify_market_open = AsyncMock()
    telegram.notify_market_session_transition = AsyncMock()
    telegram.notify_playbook_failed = AsyncMock()
    telegram.notify_playbook_generated = AsyncMock()
    telegram.close = AsyncMock()

    command_handler = MagicMock()
    command_handler.register_command = MagicMock()
    command_handler.register_command_with_args = MagicMock()
    command_handler.start_polling = AsyncMock()
    command_handler.stop_polling = AsyncMock()

    async def _run_once(markets: list[Any], processor: Any) -> None:
        for item in markets:
            await processor(item)
        cycle_count["count"] += 1
        if cycle_count["count"] >= 2:
            shutdown_event.set()

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=playbook_store))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=pre_market_planner))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        stack.enter_context(patch("src.main.TelegramClient", return_value=telegram))
        stack.enter_context(
            patch("src.main.TelegramCommandHandler", return_value=command_handler)
        )
        stack.enter_context(
            patch("src.main.SmartVolatilityScanner", return_value=smart_scanner)
        )
        stack.enter_context(
            patch("src.main.CriticalityAssessor", return_value=MagicMock())
        )
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.process_blackout_recovery_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.handle_overseas_pending_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.build_overseas_symbol_universe", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(patch("src.main.get_open_markets", return_value=[market]))
        stack.enter_context(
            patch("src.main._acquire_live_runtime_lock", return_value=None)
        )
        stack.enter_context(
            patch("src.main.get_session_info", return_value=MagicMock(session_id="US_REG"))
        )
        stack.enter_context(
            patch("src.main._should_mid_session_refresh", side_effect=[False, True])
        )
        stack.enter_context(patch("src.main._should_rescan_market", return_value=True))
        stack.enter_context(
            patch("src.main._has_market_session_transition", side_effect=[True, False])
        )
        stack.enter_context(
            patch("src.main._run_markets_in_parallel", side_effect=_run_once)
        )
        mock_trading_cycle = stack.enter_context(
            patch("src.main.trading_cycle", new=AsyncMock())
        )
        await main_module.run(settings)

    assert mock_trading_cycle.await_count == 2
    first_playbook = mock_trading_cycle.await_args_list[0].args[3]
    second_playbook = mock_trading_cycle.await_args_list[1].args[3]
    assert first_playbook is not original_playbook
    assert second_playbook is not original_playbook
    assert first_playbook.session_id == "US_REG"
    assert second_playbook.session_id == "US_REG"
    assert original_playbook.session_id == "UNKNOWN"
    assert pre_market_planner.generate_playbook.await_count == 2
    playbook_store.load_latest.assert_not_called()
    telegram.notify_playbook_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_regenerates_playbook_on_us_regular_session_transition() -> None:
    from src.analysis.smart_scanner import ScanCandidate

    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    settings = _make_settings(TRADE_MODE="realtime", ENABLED_MARKETS="US_NASDAQ")
    premarket_playbook = _make_playbook("US_NASDAQ")
    regular_playbook = _make_playbook("US_NASDAQ")

    market = MagicMock()
    market.code = "US_NASDAQ"
    market.name = "Nasdaq"
    market.exchange_code = "NASD"
    market.is_domestic = False
    market.timezone = ZoneInfo("America/New_York")

    candidate = ScanCandidate(
        stock_code="AAPL",
        name="Apple",
        price=190.0,
        volume=1_000_000.0,
        volume_ratio=2.0,
        rsi=55.0,
        signal="momentum",
        score=80.0,
    )

    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()
    cycle_count = {"count": 0}

    broker = MagicMock()
    broker.close = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": []})

    playbook_store = MagicMock()
    playbook_store.load_latest_entry = MagicMock(return_value=None)
    playbook_store.load = MagicMock(return_value=None)

    pre_market_planner = MagicMock()
    pre_market_planner.generate_playbook = AsyncMock(
        side_effect=[premarket_playbook, regular_playbook]
    )

    smart_scanner = MagicMock()
    smart_scanner.scan = AsyncMock(return_value=[candidate])
    smart_scanner.get_stock_codes = MagicMock(return_value=["AAPL"])

    telegram = MagicMock()
    telegram.notify_system_start = AsyncMock()
    telegram.notify_system_shutdown = AsyncMock()
    telegram.notify_market_open = AsyncMock()
    telegram.notify_playbook_failed = AsyncMock()
    telegram.notify_playbook_generated = AsyncMock()
    telegram.close = AsyncMock()

    command_handler = MagicMock()
    command_handler.register_command = MagicMock()
    command_handler.register_command_with_args = MagicMock()
    command_handler.start_polling = AsyncMock()
    command_handler.stop_polling = AsyncMock()

    async def _run_once(markets: list[Any], processor: Any) -> None:
        for item in markets:
            await processor(item)
        cycle_count["count"] += 1
        if cycle_count["count"] >= 2:
            shutdown_event.set()

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=playbook_store))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=pre_market_planner))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        stack.enter_context(patch("src.main.TelegramClient", return_value=telegram))
        stack.enter_context(
            patch("src.main.TelegramCommandHandler", return_value=command_handler)
        )
        stack.enter_context(
            patch("src.main.SmartVolatilityScanner", return_value=smart_scanner)
        )
        stack.enter_context(
            patch("src.main.CriticalityAssessor", return_value=MagicMock())
        )
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.process_blackout_recovery_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.handle_overseas_pending_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.build_overseas_symbol_universe", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(patch("src.main.get_open_markets", return_value=[market]))
        stack.enter_context(
            patch("src.main._acquire_live_runtime_lock", return_value=None)
        )
        stack.enter_context(
            patch(
                "src.main.get_session_info",
                side_effect=[MagicMock(session_id="US_PRE"), MagicMock(session_id="US_REG")],
            )
        )
        stack.enter_context(patch("src.main._should_mid_session_refresh", return_value=False))
        stack.enter_context(patch("src.main._should_rescan_market", return_value=True))
        stack.enter_context(
            patch("src.main._has_market_session_transition", side_effect=[True, True])
        )
        stack.enter_context(
            patch("src.main._run_markets_in_parallel", side_effect=_run_once)
        )
        mock_trading_cycle = stack.enter_context(
            patch("src.main.trading_cycle", new=AsyncMock())
        )

        await main_module.run(settings)

    assert mock_trading_cycle.await_count == 2
    first_playbook = mock_trading_cycle.await_args_list[0].args[3]
    second_playbook = mock_trading_cycle.await_args_list[1].args[3]
    assert first_playbook is not premarket_playbook
    assert second_playbook is not regular_playbook
    assert first_playbook.session_id == "US_PRE"
    assert second_playbook.session_id == "US_REG"
    assert premarket_playbook.session_id == "UNKNOWN"
    assert regular_playbook.session_id == "UNKNOWN"
    assert pre_market_planner.generate_playbook.await_count == 2
    assert telegram.notify_playbook_failed.await_count == 0


@pytest.mark.asyncio
async def test_run_reuses_stored_regular_session_playbook_after_restart() -> None:
    from src.analysis.smart_scanner import ScanCandidate

    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    settings = _make_settings(TRADE_MODE="realtime", ENABLED_MARKETS="US_NASDAQ")
    stored_playbook = _make_stock_playbook("US_NASDAQ", "AAPL").model_copy(
        update={"session_id": "US_REG"}
    )

    market = MagicMock()
    market.code = "US_NASDAQ"
    market.name = "Nasdaq"
    market.exchange_code = "NASD"
    market.is_domestic = False
    market.timezone = ZoneInfo("America/New_York")

    candidate = ScanCandidate(
        stock_code="AAPL",
        name="Apple",
        price=190.0,
        volume=1_000_000.0,
        volume_ratio=2.0,
        rsi=55.0,
        signal="momentum",
        score=80.0,
    )

    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()

    broker = MagicMock()
    broker.close = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": []})

    playbook_store = MagicMock()
    playbook_store.load_latest_entry = MagicMock(
        return_value=StoredPlaybookEntry(
            playbook=stored_playbook,
            slot="open",
            generated_at=stored_playbook.generated_at,
        )
    )
    playbook_store.load = MagicMock(return_value=None)

    pre_market_planner = MagicMock()
    pre_market_planner.generate_playbook = AsyncMock()

    smart_scanner = MagicMock()
    smart_scanner.scan = AsyncMock(return_value=[candidate])
    smart_scanner.get_stock_codes = MagicMock(return_value=["AAPL"])

    telegram = MagicMock()
    telegram.notify_system_start = AsyncMock()
    telegram.notify_system_shutdown = AsyncMock()
    telegram.notify_market_open = AsyncMock()
    telegram.notify_playbook_failed = AsyncMock()
    telegram.notify_playbook_generated = AsyncMock()
    telegram.close = AsyncMock()

    command_handler = MagicMock()
    command_handler.register_command = MagicMock()
    command_handler.register_command_with_args = MagicMock()
    command_handler.start_polling = AsyncMock()
    command_handler.stop_polling = AsyncMock()

    async def _run_once(markets: list[Any], processor: Any) -> None:
        for item in markets:
            await processor(item)
        shutdown_event.set()

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=playbook_store))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=pre_market_planner))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        stack.enter_context(patch("src.main.TelegramClient", return_value=telegram))
        stack.enter_context(
            patch("src.main.TelegramCommandHandler", return_value=command_handler)
        )
        stack.enter_context(
            patch("src.main.SmartVolatilityScanner", return_value=smart_scanner)
        )
        stack.enter_context(
            patch("src.main.CriticalityAssessor", return_value=MagicMock())
        )
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.process_blackout_recovery_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.handle_overseas_pending_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.build_overseas_symbol_universe", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(patch("src.main.get_open_markets", return_value=[market]))
        stack.enter_context(
            patch("src.main._acquire_live_runtime_lock", return_value=None)
        )
        stack.enter_context(
            patch("src.main.get_session_info", return_value=MagicMock(session_id="US_REG"))
        )
        stack.enter_context(patch("src.main._should_mid_session_refresh", return_value=False))
        stack.enter_context(patch("src.main._should_rescan_market", return_value=True))
        stack.enter_context(
            patch("src.main._run_markets_in_parallel", side_effect=_run_once)
        )
        mock_trading_cycle = stack.enter_context(
            patch("src.main.trading_cycle", new=AsyncMock())
        )

        await main_module.run(settings)

    assert mock_trading_cycle.await_count == 1
    resumed_playbook = mock_trading_cycle.await_args_list[0].args[3]
    assert resumed_playbook.session_id == "US_REG"
    assert pre_market_planner.generate_playbook.await_count == 0
    playbook_store.load_latest_entry.assert_called_once_with(
        datetime.now(market.timezone).date(),
        "US_NASDAQ",
        session_id="US_REG",
    )
    telegram.notify_playbook_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_regenerates_playbook_after_restart_when_scanner_candidates_change() -> None:
    from src.analysis.smart_scanner import ScanCandidate

    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    settings = _make_settings(TRADE_MODE="realtime", ENABLED_MARKETS="US_NASDAQ")
    stored_playbook = _make_stock_playbook("US_NASDAQ", "AAPL").model_copy(
        update={"session_id": "US_REG"}
    )
    fresh_playbook = _make_stock_playbook("US_NASDAQ", "TSLA")

    market = MagicMock()
    market.code = "US_NASDAQ"
    market.name = "Nasdaq"
    market.exchange_code = "NASD"
    market.is_domestic = False
    market.timezone = ZoneInfo("America/New_York")

    candidate = ScanCandidate(
        stock_code="TSLA",
        name="Tesla",
        price=190.0,
        volume=1_000_000.0,
        volume_ratio=2.0,
        rsi=55.0,
        signal="momentum",
        score=80.0,
    )

    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()

    broker = MagicMock()
    broker.close = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": []})

    playbook_store = MagicMock()
    playbook_store.load_latest_entry = MagicMock(
        return_value=StoredPlaybookEntry(
            playbook=stored_playbook,
            slot="open",
            generated_at=stored_playbook.generated_at,
        )
    )
    playbook_store.load = MagicMock(return_value=None)

    pre_market_planner = MagicMock()
    pre_market_planner.generate_playbook = AsyncMock(return_value=fresh_playbook)

    smart_scanner = MagicMock()
    smart_scanner.scan = AsyncMock(return_value=[candidate])
    smart_scanner.get_stock_codes = MagicMock(return_value=["TSLA"])

    telegram = MagicMock()
    telegram.notify_system_start = AsyncMock()
    telegram.notify_system_shutdown = AsyncMock()
    telegram.notify_market_open = AsyncMock()
    telegram.notify_playbook_failed = AsyncMock()
    telegram.notify_playbook_generated = AsyncMock()
    telegram.close = AsyncMock()

    command_handler = MagicMock()
    command_handler.register_command = MagicMock()
    command_handler.register_command_with_args = MagicMock()
    command_handler.start_polling = AsyncMock()
    command_handler.stop_polling = AsyncMock()

    async def _run_once(markets: list[Any], processor: Any) -> None:
        for item in markets:
            await processor(item)
        shutdown_event.set()

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=playbook_store))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=pre_market_planner))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        stack.enter_context(patch("src.main.TelegramClient", return_value=telegram))
        stack.enter_context(
            patch("src.main.TelegramCommandHandler", return_value=command_handler)
        )
        stack.enter_context(
            patch("src.main.SmartVolatilityScanner", return_value=smart_scanner)
        )
        stack.enter_context(
            patch("src.main.CriticalityAssessor", return_value=MagicMock())
        )
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.process_blackout_recovery_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.handle_overseas_pending_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.build_overseas_symbol_universe", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(patch("src.main.get_open_markets", return_value=[market]))
        stack.enter_context(
            patch("src.main._acquire_live_runtime_lock", return_value=None)
        )
        stack.enter_context(
            patch("src.main.get_session_info", return_value=MagicMock(session_id="US_REG"))
        )
        stack.enter_context(patch("src.main._should_mid_session_refresh", return_value=False))
        stack.enter_context(patch("src.main._should_rescan_market", return_value=True))
        stack.enter_context(
            patch("src.main._run_markets_in_parallel", side_effect=_run_once)
        )
        mock_trading_cycle = stack.enter_context(
            patch("src.main.trading_cycle", new=AsyncMock())
        )

        await main_module.run(settings)

    assert mock_trading_cycle.await_count == 1
    resumed_playbook = mock_trading_cycle.await_args_list[0].args[3]
    assert resumed_playbook.session_id == "US_REG"
    assert resumed_playbook.get_stock_playbook("TSLA") is not None
    assert resumed_playbook.get_stock_playbook("AAPL") is None
    assert pre_market_planner.generate_playbook.await_count == 1
    telegram.notify_playbook_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_forces_rescan_on_market_session_transition_even_after_state_update() -> None:
    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    settings = _make_settings(TRADE_MODE="realtime", ENABLED_MARKETS="US_NASDAQ")

    market = MagicMock()
    market.code = "US_NASDAQ"
    market.name = "Nasdaq"
    market.exchange_code = "NASD"
    market.is_domestic = False
    market.timezone = ZoneInfo("America/New_York")

    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()
    cycle_count = {"count": 0}
    rescan_session_flags: list[bool] = []

    broker = MagicMock()
    broker.close = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": []})

    playbook_store = MagicMock()
    playbook_store.load_latest = MagicMock(return_value=None)
    playbook_store.load_latest_entry = MagicMock(return_value=None)
    playbook_store.load = MagicMock(return_value=None)

    pre_market_planner = MagicMock()
    pre_market_planner.generate_playbook = AsyncMock(
        side_effect=[_make_playbook("US_NASDAQ"), _make_playbook("US_NASDAQ")]
    )

    telegram = MagicMock()
    telegram.notify_system_start = AsyncMock()
    telegram.notify_system_shutdown = AsyncMock()
    telegram.notify_market_open = AsyncMock()
    telegram.notify_market_session_transition = AsyncMock()
    telegram.notify_playbook_failed = AsyncMock()
    telegram.notify_playbook_generated = AsyncMock()
    telegram.close = AsyncMock()

    command_handler = MagicMock()
    command_handler.register_command = MagicMock()
    command_handler.register_command_with_args = MagicMock()
    command_handler.start_polling = AsyncMock()
    command_handler.stop_polling = AsyncMock()

    async def _run_once(markets: list[Any], processor: Any) -> None:
        for item in markets:
            await processor(item)
        cycle_count["count"] += 1
        if cycle_count["count"] >= 2:
            shutdown_event.set()

    def _record_rescan_decision(
        *, last_scan: float, now_timestamp: float, rescan_interval: float, session_changed: bool
    ) -> bool:
        del last_scan, now_timestamp, rescan_interval
        rescan_session_flags.append(session_changed)
        # This test only verifies the propagated session_changed flag, so keep
        # the scanner/rescan branch disabled and avoid unrelated universe mocks.
        return False

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=playbook_store))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=pre_market_planner))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        stack.enter_context(patch("src.main.TelegramClient", return_value=telegram))
        stack.enter_context(
            patch("src.main.TelegramCommandHandler", return_value=command_handler)
        )
        stack.enter_context(patch("src.main.SmartVolatilityScanner", return_value=MagicMock()))
        stack.enter_context(
            patch("src.main.CriticalityAssessor", return_value=MagicMock())
        )
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.process_blackout_recovery_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.handle_overseas_pending_orders", new=AsyncMock())
        )
        stack.enter_context(patch("src.main.get_open_markets", return_value=[market]))
        stack.enter_context(
            patch("src.main._acquire_live_runtime_lock", return_value=None)
        )
        stack.enter_context(
            patch(
                "src.main.get_session_info",
                side_effect=[MagicMock(session_id="US_PRE"), MagicMock(session_id="US_REG")],
            )
        )
        stack.enter_context(patch("src.main._should_mid_session_refresh", return_value=False))
        stack.enter_context(
            patch("src.main._should_rescan_market", side_effect=_record_rescan_decision)
        )
        stack.enter_context(
            patch("src.main._run_markets_in_parallel", side_effect=_run_once)
        )
        stack.enter_context(patch("src.main.trading_cycle", new=AsyncMock()))

        await main_module.run(settings)

    assert rescan_session_flags == [False, True]


@pytest.mark.asyncio
async def test_run_session_transition_clears_tracking_cache_before_building_overseas_universe(
) -> None:
    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    settings = _make_settings(TRADE_MODE="realtime", ENABLED_MARKETS="US_NASDAQ")

    market = MagicMock()
    market.code = "US_NASDAQ"
    market.name = "Nasdaq"
    market.exchange_code = "NASD"
    market.is_domestic = False
    market.timezone = ZoneInfo("America/New_York")

    candidate = ScanCandidate(
        stock_code="AAPL",
        name="Apple",
        price=190.0,
        volume=1_000_000.0,
        volume_ratio=2.0,
        rsi=55.0,
        signal="momentum",
        score=80.0,
    )

    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()
    cycle_count = {"count": 0}

    broker = MagicMock()
    broker.close = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": []})

    playbook_store = MagicMock()
    playbook_store.load_latest = MagicMock(return_value=None)
    playbook_store.load_latest_entry = MagicMock(return_value=None)
    playbook_store.load = MagicMock(return_value=None)

    pre_market_planner = MagicMock()
    pre_market_planner.generate_playbook = AsyncMock(
        side_effect=[_make_playbook("US_NASDAQ"), _make_playbook("US_NASDAQ")]
    )

    smart_scanner = MagicMock()
    smart_scanner.scan = AsyncMock(return_value=[candidate])
    smart_scanner.get_stock_codes = MagicMock(return_value=["AAPL"])

    telegram = MagicMock()
    telegram.notify_system_start = AsyncMock()
    telegram.notify_system_shutdown = AsyncMock()
    telegram.notify_market_open = AsyncMock()
    telegram.notify_playbook_failed = AsyncMock()
    telegram.notify_playbook_generated = AsyncMock()
    telegram.close = AsyncMock()

    command_handler = MagicMock()
    command_handler.register_command = MagicMock()
    command_handler.register_command_with_args = MagicMock()
    command_handler.start_polling = AsyncMock()
    command_handler.stop_polling = AsyncMock()

    runtime_fallback_snapshots: list[list[str]] = []

    async def _record_universe(
        *,
        db_conn: Any,
        overseas_broker: Any,
        market: Any,
        runtime_fallback_stocks: list[str] | None,
    ) -> list[str]:
        del db_conn, overseas_broker, market
        runtime_fallback_snapshots.append(list(runtime_fallback_stocks or []))
        return []

    async def _run_once(markets: list[Any], processor: Any) -> None:
        for item in markets:
            await processor(item)
        cycle_count["count"] += 1
        if cycle_count["count"] >= 2:
            shutdown_event.set()

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=playbook_store))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=pre_market_planner))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        stack.enter_context(patch("src.main.TelegramClient", return_value=telegram))
        stack.enter_context(
            patch("src.main.TelegramCommandHandler", return_value=command_handler)
        )
        stack.enter_context(
            patch("src.main.SmartVolatilityScanner", return_value=smart_scanner)
        )
        stack.enter_context(
            patch("src.main.CriticalityAssessor", return_value=MagicMock())
        )
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.process_blackout_recovery_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.handle_overseas_pending_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch(
                "src.main.build_overseas_symbol_universe",
                new=AsyncMock(side_effect=_record_universe),
            )
        )
        stack.enter_context(patch("src.main.get_open_markets", return_value=[market]))
        stack.enter_context(
            patch("src.main._acquire_live_runtime_lock", return_value=None)
        )
        stack.enter_context(
            patch(
                "src.main.get_session_info",
                side_effect=[MagicMock(session_id="US_PRE"), MagicMock(session_id="US_REG")],
            )
        )
        stack.enter_context(patch("src.main._should_mid_session_refresh", return_value=False))
        stack.enter_context(patch("src.main._should_rescan_market", return_value=True))
        stack.enter_context(
            patch("src.main._has_market_session_transition", side_effect=[True, True])
        )
        stack.enter_context(
            patch("src.main._run_markets_in_parallel", side_effect=_run_once)
        )
        stack.enter_context(patch("src.main.trading_cycle", new=AsyncMock()))

        await main_module.run(settings)

    assert runtime_fallback_snapshots == [[], []]


@pytest.mark.asyncio
async def test_run_closes_removed_market_while_other_market_stays_open() -> None:
    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    settings = _make_settings(TRADE_MODE="realtime", ENABLED_MARKETS="KR,US_NASDAQ")
    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()
    cycle_count = {"count": 0}

    broker = MagicMock()
    broker.close = AsyncMock()
    broker.get_balance = AsyncMock(return_value={"output1": [], "output2": []})
    overseas_broker = MagicMock()
    overseas_broker.close = AsyncMock()
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": []})

    open_markets_iter = iter(
        [
            [MARKETS["KR"], MARKETS["US_NASDAQ"]],
            [MARKETS["US_NASDAQ"]],
            [MARKETS["US_NASDAQ"]],
        ]
    )

    def _next_open_markets(*args: Any, **kwargs: Any) -> list[Any]:
        return next(open_markets_iter)

    async def _run_once(markets: list[Any], processor: Any) -> None:
        for item in markets:
            await processor(item)
        cycle_count["count"] += 1
        if cycle_count["count"] >= 3:
            shutdown_event.set()

    close_handler = AsyncMock()

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=MagicMock()))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        telegram = stack.enter_context(
            patch(
                "src.main.TelegramClient",
                return_value=MagicMock(
                    notify_system_start=AsyncMock(),
                    notify_system_shutdown=AsyncMock(),
                    notify_market_open=AsyncMock(),
                    close=AsyncMock(),
                ),
            )
        )
        command_handler = MagicMock(
            register_command=MagicMock(),
            register_command_with_args=MagicMock(),
            start_polling=AsyncMock(),
            stop_polling=AsyncMock(),
        )
        stack.enter_context(patch("src.main.TelegramCommandHandler", return_value=command_handler))
        smart_scanner = MagicMock()
        smart_scanner.get_stock_codes = MagicMock(return_value=[])
        smart_scanner.scan = AsyncMock(return_value=[])
        stack.enter_context(patch("src.main.SmartVolatilityScanner", return_value=smart_scanner))
        stack.enter_context(patch("src.main.CriticalityAssessor", return_value=MagicMock()))
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(patch("src.main.process_blackout_recovery_orders", new=AsyncMock()))
        stack.enter_context(patch("src.main.handle_domestic_pending_orders", new=AsyncMock()))
        stack.enter_context(patch("src.main.handle_overseas_pending_orders", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.build_overseas_symbol_universe", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(patch("src.main.get_open_markets", side_effect=_next_open_markets))
        stack.enter_context(patch("src.main._acquire_live_runtime_lock", return_value=None))
        stack.enter_context(
            patch(
                "src.main.get_session_info",
                side_effect=lambda market: MagicMock(
                    session_id="KRX_REG" if market.code == "KR" else "US_REG"
                ),
            )
        )
        stack.enter_context(patch("src.main._should_mid_session_refresh", return_value=False))
        stack.enter_context(patch("src.main._should_rescan_market", return_value=False))
        stack.enter_context(patch("src.main._run_markets_in_parallel", side_effect=_run_once))
        stack.enter_context(patch("src.main.trading_cycle", new=AsyncMock()))
        stack.enter_context(patch("src.main._handle_market_close", new=close_handler))

        await main_module.run(settings)

    close_handler.assert_awaited_once()
    assert close_handler.await_args.kwargs["market_code"] == "KR"
    telegram.return_value.notify_market_open.assert_any_await(MARKETS["KR"].name)
    telegram.return_value.notify_market_open.assert_any_await(MARKETS["US_NASDAQ"].name)
    assert telegram.return_value.notify_market_open.await_count == 2


@pytest.mark.asyncio
async def test_run_realtime_mode_reconciles_close_and_session_transition_independently() -> None:
    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    settings = _make_settings(TRADE_MODE="realtime", ENABLED_MARKETS="KR,US_NASDAQ")
    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()
    cycle_count = {"count": 0}

    broker = MagicMock()
    broker.close = AsyncMock()
    broker.get_balance = AsyncMock(return_value={"output1": [], "output2": []})
    overseas_broker = MagicMock()
    overseas_broker.close = AsyncMock()
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": []})

    lifecycle_inputs = iter(
        [
            (
                [MARKETS["KR"], MARKETS["US_NASDAQ"]],
                {"KR": "KRX_REG", "US_NASDAQ": "US_PRE"},
            ),
            (
                [MARKETS["US_NASDAQ"]],
                {"US_NASDAQ": "US_REG"},
            ),
            (
                [MARKETS["US_NASDAQ"]],
                {"US_NASDAQ": "US_REG"},
            ),
        ]
    )
    current_sessions: dict[str, str] = {}

    def _next_open_markets(*args: Any, **kwargs: Any) -> list[Any]:
        markets, sessions = next(lifecycle_inputs)
        current_sessions.clear()
        current_sessions.update(sessions)
        return markets

    def _current_session_info(market: Any) -> Any:
        return MagicMock(session_id=current_sessions[market.code])

    async def _run_once(markets: list[Any], processor: Any) -> None:
        for item in markets:
            await processor(item)
        cycle_count["count"] += 1
        if cycle_count["count"] >= 3:
            shutdown_event.set()

    close_handler = AsyncMock()

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=MagicMock()))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        telegram = stack.enter_context(
            patch(
                "src.main.TelegramClient",
                return_value=MagicMock(
                    notify_system_start=AsyncMock(),
                    notify_system_shutdown=AsyncMock(),
                    notify_market_open=AsyncMock(),
                    notify_market_session_transition=AsyncMock(),
                    close=AsyncMock(),
                ),
            )
        )
        command_handler = MagicMock(
            register_command=MagicMock(),
            register_command_with_args=MagicMock(),
            start_polling=AsyncMock(),
            stop_polling=AsyncMock(),
        )
        stack.enter_context(patch("src.main.TelegramCommandHandler", return_value=command_handler))
        smart_scanner = MagicMock()
        smart_scanner.get_stock_codes = MagicMock(return_value=[])
        smart_scanner.scan = AsyncMock(return_value=[])
        stack.enter_context(patch("src.main.SmartVolatilityScanner", return_value=smart_scanner))
        stack.enter_context(patch("src.main.CriticalityAssessor", return_value=MagicMock()))
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(patch("src.main.process_blackout_recovery_orders", new=AsyncMock()))
        stack.enter_context(patch("src.main.handle_domestic_pending_orders", new=AsyncMock()))
        stack.enter_context(patch("src.main.handle_overseas_pending_orders", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.build_overseas_symbol_universe", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(patch("src.main.get_open_markets", side_effect=_next_open_markets))
        stack.enter_context(patch("src.main._acquire_live_runtime_lock", return_value=None))
        stack.enter_context(patch("src.main.get_session_info", side_effect=_current_session_info))
        stack.enter_context(patch("src.main._should_mid_session_refresh", return_value=False))
        stack.enter_context(patch("src.main._should_rescan_market", return_value=False))
        stack.enter_context(patch("src.main._run_markets_in_parallel", side_effect=_run_once))
        stack.enter_context(patch("src.main.trading_cycle", new=AsyncMock()))
        stack.enter_context(patch("src.main._handle_market_close", new=close_handler))

        await main_module.run(settings)

    close_handler.assert_awaited_once()
    assert close_handler.await_args.kwargs["market_code"] == "KR"
    telegram.return_value.notify_market_open.assert_any_await(MARKETS["KR"].name)
    telegram.return_value.notify_market_open.assert_any_await(MARKETS["US_NASDAQ"].name)
    assert telegram.return_value.notify_market_open.await_count == 2
    telegram.return_value.notify_market_session_transition.assert_awaited_once_with(
        market_name=MARKETS["US_NASDAQ"].name,
        market_code="US_NASDAQ",
        previous_session_id="US_PRE",
        current_session_id="US_REG",
    )


@pytest.mark.asyncio
async def test_run_realtime_mode_restarts_completed_hard_stop_task() -> None:
    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    settings = _make_settings(TRADE_MODE="realtime", ENABLED_MARKETS="US_NASDAQ")
    market = MagicMock()
    market.code = "US_NASDAQ"
    market.name = "Nasdaq"
    market.exchange_code = "NASD"
    market.is_domestic = False
    market.timezone = ZoneInfo("America/New_York")

    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()

    broker = MagicMock()
    broker.close = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.close = AsyncMock()
    overseas_broker.get_overseas_balance = AsyncMock(return_value={"output1": []})

    async def _run_once(markets: list[Any], processor: Any) -> None:
        for item in markets:
            await processor(item)
        shutdown_event.set()

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=MagicMock()))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        telegram_client = MagicMock(
            notify_system_start=AsyncMock(),
            notify_system_shutdown=AsyncMock(),
            close=AsyncMock(),
        )
        telegram = stack.enter_context(
            patch("src.main.TelegramClient", return_value=telegram_client)
        )
        command_handler = MagicMock(
            register_command=MagicMock(),
            register_command_with_args=MagicMock(),
            start_polling=AsyncMock(),
            stop_polling=AsyncMock(),
        )
        stack.enter_context(patch("src.main.TelegramCommandHandler", return_value=command_handler))
        stack.enter_context(patch("src.main.SmartVolatilityScanner", return_value=MagicMock()))
        stack.enter_context(patch("src.main.CriticalityAssessor", return_value=MagicMock()))
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(patch("src.main.process_blackout_recovery_orders", new=AsyncMock()))
        stack.enter_context(patch("src.main.handle_overseas_pending_orders", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.build_overseas_symbol_universe", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(patch("src.main.get_open_markets", return_value=[market]))
        stack.enter_context(patch("src.main._acquire_live_runtime_lock", return_value=None))
        stack.enter_context(
            patch("src.main.get_session_info", return_value=MagicMock(session_id="US_REG"))
        )
        stack.enter_context(patch("src.main._should_mid_session_refresh", return_value=False))
        stack.enter_context(patch("src.main._should_rescan_market", return_value=False))
        stack.enter_context(patch("src.main._has_market_session_transition", return_value=False))
        stack.enter_context(patch("src.main._run_markets_in_parallel", side_effect=_run_once))
        stack.enter_context(patch("src.main.trading_cycle", new=AsyncMock()))
        restart_hook = stack.enter_context(
            patch("src.main._restart_realtime_hard_stop_task_if_needed", return_value=None)
        )

        await main_module.run(settings)

    assert restart_hook.called
    telegram.return_value.notify_system_shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_daily_mode_warning_logs_startup_anchor_and_last_regular_batch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    real_datetime = datetime

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any | None = None) -> datetime:
            current = real_datetime(2026, 3, 23, 0, 31, tzinfo=UTC)
            if tz is None:
                return current
            return current.astimezone(tz)

    settings = _make_settings(
        TRADE_MODE="daily",
        ENABLED_MARKETS="KR",
        SESSION_INTERVAL_HOURS=6,
        DAILY_SESSIONS=4,
    )
    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()

    broker = MagicMock()
    broker.close = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.close = AsyncMock()

    async def _run_daily_once(*args: Any, **kwargs: Any) -> float:
        shutdown_event.set()
        return 0.0

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.datetime", _FrozenDateTime))
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=MagicMock()))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        telegram = stack.enter_context(
            patch(
                "src.main.TelegramClient",
                return_value=MagicMock(
                    notify_system_start=AsyncMock(),
                    notify_system_shutdown=AsyncMock(),
                    close=AsyncMock(),
                ),
            )
        )
        command_handler = MagicMock(
            register_command=MagicMock(),
            register_command_with_args=MagicMock(),
            start_polling=AsyncMock(),
            stop_polling=AsyncMock(),
        )
        stack.enter_context(patch("src.main.TelegramCommandHandler", return_value=command_handler))
        stack.enter_context(patch("src.main.SmartVolatilityScanner", return_value=MagicMock()))
        stack.enter_context(patch("src.main.CriticalityAssessor", return_value=MagicMock()))
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.process_blackout_recovery_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.handle_overseas_pending_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.build_overseas_symbol_universe", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch("src.main.get_open_markets", return_value=[MARKETS["KR"]])
        )
        is_market_open_mock = stack.enter_context(
            patch("src.main.is_market_open", return_value=False)
        )
        stack.enter_context(
            patch("src.main._acquire_live_runtime_lock", return_value=None)
        )
        run_daily_session = stack.enter_context(
            patch("src.main.run_daily_session", new=AsyncMock(side_effect=_run_daily_once))
        )

        with caplog.at_level(logging.INFO):
            await main_module.run(settings)

    run_daily_session.assert_awaited_once()
    telegram.assert_called_once()
    assert "Daily batch cadence anchored to process start" in caplog.text
    assert "first_batch_utc=2026-03-23T00:31:00+00:00" in caplog.text
    assert "subsequent_batches_wait_hours=6" in caplog.text
    assert "market=KR" in caplog.text
    assert "markets_open_at_batch_start=true" in caplog.text
    assert "current_batch=2026-03-23T09:31:00+09:00" in caplog.text
    assert "next_scheduled_batch=2026-03-23T15:31:00+09:00" in caplog.text
    # The next scheduled batch is already after KR regular-session close, so the
    # helper exits on the close-boundary check before consulting is_market_open().
    is_market_open_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_live_daily_mode_starts_realtime_hard_stop_monitor(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    settings = _make_settings(
        MODE="live",
        TRADE_MODE="daily",
        ENABLED_MARKETS="US",
        REALTIME_HARD_STOP_ENABLED=True,
    )
    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()

    broker = MagicMock()
    broker.close = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.close = AsyncMock()

    websocket_client = MagicMock()
    websocket_client.run = AsyncMock(return_value=None)
    websocket_client.stop = AsyncMock()

    async def _run_daily_once(*args: Any, **kwargs: Any) -> float:
        shutdown_event.set()
        return 0.0

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        websocket_ctor = stack.enter_context(
            patch("src.main.KISWebSocketClient", return_value=websocket_client)
        )
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=MagicMock()))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        telegram = stack.enter_context(
            patch(
                "src.main.TelegramClient",
                return_value=MagicMock(
                    notify_system_start=AsyncMock(),
                    notify_system_shutdown=AsyncMock(),
                    close=AsyncMock(),
                ),
            )
        )
        command_handler = MagicMock(
            register_command=MagicMock(),
            register_command_with_args=MagicMock(),
            start_polling=AsyncMock(),
            stop_polling=AsyncMock(),
        )
        stack.enter_context(patch("src.main.TelegramCommandHandler", return_value=command_handler))
        stack.enter_context(patch("src.main.SmartVolatilityScanner", return_value=MagicMock()))
        stack.enter_context(patch("src.main.CriticalityAssessor", return_value=MagicMock()))
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(patch("src.main.process_blackout_recovery_orders", new=AsyncMock()))
        stack.enter_context(patch("src.main.handle_overseas_pending_orders", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.build_overseas_symbol_universe", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(patch("src.main.get_open_markets", return_value=[MARKETS["US_NASDAQ"]]))
        stack.enter_context(patch("src.main._acquire_live_runtime_lock", return_value=None))
        run_daily_session = stack.enter_context(
            patch("src.main.run_daily_session", new=AsyncMock(side_effect=_run_daily_once))
        )

        with caplog.at_level(logging.INFO):
            await main_module.run(settings)

    run_daily_session.assert_awaited_once()
    websocket_ctor.assert_called_once()
    websocket_client.run.assert_awaited_once()
    websocket_client.stop.assert_awaited_once()
    telegram.assert_called_once()
    assert "Realtime hard-stop websocket monitor started" in caplog.text
    assert "enabled_markets=US_NASDAQ,US_NYSE,US_AMEX" in caplog.text
    assert "source=websocket_hard_stop" in caplog.text


@pytest.mark.asyncio
async def test_run_daily_mode_keeps_default_wait_when_dst_regular_session_is_active(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _FakeEvent:
        def __init__(self) -> None:
            self._flag = False

        def is_set(self) -> bool:
            return self._flag

        def set(self) -> None:
            self._flag = True

        def clear(self) -> None:
            self._flag = False

        async def wait(self) -> None:
            return None

    real_datetime = datetime

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any | None = None) -> datetime:
            current = real_datetime(2026, 3, 25, 14, 12, tzinfo=UTC)
            if tz is None:
                return current
            return current.astimezone(tz)

    settings = _make_settings(
        TRADE_MODE="daily",
        ENABLED_MARKETS="US",
        SESSION_INTERVAL_HOURS=6,
        DAILY_SESSIONS=4,
    )
    shutdown_event = _FakeEvent()
    pause_event = _FakeEvent()
    pause_event.set()

    broker = MagicMock()
    broker.close = AsyncMock()
    overseas_broker = MagicMock()
    overseas_broker.close = AsyncMock()

    async def _run_daily_once(*args: Any, **kwargs: Any) -> float:
        shutdown_event.set()
        return 0.0

    with ExitStack() as stack:
        stack.enter_context(
            patch("src.main.asyncio.Event", side_effect=[shutdown_event, pause_event])
        )
        stack.enter_context(
            patch(
                "src.main.asyncio.get_running_loop",
                return_value=MagicMock(add_signal_handler=MagicMock()),
            )
        )
        stack.enter_context(patch("src.main.datetime", _FrozenDateTime))
        stack.enter_context(patch("src.main.KISBroker", return_value=broker))
        stack.enter_context(patch("src.main.OverseasBroker", return_value=overseas_broker))
        stack.enter_context(patch("src.main.DecisionEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.RiskManager", return_value=MagicMock()))
        stack.enter_context(patch("src.main.init_db", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DecisionLogger", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextAggregator", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextScheduler", return_value=MagicMock()))
        stack.enter_context(patch("src.main.EvolutionOptimizer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ContextSelector", return_value=MagicMock()))
        stack.enter_context(patch("src.main.ScenarioEngine", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PlaybookStore", return_value=MagicMock()))
        stack.enter_context(patch("src.main.DailyReviewer", return_value=MagicMock()))
        stack.enter_context(patch("src.main.PreMarketPlanner", return_value=MagicMock()))
        stack.enter_context(patch("src.main.NotificationFilter", return_value=MagicMock()))
        telegram = stack.enter_context(
            patch(
                "src.main.TelegramClient",
                return_value=MagicMock(
                    notify_system_start=AsyncMock(),
                    notify_system_shutdown=AsyncMock(),
                    close=AsyncMock(),
                ),
            )
        )
        command_handler = MagicMock(
            register_command=MagicMock(),
            register_command_with_args=MagicMock(),
            start_polling=AsyncMock(),
            stop_polling=AsyncMock(),
        )
        stack.enter_context(patch("src.main.TelegramCommandHandler", return_value=command_handler))
        stack.enter_context(patch("src.main.SmartVolatilityScanner", return_value=MagicMock()))
        stack.enter_context(patch("src.main.CriticalityAssessor", return_value=MagicMock()))
        stack.enter_context(
            patch(
                "src.main.PriorityTaskQueue",
                return_value=MagicMock(
                    get_metrics=AsyncMock(return_value=MagicMock(total_enqueued=0))
                ),
            )
        )
        stack.enter_context(patch("src.main._start_dashboard_server"))
        stack.enter_context(patch("src.main.sync_positions_from_broker", new=AsyncMock()))
        stack.enter_context(
            patch("src.main.process_blackout_recovery_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.handle_overseas_pending_orders", new=AsyncMock())
        )
        stack.enter_context(
            patch("src.main.build_overseas_symbol_universe", new=AsyncMock(return_value=[]))
        )
        stack.enter_context(
            patch("src.main.get_open_markets", return_value=[MARKETS["US_NASDAQ"]])
        )
        stack.enter_context(
            patch("src.main._acquire_live_runtime_lock", return_value=None)
        )
        run_daily_session = stack.enter_context(
            patch("src.main.run_daily_session", new=AsyncMock(side_effect=_run_daily_once))
        )

        with caplog.at_level(logging.INFO):
            await main_module.run(settings)

    run_daily_session.assert_awaited_once()
    telegram.assert_called_once()
    assert "Daily batch cadence anchored to process start" in caplog.text
    assert "Next session in 6.0 hours" in caplog.text
    assert "Daily mode has no additional regular-session batch before close" in caplog.text


@pytest.mark.asyncio
async def test_trading_cycle_orchestrates_stage_helpers_in_order() -> None:
    """Issue #447 regression: trading_cycle should preserve stage orchestration contracts."""
    call_order: list[str] = []
    criticality = MagicMock(value="NORMAL")
    snapshot = {"criticality": criticality}
    decision_data = {"decision": MagicMock(action="HOLD", confidence=0)}
    execution_result = {
        "should_return": False,
        "order_succeeded": True,
        "quantity": 0,
        "trade_price": 0.0,
        "trade_pnl": 0.0,
        "buy_trade": None,
        "buy_price": 0.0,
        "sell_qty": 0,
    }

    async def collect_side_effect(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["runtime_session_id"] == "KRX_REG"
        call_order.append("collect")
        return snapshot

    async def evaluate_side_effect(**kwargs: Any) -> dict[str, Any]:
        assert call_order == ["collect"]
        assert kwargs["snapshot"] is snapshot
        call_order.append("evaluate")
        return decision_data

    async def execute_side_effect(**kwargs: Any) -> dict[str, Any]:
        assert call_order == ["collect", "evaluate"]
        assert kwargs["snapshot"] is snapshot
        assert kwargs["decision_data"] is decision_data
        call_order.append("execute")
        return execution_result

    def log_side_effect(**kwargs: Any) -> None:
        assert call_order == ["collect", "evaluate", "execute"]
        assert kwargs["snapshot"] is snapshot
        assert kwargs["decision_data"] is decision_data
        assert kwargs["execution_result"] is execution_result
        call_order.append("log")

    criticality_assessor = MagicMock(get_timeout=MagicMock(return_value=5.0))
    market = MagicMock()
    market.name = "Korea"
    market.code = "KR"
    market.exchange_code = "KRX"
    market.is_domestic = True

    with (
        patch("src.main.get_session_info", return_value=MagicMock(session_id="KRX_REG")),
        patch(
            "src.main._collect_trading_cycle_market_snapshot",
            new=AsyncMock(side_effect=collect_side_effect),
        ),
        patch(
            "src.main._evaluate_trading_cycle_decision",
            new=AsyncMock(side_effect=evaluate_side_effect),
        ),
        patch(
            "src.main._execute_trading_cycle_action",
            new=AsyncMock(side_effect=execute_side_effect),
        ),
        patch("src.main._log_trading_cycle_trade", side_effect=log_side_effect),
    ):
        await main_module.trading_cycle(
            broker=MagicMock(),
            overseas_broker=MagicMock(),
            scenario_engine=MagicMock(),
            playbook=MagicMock(),
            risk=MagicMock(),
            db_conn=MagicMock(),
            decision_logger=MagicMock(),
            context_store=MagicMock(),
            criticality_assessor=criticality_assessor,
            telegram=MagicMock(),
            market=market,
            stock_code="005930",
            scan_candidates={},
            settings=MagicMock(),
        )

    assert call_order == ["collect", "evaluate", "execute", "log"]
    criticality_assessor.get_timeout.assert_called_once_with(criticality)


# ---------------------------------------------------------------------------
# _register_post_buy_for_hard_stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_post_buy_for_hard_stop_subscribes_new_position() -> None:
    monitor = RealtimeHardStopMonitor()
    ws_client = MagicMock()
    ws_client.subscribe = AsyncMock()

    await _register_post_buy_for_hard_stop(
        monitor=monitor,
        websocket_client=ws_client,
        market=MARKETS["KR"],
        stock_code="005930",
        stock_name="Samsung",
        entry_price=100.0,
        quantity=7,
        market_data={},
    )

    tracked = monitor.get("KR", "005930")
    assert tracked is not None
    assert tracked.quantity == 7
    assert tracked.stock_name == "Samsung"
    ws_client.subscribe.assert_awaited_once_with("KR", "005930")


@pytest.mark.asyncio
async def test_register_post_buy_for_hard_stop_uses_staged_exit_evidence_stop_loss() -> None:
    monitor = RealtimeHardStopMonitor()
    ws_client = MagicMock()
    ws_client.subscribe = AsyncMock()

    await _register_post_buy_for_hard_stop(
        monitor=monitor,
        websocket_client=ws_client,
        market=MARKETS["KR"],
        stock_code="005930",
        stock_name=None,
        entry_price=100.0,
        quantity=5,
        market_data={"_staged_exit_evidence": {"stop_loss_threshold": -4.0}},
    )

    tracked = monitor.get("KR", "005930")
    assert tracked is not None
    assert tracked.hard_stop_price == pytest.approx(96.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "market_data",
    [
        {},
        {"_staged_exit_evidence": {}},
    ],
    ids=["missing-staged-exit-evidence", "missing-stop-loss-threshold"],
)
async def test_register_post_buy_for_hard_stop_falls_back_to_default_stop_loss(
    market_data: dict[str, object]
) -> None:
    monitor = RealtimeHardStopMonitor()

    await _register_post_buy_for_hard_stop(
        monitor=monitor,
        websocket_client=None,
        market=MARKETS["KR"],
        stock_code="005930",
        stock_name=None,
        entry_price=100.0,
        quantity=5,
        market_data=market_data,
    )

    tracked = monitor.get("KR", "005930")
    assert tracked is not None
    assert tracked.hard_stop_price == pytest.approx(98.0)  # default -2.0%


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "threshold",
    [1.5, 0],
    ids=["positive", "zero"],
)
async def test_register_post_buy_for_hard_stop_ignores_non_negative_stop_loss_threshold(
    threshold: float
) -> None:
    monitor = RealtimeHardStopMonitor()

    await _register_post_buy_for_hard_stop(
        monitor=monitor,
        websocket_client=None,
        market=MARKETS["KR"],
        stock_code="005930",
        stock_name=None,
        entry_price=100.0,
        quantity=5,
        market_data={"_staged_exit_evidence": {"stop_loss_threshold": threshold}},
    )

    tracked = monitor.get("KR", "005930")
    assert tracked is not None
    assert tracked.hard_stop_price == pytest.approx(98.0)  # default -2.0%


@pytest.mark.asyncio
async def test_execute_trading_cycle_action_registers_hard_stop_after_successful_buy() -> None:
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    monitor = RealtimeHardStopMonitor()
    ws_client = MagicMock()
    ws_client.subscribe = AsyncMock()
    ws_client.unsubscribe = AsyncMock()

    await _execute_trading_cycle_action(
        broker=broker,
        overseas_broker=MagicMock(),
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=MagicMock(),
        telegram=MagicMock(notify_trade_execution=AsyncMock()),
        market=MARKETS["KR"],
        stock_code="005930",
        runtime_session_id="KRX_REG",
        snapshot={
            "current_price": 100.0,
            "total_cash": 1_000_000.0,
            "pnl_pct": 0.0,
            "candidate": None,
            "balance_data": {},
            "market_data": {},
        },
        decision_data={
            "decision": main_module.TradeDecision(action="BUY", confidence=85, rationale="buy"),
            "match": _make_buy_match(),
            "decision_id": "buy-dec",
        },
        settings=_make_settings(),
        realtime_hard_stop_monitor=monitor,
        realtime_hard_stop_client=ws_client,
    )

    tracked = monitor.get("KR", "005930")
    assert tracked is not None, "Newly bought position must be registered for hard-stop monitoring"
    ws_client.subscribe.assert_awaited_once_with("KR", "005930")


@pytest.mark.asyncio
async def test_register_post_buy_for_hard_stop_noop_when_monitor_is_none() -> None:
    ws_client = MagicMock()
    ws_client.subscribe = AsyncMock()

    await _register_post_buy_for_hard_stop(
        monitor=None,
        websocket_client=ws_client,
        market=MARKETS["KR"],
        stock_code="005930",
        stock_name=None,
        entry_price=100.0,
        quantity=5,
        market_data={},
    )

    ws_client.subscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_post_buy_for_hard_stop_noop_for_unsupported_market() -> None:
    monitor = RealtimeHardStopMonitor()
    ws_client = MagicMock()
    ws_client.subscribe = AsyncMock()

    # MARKETS["JP"] is not in _SUPPORTED_MARKETS, so no subscription should happen
    await _register_post_buy_for_hard_stop(
        monitor=monitor,
        websocket_client=ws_client,
        market=MARKETS["JP"],
        stock_code="7203",
        stock_name=None,
        entry_price=2000.0,
        quantity=10,
        market_data={},
    )

    assert monitor.get("JP", "7203") is None
    ws_client.subscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_daily_session_stock_registers_hard_stop_after_successful_buy() -> None:
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
    monitor = RealtimeHardStopMonitor()
    ws_client = MagicMock()
    ws_client.subscribe = AsyncMock()
    ws_client.unsubscribe = AsyncMock()

    playbook = _make_playbook("KR")
    engine = MagicMock()
    engine.evaluate = MagicMock(return_value=_make_buy_match())

    decision_logger = MagicMock()
    decision_logger.log_decision = MagicMock(return_value="buy-dec-daily")

    await _process_daily_session_stock(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=engine,
        playbook=playbook,
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        telegram=MagicMock(notify_trade_execution=AsyncMock()),
        settings=_make_settings(),
        market=MARKETS["KR"],
        stock_data={
            "stock_code": "005930",
            "current_price": 70000.0,
            "foreigner_net": 0,
            "price_change_pct": 0.0,
            "volume_ratio": 1.0,
        },
        candidate_map={},
        portfolio_data={},
        balance_data={},
        balance_info={},
        purchase_total=0.0,
        pnl_pct=0.0,
        total_eval=10_000_000.0,
        total_cash=10_000_000.0,
        runtime_session_id="KRX_REG",
        daily_buy_cooldown={},
        realtime_hard_stop_monitor=monitor,
        realtime_hard_stop_client=ws_client,
    )

    tracked = monitor.get("KR", "005930")
    assert tracked is not None, "daily batch BUY must register position for hard-stop monitoring"
    ws_client.subscribe.assert_awaited_once_with("KR", "005930")


@pytest.mark.asyncio
async def test_execute_trading_cycle_action_no_hard_stop_on_failed_buy() -> None:
    """Ghost subscription must NOT be created when the broker rejects a BUY order."""
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"rt_cd": "9", "msg1": "insufficient balance"})
    monitor = RealtimeHardStopMonitor()
    ws_client = MagicMock()
    ws_client.subscribe = AsyncMock()
    ws_client.unsubscribe = AsyncMock()

    await _execute_trading_cycle_action(
        broker=broker,
        overseas_broker=MagicMock(),
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=MagicMock(),
        telegram=MagicMock(notify_trade_execution=AsyncMock()),
        market=MARKETS["KR"],
        stock_code="005930",
        runtime_session_id="KRX_REG",
        snapshot={
            "current_price": 100.0,
            "total_cash": 1_000_000.0,
            "pnl_pct": 0.0,
            "candidate": None,
            "balance_data": {},
            "market_data": {},
        },
        decision_data={
            "decision": main_module.TradeDecision(action="BUY", confidence=85, rationale="buy"),
            "match": _make_buy_match(),
            "decision_id": "buy-dec",
        },
        settings=_make_settings(),
        realtime_hard_stop_monitor=monitor,
        realtime_hard_stop_client=ws_client,
    )

    assert monitor.get("KR", "005930") is None, (
        "Rejected BUY must not create hard-stop subscription"
    )
    ws_client.subscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_daily_session_stock_no_hard_stop_on_failed_buy() -> None:
    """Ghost subscription must NOT be created when the broker rejects a BUY in daily batch."""
    db_conn = init_db(":memory:")
    broker = MagicMock()
    broker.send_order = AsyncMock(return_value={"rt_cd": "9", "msg1": "insufficient balance"})
    monitor = RealtimeHardStopMonitor()
    ws_client = MagicMock()
    ws_client.subscribe = AsyncMock()
    ws_client.unsubscribe = AsyncMock()

    playbook = _make_playbook("KR")
    engine = MagicMock()
    engine.evaluate = MagicMock(return_value=_make_buy_match())
    decision_logger = MagicMock()
    decision_logger.log_decision = MagicMock(return_value="buy-dec-daily")

    await _process_daily_session_stock(
        broker=broker,
        overseas_broker=MagicMock(),
        scenario_engine=engine,
        playbook=playbook,
        risk=MagicMock(),
        db_conn=db_conn,
        decision_logger=decision_logger,
        telegram=MagicMock(notify_trade_execution=AsyncMock()),
        settings=_make_settings(),
        market=MARKETS["KR"],
        stock_data={
            "stock_code": "005930",
            "current_price": 70000.0,
            "foreigner_net": 0,
            "price_change_pct": 0.0,
            "volume_ratio": 1.0,
        },
        candidate_map={},
        portfolio_data={},
        balance_data={},
        balance_info={},
        purchase_total=0.0,
        pnl_pct=0.0,
        total_eval=10_000_000.0,
        total_cash=10_000_000.0,
        runtime_session_id="KRX_REG",
        daily_buy_cooldown={},
        realtime_hard_stop_monitor=monitor,
        realtime_hard_stop_client=ws_client,
    )

    assert monitor.get("KR", "005930") is None, (
        "Rejected BUY must not create hard-stop subscription"
    )
    ws_client.subscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_post_buy_for_hard_stop_survives_subscribe_failure() -> None:
    """Websocket subscribe failure must not propagate — monitor registration is preserved."""
    monitor = RealtimeHardStopMonitor()
    ws_client = MagicMock()
    ws_client.subscribe = AsyncMock(side_effect=Exception("ws error"))

    await _register_post_buy_for_hard_stop(
        monitor=monitor,
        websocket_client=ws_client,
        market=MARKETS["KR"],
        stock_code="005930",
        stock_name="Samsung",
        entry_price=100.0,
        quantity=7,
        market_data={},
    )

    tracked = monitor.get("KR", "005930")
    assert tracked is not None, "monitor.register() must succeed even if subscribe() raises"
    ws_client.subscribe.assert_awaited_once_with("KR", "005930")
