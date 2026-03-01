"""Kill switch orchestration for emergency risk actions.

Order is fixed:
1) block new orders
2) cancel pending orders
3) refresh order state
4) reduce risk
5) snapshot and notify
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

StepCallable = Callable[[], Any | Awaitable[Any]]


@dataclass
class KillSwitchReport:
    reason: str
    steps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class KillSwitchOrchestrator:
    def __init__(self) -> None:
        self.new_orders_blocked = False

    async def _run_step(
        self,
        report: KillSwitchReport,
        name: str,
        fn: StepCallable | None,
    ) -> None:
        report.steps.append(name)
        if fn is None:
            return
        try:
            result = fn()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # pragma: no cover - intentionally resilient
            report.errors.append(f"{name}: {exc}")

    async def trigger(
        self,
        *,
        reason: str,
        cancel_pending_orders: StepCallable | None = None,
        refresh_order_state: StepCallable | None = None,
        reduce_risk: StepCallable | None = None,
        snapshot_state: StepCallable | None = None,
        notify: StepCallable | None = None,
    ) -> KillSwitchReport:
        report = KillSwitchReport(reason=reason)

        self.new_orders_blocked = True
        report.steps.append("block_new_orders")

        await self._run_step(report, "cancel_pending_orders", cancel_pending_orders)
        await self._run_step(report, "refresh_order_state", refresh_order_state)
        await self._run_step(report, "reduce_risk", reduce_risk)
        await self._run_step(report, "snapshot_state", snapshot_state)
        await self._run_step(report, "notify", notify)

        return report

    def clear_block(self) -> None:
        self.new_orders_blocked = False
