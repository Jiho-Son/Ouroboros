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
