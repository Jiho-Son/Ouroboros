"""Kill switch orchestration for emergency risk actions.

Order is fixed:
1) block new orders
2) cancel pending orders
3) refresh order state (retry up to 3 attempts with exponential backoff)
4) reduce risk
5) snapshot and notify
"""

from __future__ import annotations

import asyncio
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
    ) -> bool:
        report.steps.append(name)
        if fn is None:
            return True
        try:
            result = fn()
            if inspect.isawaitable(result):
                await result
            if result is False:
                raise RuntimeError("step returned False")
            return True
        except Exception as exc:  # pragma: no cover - intentionally resilient
            report.errors.append(f"{name}: {exc}")
            return False

    async def _run_refresh_with_retry(
        self,
        report: KillSwitchReport,
        fn: StepCallable | None,
        *,
        max_attempts: int,
        base_delay_sec: float,
    ) -> None:
        report.steps.append("refresh_order_state")
        if fn is None:
            return

        attempts = max(1, max_attempts)
        delay = max(0.0, base_delay_sec)
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = fn()
                if inspect.isawaitable(result):
                    await result
                if result is False:
                    raise RuntimeError("step returned False")
                return
            except Exception as exc:  # pragma: no cover - intentionally resilient
                last_exc = exc
                if attempt >= attempts:
                    break
                if delay > 0:
                    await asyncio.sleep(delay * (2 ** (attempt - 1)))
        if last_exc is not None:
            report.errors.append(
                "refresh_order_state: failed after "
                f"{attempts} attempts ({last_exc})"
            )

    async def trigger(
        self,
        *,
        reason: str,
        cancel_pending_orders: StepCallable | None = None,
        refresh_order_state: StepCallable | None = None,
        reduce_risk: StepCallable | None = None,
        snapshot_state: StepCallable | None = None,
        notify: StepCallable | None = None,
        refresh_retry_attempts: int = 3,
        refresh_retry_base_delay_sec: float = 1.0,
    ) -> KillSwitchReport:
        report = KillSwitchReport(reason=reason)

        self.new_orders_blocked = True
        report.steps.append("block_new_orders")

        await self._run_step(report, "cancel_pending_orders", cancel_pending_orders)
        await self._run_refresh_with_retry(
            report,
            refresh_order_state,
            max_attempts=refresh_retry_attempts,
            base_delay_sec=refresh_retry_base_delay_sec,
        )
        await self._run_step(report, "reduce_risk", reduce_risk)
        await self._run_step(report, "snapshot_state", snapshot_state)
        await self._run_step(report, "notify", notify)

        return report

    def clear_block(self) -> None:
        self.new_orders_blocked = False
