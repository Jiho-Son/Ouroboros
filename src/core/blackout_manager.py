"""Blackout policy and queued order-intent manager."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class BlackoutWindow:
    start: time
    end: time

    def contains(self, kst_time: time) -> bool:
        if self.start <= self.end:
            return self.start <= kst_time < self.end
        return kst_time >= self.start or kst_time < self.end


@dataclass
class QueuedOrderIntent:
    market_code: str
    exchange_code: str
    stock_code: str
    order_type: str
    quantity: int
    price: float
    source: str
    queued_at: datetime
    attempts: int = 0


def parse_blackout_windows_kst(raw: str) -> list[BlackoutWindow]:
    """Parse comma-separated KST windows like '23:30-00:10,11:20-11:30'."""
    windows: list[BlackoutWindow] = []
    for token in raw.split(","):
        span = token.strip()
        if not span or "-" not in span:
            continue
        start_raw, end_raw = [part.strip() for part in span.split("-", 1)]
        try:
            start_h, start_m = [int(v) for v in start_raw.split(":", 1)]
            end_h, end_m = [int(v) for v in end_raw.split(":", 1)]
        except (ValueError, TypeError):
            continue
        if not (0 <= start_h <= 23 and 0 <= end_h <= 23):
            continue
        if not (0 <= start_m <= 59 and 0 <= end_m <= 59):
            continue
        windows.append(BlackoutWindow(start=time(start_h, start_m), end=time(end_h, end_m)))
    return windows


class BlackoutOrderManager:
    """Tracks blackout mode and queues order intents until recovery."""

    def __init__(
        self,
        *,
        enabled: bool,
        windows: list[BlackoutWindow],
        max_queue_size: int = 500,
    ) -> None:
        self.enabled = enabled
        self._windows = windows
        self._queue: deque[QueuedOrderIntent] = deque()
        self._was_blackout = False
        self._max_queue_size = max_queue_size

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    def in_blackout(self, now: datetime | None = None) -> bool:
        if not self.enabled or not self._windows:
            return False
        now = now or datetime.now(UTC)
        kst_now = now.astimezone(ZoneInfo("Asia/Seoul")).timetz().replace(tzinfo=None)
        return any(window.contains(kst_now) for window in self._windows)

    def enqueue(self, intent: QueuedOrderIntent) -> bool:
        if len(self._queue) >= self._max_queue_size:
            return False
        self._queue.append(intent)
        return True

    def pop_recovery_batch(self, now: datetime | None = None) -> list[QueuedOrderIntent]:
        in_blackout_now = self.in_blackout(now)
        batch: list[QueuedOrderIntent] = []
        if not in_blackout_now and self._queue:
            while self._queue:
                batch.append(self._queue.popleft())
        self._was_blackout = in_blackout_now
        return batch

    def requeue(self, intent: QueuedOrderIntent) -> None:
        if len(self._queue) < self._max_queue_size:
            self._queue.append(intent)

    def clear(self) -> int:
        count = len(self._queue)
        self._queue.clear()
        return count
