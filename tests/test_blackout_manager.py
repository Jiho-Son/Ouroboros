from __future__ import annotations

from datetime import UTC, datetime

from src.core.blackout_manager import (
    BlackoutOrderManager,
    QueuedOrderIntent,
    parse_blackout_windows_kst,
)


def test_parse_blackout_windows_kst() -> None:
    windows = parse_blackout_windows_kst("23:30-00:10,11:20-11:30,invalid")
    assert len(windows) == 2


def test_blackout_manager_handles_cross_midnight_window() -> None:
    manager = BlackoutOrderManager(
        enabled=True,
        windows=parse_blackout_windows_kst("23:30-00:10"),
        max_queue_size=10,
    )
    # 2026-01-01 23:40 KST = 2026-01-01 14:40 UTC
    assert manager.in_blackout(datetime(2026, 1, 1, 14, 40, tzinfo=UTC))
    # 2026-01-02 00:20 KST = 2026-01-01 15:20 UTC
    assert not manager.in_blackout(datetime(2026, 1, 1, 15, 20, tzinfo=UTC))


def test_recovery_batch_only_after_blackout_exit() -> None:
    manager = BlackoutOrderManager(
        enabled=True,
        windows=parse_blackout_windows_kst("23:30-00:10"),
        max_queue_size=10,
    )
    intent = QueuedOrderIntent(
        market_code="KR",
        exchange_code="KRX",
        session_id="KRX_REG",
        stock_code="005930",
        order_type="BUY",
        quantity=1,
        price=100.0,
        source="test",
        queued_at=datetime.now(UTC),
    )
    assert manager.enqueue(intent)

    # Inside blackout: no pop yet
    inside_blackout = datetime(2026, 1, 1, 14, 40, tzinfo=UTC)
    assert manager.pop_recovery_batch(inside_blackout) == []

    # Outside blackout: pop full batch once
    outside_blackout = datetime(2026, 1, 1, 15, 20, tzinfo=UTC)
    batch = manager.pop_recovery_batch(outside_blackout)
    assert len(batch) == 1
    assert manager.pending_count == 0


def test_requeued_intent_is_processed_next_non_blackout_cycle() -> None:
    manager = BlackoutOrderManager(
        enabled=True,
        windows=parse_blackout_windows_kst("23:30-00:10"),
        max_queue_size=10,
    )
    intent = QueuedOrderIntent(
        market_code="KR",
        exchange_code="KRX",
        session_id="KRX_REG",
        stock_code="005930",
        order_type="BUY",
        quantity=1,
        price=100.0,
        source="test",
        queued_at=datetime.now(UTC),
    )
    manager.enqueue(intent)
    outside_blackout = datetime(2026, 1, 1, 15, 20, tzinfo=UTC)
    first_batch = manager.pop_recovery_batch(outside_blackout)
    assert len(first_batch) == 1

    manager.requeue(first_batch[0])
    second_batch = manager.pop_recovery_batch(outside_blackout)
    assert len(second_batch) == 1


def test_queue_overflow_drops_oldest_and_keeps_latest() -> None:
    manager = BlackoutOrderManager(
        enabled=True,
        windows=parse_blackout_windows_kst("23:30-00:10"),
        max_queue_size=2,
    )
    first = QueuedOrderIntent(
        market_code="KR",
        exchange_code="KRX",
        session_id="KRX_REG",
        stock_code="000001",
        order_type="BUY",
        quantity=1,
        price=100.0,
        source="first",
        queued_at=datetime.now(UTC),
    )
    second = QueuedOrderIntent(
        market_code="KR",
        exchange_code="KRX",
        session_id="KRX_REG",
        stock_code="000002",
        order_type="BUY",
        quantity=1,
        price=101.0,
        source="second",
        queued_at=datetime.now(UTC),
    )
    third = QueuedOrderIntent(
        market_code="KR",
        exchange_code="KRX",
        session_id="KRX_REG",
        stock_code="000003",
        order_type="SELL",
        quantity=2,
        price=102.0,
        source="third",
        queued_at=datetime.now(UTC),
    )

    assert manager.enqueue(first)
    assert manager.enqueue(second)
    assert manager.enqueue(third)
    assert manager.pending_count == 2
    assert manager.overflow_drop_count == 1

    outside_blackout = datetime(2026, 1, 1, 15, 20, tzinfo=UTC)
    batch = manager.pop_recovery_batch(outside_blackout)
    assert [intent.stock_code for intent in batch] == ["000002", "000003"]
