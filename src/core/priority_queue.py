"""Priority-based task queue for latency control.

Implements a thread-safe priority queue with timeout enforcement and metrics tracking.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from src.core.criticality import CriticalityLevel

logger = logging.getLogger(__name__)


@dataclass(order=True)
class PriorityTask:
    """Task with priority and timestamp for queue ordering."""

    # Lower priority value = higher urgency (CRITICAL=0, HIGH=1, NORMAL=2, LOW=3)
    priority: int
    timestamp: float
    # Task data not used in comparison
    task_id: str = field(compare=False)
    task_data: dict[str, Any] = field(compare=False, default_factory=dict)
    callback: Callable[[], Coroutine[Any, Any, Any]] | None = field(
        compare=False, default=None
    )


@dataclass
class QueueMetrics:
    """Metrics for priority queue performance monitoring."""

    total_enqueued: int = 0
    total_dequeued: int = 0
    total_timeouts: int = 0
    total_errors: int = 0
    current_size: int = 0
    # Average wait time per criticality level (in seconds)
    avg_wait_time: dict[CriticalityLevel, float] = field(default_factory=dict)
    # P95 wait time per criticality level
    p95_wait_time: dict[CriticalityLevel, float] = field(default_factory=dict)


class PriorityTaskQueue:
    """Thread-safe priority queue with timeout enforcement."""

    # Priority mapping for criticality levels
    PRIORITY_MAP = {
        CriticalityLevel.CRITICAL: 0,
        CriticalityLevel.HIGH: 1,
        CriticalityLevel.NORMAL: 2,
        CriticalityLevel.LOW: 3,
    }

    def __init__(self, max_size: int = 1000) -> None:
        """Initialize the priority task queue.

        Args:
            max_size: Maximum queue size (default 1000)
        """
        self._queue: list[PriorityTask] = []
        self._lock = asyncio.Lock()
        self._max_size = max_size
        self._metrics = QueueMetrics()
        # Track wait times for metrics
        self._wait_times: dict[CriticalityLevel, list[float]] = {
            level: [] for level in CriticalityLevel
        }

    async def enqueue(
        self,
        task_id: str,
        criticality: CriticalityLevel,
        task_data: dict[str, Any],
        callback: Callable[[], Coroutine[Any, Any, Any]] | None = None,
    ) -> bool:
        """Add a task to the priority queue.

        Args:
            task_id: Unique identifier for the task
            criticality: Criticality level determining priority
            task_data: Data associated with the task
            callback: Optional async callback to execute

        Returns:
            True if enqueued successfully, False if queue is full
        """
        async with self._lock:
            if len(self._queue) >= self._max_size:
                logger.warning(
                    "Priority queue full (size=%d), rejecting task %s",
                    len(self._queue),
                    task_id,
                )
                return False

            priority = self.PRIORITY_MAP[criticality]
            timestamp = time.time()

            task = PriorityTask(
                priority=priority,
                timestamp=timestamp,
                task_id=task_id,
                task_data=task_data,
                callback=callback,
            )

            heapq.heappush(self._queue, task)
            self._metrics.total_enqueued += 1
            self._metrics.current_size = len(self._queue)

            logger.debug(
                "Enqueued task %s with criticality %s (priority=%d, queue_size=%d)",
                task_id,
                criticality.value,
                priority,
                len(self._queue),
            )

            return True

    async def dequeue(self, timeout: float | None = None) -> PriorityTask | None:
        """Remove and return the highest priority task from the queue.

        Args:
            timeout: Maximum time to wait for a task (seconds)

        Returns:
            PriorityTask if available, None if queue is empty or timeout
        """
        start_time = time.time()
        deadline = start_time + timeout if timeout else None

        while True:
            async with self._lock:
                if self._queue:
                    task = heapq.heappop(self._queue)
                    self._metrics.total_dequeued += 1
                    self._metrics.current_size = len(self._queue)

                    # Calculate wait time
                    wait_time = time.time() - task.timestamp
                    criticality = self._get_criticality_from_priority(task.priority)
                    self._wait_times[criticality].append(wait_time)
                    self._update_wait_time_metrics()

                    logger.debug(
                        "Dequeued task %s (priority=%d, wait_time=%.2fs, queue_size=%d)",
                        task.task_id,
                        task.priority,
                        wait_time,
                        len(self._queue),
                    )

                    return task

            # Queue is empty
            if deadline and time.time() >= deadline:
                return None

            # Wait a bit before checking again
            await asyncio.sleep(0.1)

    async def execute_with_timeout(
        self,
        task: PriorityTask,
        timeout: float | None,
    ) -> Any:
        """Execute a task with timeout enforcement.

        Args:
            task: Task to execute
            timeout: Timeout in seconds (None = no timeout)

        Returns:
            Result from task callback

        Raises:
            asyncio.TimeoutError: If task exceeds timeout
            Exception: Any exception raised by the task callback
        """
        if not task.callback:
            logger.warning("Task %s has no callback, skipping execution", task.task_id)
            return None

        criticality = self._get_criticality_from_priority(task.priority)

        try:
            if timeout:
                result = await asyncio.wait_for(task.callback(), timeout=timeout)
            else:
                result = await task.callback()

            logger.debug(
                "Task %s completed successfully (criticality=%s)",
                task.task_id,
                criticality.value,
            )
            return result

        except TimeoutError:
            self._metrics.total_timeouts += 1
            logger.error(
                "Task %s timed out after %.2fs (criticality=%s)",
                task.task_id,
                timeout or 0.0,
                criticality.value,
            )
            raise

        except Exception as exc:
            self._metrics.total_errors += 1
            logger.exception(
                "Task %s failed with error (criticality=%s): %s",
                task.task_id,
                criticality.value,
                exc,
            )
            raise

    def _get_criticality_from_priority(self, priority: int) -> CriticalityLevel:
        """Convert priority back to criticality level."""
        for level, prio in self.PRIORITY_MAP.items():
            if prio == priority:
                return level
        return CriticalityLevel.NORMAL

    def _update_wait_time_metrics(self) -> None:
        """Update average and p95 wait time metrics."""
        for level, times in self._wait_times.items():
            if not times:
                continue

            # Keep only last 1000 measurements to avoid memory bloat
            if len(times) > 1000:
                self._wait_times[level] = times[-1000:]
                times = self._wait_times[level]

            # Calculate average
            self._metrics.avg_wait_time[level] = sum(times) / len(times)

            # Calculate P95
            sorted_times = sorted(times)
            p95_idx = int(len(sorted_times) * 0.95)
            self._metrics.p95_wait_time[level] = sorted_times[p95_idx]

    async def get_metrics(self) -> QueueMetrics:
        """Get current queue metrics.

        Returns:
            QueueMetrics with current statistics
        """
        async with self._lock:
            return QueueMetrics(
                total_enqueued=self._metrics.total_enqueued,
                total_dequeued=self._metrics.total_dequeued,
                total_timeouts=self._metrics.total_timeouts,
                total_errors=self._metrics.total_errors,
                current_size=self._metrics.current_size,
                avg_wait_time=dict(self._metrics.avg_wait_time),
                p95_wait_time=dict(self._metrics.p95_wait_time),
            )

    async def size(self) -> int:
        """Get current queue size.

        Returns:
            Number of tasks in queue
        """
        async with self._lock:
            return len(self._queue)

    async def clear(self) -> int:
        """Clear all tasks from the queue.

        Returns:
            Number of tasks cleared
        """
        async with self._lock:
            count = len(self._queue)
            self._queue.clear()
            self._metrics.current_size = 0
            logger.info("Cleared %d tasks from priority queue", count)
            return count
