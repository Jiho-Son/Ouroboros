from __future__ import annotations

import logging

from src.core.realtime_hard_stop import HardStopTrigger, RealtimeHardStopMonitor


def test_register_position_stores_derived_stop_price() -> None:
    monitor = RealtimeHardStopMonitor()

    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=3,
        hard_stop_pct=-3.5,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    tracked = monitor.get("KR", "005930")
    assert tracked is not None
    assert tracked.hard_stop_price == 96.5
    assert tracked.quantity == 3


def test_register_is_idempotent_and_refreshes_metadata() -> None:
    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=3,
        hard_stop_pct=-3.5,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=5,
        hard_stop_pct=-2.0,
        decision_id="dec-2",
        position_timestamp="2026-03-09T00:01:00+00:00",
    )

    tracked = monitor.get("KR", "005930")
    assert tracked is not None
    assert tracked.hard_stop_price == 98.0
    assert tracked.quantity == 5
    assert tracked.decision_id == "dec-2"


def test_remove_clears_tracked_symbol() -> None:
    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=3,
        hard_stop_pct=-3.5,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    monitor.remove("KR", "005930")

    assert monitor.get("KR", "005930") is None


def test_register_preserves_in_flight_state_for_same_symbol() -> None:
    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=3,
        hard_stop_pct=-3.5,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )
    assert monitor.evaluate_price("KR", "005930", 96.0) is not None

    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=3,
        hard_stop_pct=-3.5,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    tracked = monitor.get("KR", "005930")
    assert tracked is not None
    assert tracked.in_flight is True


def test_price_above_stop_does_not_trigger() -> None:
    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=3,
        hard_stop_pct=-3.5,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    trigger = monitor.evaluate_price("KR", "005930", 97.0)

    assert trigger is None


def test_price_breach_triggers_once_until_released() -> None:
    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="KR",
        stock_code="005930",
        entry_price=100.0,
        quantity=3,
        hard_stop_pct=-3.5,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )

    first = monitor.evaluate_price("KR", "005930", 96.0)
    second = monitor.evaluate_price("KR", "005930", 95.5)
    monitor.release_in_flight("KR", "005930")
    third = monitor.evaluate_price("KR", "005930", 95.0)

    assert first == HardStopTrigger(
        market_code="KR",
        stock_code="005930",
        last_price=96.0,
        hard_stop_price=96.5,
        quantity=3,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )
    assert second is None
    assert third is not None


def test_evaluate_price_diagnostic_logs_us_entry_and_above_stop_result(
    caplog,
) -> None:
    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="US_NASDAQ",
        stock_code="AAPL",
        entry_price=100.0,
        quantity=3,
        hard_stop_pct=-3.5,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )
    caplog.set_level(logging.INFO)

    evaluation = monitor.evaluate_price_diagnostic("US_NASDAQ", "AAPL", 97.0)

    assert evaluation.reason == "above_stop"
    assert "action=enter" in caplog.text
    assert "action=result reason=above_stop" in caplog.text


def test_evaluate_price_diagnostic_logs_us_in_flight_result(caplog) -> None:
    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="US_NASDAQ",
        stock_code="AAPL",
        entry_price=100.0,
        quantity=3,
        hard_stop_pct=-3.5,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )
    monitor.evaluate_price_diagnostic("US_NASDAQ", "AAPL", 96.0)
    caplog.clear()
    caplog.set_level(logging.INFO)

    evaluation = monitor.evaluate_price_diagnostic("US_NASDAQ", "AAPL", 95.5)

    assert evaluation.reason == "in_flight"
    assert "action=enter" in caplog.text
    assert "action=result reason=in_flight" in caplog.text


def test_evaluate_price_diagnostic_logs_us_trigger_result(caplog) -> None:
    monitor = RealtimeHardStopMonitor()
    monitor.register(
        market_code="US_NASDAQ",
        stock_code="AAPL",
        entry_price=100.0,
        quantity=3,
        hard_stop_pct=-3.5,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )
    caplog.set_level(logging.INFO)

    evaluation = monitor.evaluate_price_diagnostic("US_NASDAQ", "AAPL", 96.0)

    assert evaluation.trigger == HardStopTrigger(
        market_code="US_NASDAQ",
        stock_code="AAPL",
        last_price=96.0,
        hard_stop_price=96.5,
        quantity=3,
        decision_id="dec-1",
        position_timestamp="2026-03-09T00:00:00+00:00",
    )
    assert "action=enter" in caplog.text
    assert "action=result reason=triggered" in caplog.text
