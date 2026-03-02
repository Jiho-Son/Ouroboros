import pytest

from src.core.kill_switch import KillSwitchOrchestrator


@pytest.mark.asyncio
async def test_kill_switch_executes_steps_in_order() -> None:
    ks = KillSwitchOrchestrator()
    calls: list[str] = []

    async def _cancel() -> None:
        calls.append("cancel")

    def _refresh() -> None:
        calls.append("refresh")

    def _reduce() -> None:
        calls.append("reduce")

    def _snapshot() -> None:
        calls.append("snapshot")

    def _notify() -> None:
        calls.append("notify")

    report = await ks.trigger(
        reason="test",
        cancel_pending_orders=_cancel,
        refresh_order_state=_refresh,
        reduce_risk=_reduce,
        snapshot_state=_snapshot,
        notify=_notify,
    )

    assert report.steps == [
        "block_new_orders",
        "cancel_pending_orders",
        "refresh_order_state",
        "reduce_risk",
        "snapshot_state",
        "notify",
    ]
    assert calls == ["cancel", "refresh", "reduce", "snapshot", "notify"]
    assert report.errors == []


@pytest.mark.asyncio
async def test_kill_switch_collects_step_errors() -> None:
    ks = KillSwitchOrchestrator()

    def _boom() -> None:
        raise RuntimeError("boom")

    report = await ks.trigger(reason="test", cancel_pending_orders=_boom)
    assert any(err.startswith("cancel_pending_orders:") for err in report.errors)


@pytest.mark.asyncio
async def test_kill_switch_refresh_retries_then_succeeds() -> None:
    ks = KillSwitchOrchestrator()
    refresh_calls = {"count": 0}

    def _flaky_refresh() -> None:
        refresh_calls["count"] += 1
        if refresh_calls["count"] < 3:
            raise RuntimeError("temporary refresh failure")

    report = await ks.trigger(
        reason="test",
        refresh_order_state=_flaky_refresh,
        refresh_retry_attempts=3,
        refresh_retry_base_delay_sec=0.0,
    )
    assert refresh_calls["count"] == 3
    assert report.errors == []


@pytest.mark.asyncio
async def test_kill_switch_refresh_retry_exhausted_records_error_and_continues() -> None:
    ks = KillSwitchOrchestrator()
    calls: list[str] = []

    def _refresh_fail() -> None:
        raise RuntimeError("persistent refresh failure")

    def _reduce() -> None:
        calls.append("reduce")

    def _snapshot() -> None:
        calls.append("snapshot")

    report = await ks.trigger(
        reason="test",
        refresh_order_state=_refresh_fail,
        reduce_risk=_reduce,
        snapshot_state=_snapshot,
        refresh_retry_attempts=2,
        refresh_retry_base_delay_sec=0.0,
    )
    assert any(
        err.startswith("refresh_order_state: failed after 2 attempts")
        for err in report.errors
    )
    assert calls == ["reduce", "snapshot"]
