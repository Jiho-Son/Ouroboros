"""Tests for latency control system (criticality assessment and priority queue)."""

from __future__ import annotations

import asyncio

import pytest

from src.core.criticality import CriticalityAssessor, CriticalityLevel
from src.core.priority_queue import PriorityTask, PriorityTaskQueue

# ---------------------------------------------------------------------------
# CriticalityAssessor Tests
# ---------------------------------------------------------------------------


class TestCriticalityAssessor:
    """Test suite for criticality assessment logic."""

    def test_market_closed_returns_low(self) -> None:
        """Market closed should return LOW priority."""
        assessor = CriticalityAssessor()
        level = assessor.assess_market_conditions(
            pnl_pct=0.0,
            volatility_score=50.0,
            volume_surge=1.0,
            is_market_open=False,
        )
        assert level == CriticalityLevel.LOW

    def test_very_low_volatility_returns_low(self) -> None:
        """Very low volatility should return LOW priority."""
        assessor = CriticalityAssessor()
        level = assessor.assess_market_conditions(
            pnl_pct=0.0,
            volatility_score=20.0,  # Below 30.0 threshold
            volume_surge=1.0,
            is_market_open=True,
        )
        assert level == CriticalityLevel.LOW

    def test_critical_pnl_threshold_triggered(self) -> None:
        """P&L below -2.5% should trigger CRITICAL."""
        assessor = CriticalityAssessor()
        level = assessor.assess_market_conditions(
            pnl_pct=-2.6,  # Below -2.5% threshold
            volatility_score=50.0,
            volume_surge=1.0,
            is_market_open=True,
        )
        assert level == CriticalityLevel.CRITICAL

    def test_critical_pnl_at_circuit_breaker_proximity(self) -> None:
        """P&L at exactly -2.5% (near -3.0% breaker) should be CRITICAL."""
        assessor = CriticalityAssessor()
        level = assessor.assess_market_conditions(
            pnl_pct=-2.5,
            volatility_score=50.0,
            volume_surge=1.0,
            is_market_open=True,
        )
        assert level == CriticalityLevel.CRITICAL

    def test_critical_price_change_positive(self) -> None:
        """Large positive price change (>5%) should trigger CRITICAL."""
        assessor = CriticalityAssessor()
        level = assessor.assess_market_conditions(
            pnl_pct=0.0,
            volatility_score=50.0,
            volume_surge=1.0,
            price_change_1m=5.5,  # Above 5.0% threshold
            is_market_open=True,
        )
        assert level == CriticalityLevel.CRITICAL

    def test_critical_price_change_negative(self) -> None:
        """Large negative price change (<-5%) should trigger CRITICAL."""
        assessor = CriticalityAssessor()
        level = assessor.assess_market_conditions(
            pnl_pct=0.0,
            volatility_score=50.0,
            volume_surge=1.0,
            price_change_1m=-6.0,  # Below -5.0% threshold
            is_market_open=True,
        )
        assert level == CriticalityLevel.CRITICAL

    def test_critical_volume_surge(self) -> None:
        """Extreme volume surge (>10x) should trigger CRITICAL."""
        assessor = CriticalityAssessor()
        level = assessor.assess_market_conditions(
            pnl_pct=0.0,
            volatility_score=50.0,
            volume_surge=12.0,  # Above 10.0x threshold
            is_market_open=True,
        )
        assert level == CriticalityLevel.CRITICAL

    def test_high_volatility_returns_high(self) -> None:
        """High volatility score should return HIGH priority."""
        assessor = CriticalityAssessor()
        level = assessor.assess_market_conditions(
            pnl_pct=0.0,
            volatility_score=75.0,  # Above 70.0 threshold
            volume_surge=1.0,
            is_market_open=True,
        )
        assert level == CriticalityLevel.HIGH

    def test_normal_conditions_return_normal(self) -> None:
        """Normal market conditions should return NORMAL priority."""
        assessor = CriticalityAssessor()
        level = assessor.assess_market_conditions(
            pnl_pct=0.5,
            volatility_score=50.0,  # Between 30-70
            volume_surge=1.5,
            price_change_1m=1.0,
            is_market_open=True,
        )
        assert level == CriticalityLevel.NORMAL

    def test_custom_thresholds(self) -> None:
        """Custom thresholds should be respected."""
        assessor = CriticalityAssessor(
            critical_pnl_threshold=-1.0,
            critical_price_change_threshold=3.0,
            critical_volume_surge_threshold=5.0,
            high_volatility_threshold=60.0,
            low_volatility_threshold=20.0,
        )

        # Test custom P&L threshold
        level = assessor.assess_market_conditions(
            pnl_pct=-1.1,
            volatility_score=50.0,
            volume_surge=1.0,
            is_market_open=True,
        )
        assert level == CriticalityLevel.CRITICAL

        # Test custom price change threshold
        level = assessor.assess_market_conditions(
            pnl_pct=0.0,
            volatility_score=50.0,
            volume_surge=1.0,
            price_change_1m=3.5,
            is_market_open=True,
        )
        assert level == CriticalityLevel.CRITICAL

    def test_get_timeout_returns_correct_values(self) -> None:
        """Timeout values should match specification."""
        assessor = CriticalityAssessor()

        assert assessor.get_timeout(CriticalityLevel.CRITICAL) == 5.0
        assert assessor.get_timeout(CriticalityLevel.HIGH) == 30.0
        assert assessor.get_timeout(CriticalityLevel.NORMAL) == 60.0
        assert assessor.get_timeout(CriticalityLevel.LOW) is None


# ---------------------------------------------------------------------------
# PriorityTaskQueue Tests
# ---------------------------------------------------------------------------


class TestPriorityTaskQueue:
    """Test suite for priority queue implementation."""

    @pytest.mark.asyncio
    async def test_enqueue_task(self) -> None:
        """Tasks should be enqueued successfully."""
        queue = PriorityTaskQueue()

        success = await queue.enqueue(
            task_id="test-1",
            criticality=CriticalityLevel.NORMAL,
            task_data={"action": "test"},
        )

        assert success is True
        assert await queue.size() == 1

    @pytest.mark.asyncio
    async def test_enqueue_rejects_when_full(self) -> None:
        """Queue should reject tasks when full."""
        queue = PriorityTaskQueue(max_size=2)

        # Fill the queue
        await queue.enqueue("task-1", CriticalityLevel.NORMAL, {})
        await queue.enqueue("task-2", CriticalityLevel.NORMAL, {})

        # Third task should be rejected
        success = await queue.enqueue("task-3", CriticalityLevel.NORMAL, {})
        assert success is False
        assert await queue.size() == 2

    @pytest.mark.asyncio
    async def test_dequeue_returns_highest_priority(self) -> None:
        """Dequeue should return highest priority task first."""
        queue = PriorityTaskQueue()

        # Enqueue tasks in reverse priority order
        await queue.enqueue("low", CriticalityLevel.LOW, {"priority": 3})
        await queue.enqueue("normal", CriticalityLevel.NORMAL, {"priority": 2})
        await queue.enqueue("high", CriticalityLevel.HIGH, {"priority": 1})
        await queue.enqueue("critical", CriticalityLevel.CRITICAL, {"priority": 0})

        # Dequeue should return CRITICAL first
        task = await queue.dequeue(timeout=1.0)
        assert task is not None
        assert task.task_id == "critical"
        assert task.priority == 0

        # Then HIGH
        task = await queue.dequeue(timeout=1.0)
        assert task is not None
        assert task.task_id == "high"
        assert task.priority == 1

    @pytest.mark.asyncio
    async def test_dequeue_fifo_within_same_priority(self) -> None:
        """Tasks with same priority should be FIFO."""
        queue = PriorityTaskQueue()

        # Enqueue multiple tasks with same priority
        await queue.enqueue("task-1", CriticalityLevel.NORMAL, {})
        await asyncio.sleep(0.01)  # Small delay to ensure different timestamps
        await queue.enqueue("task-2", CriticalityLevel.NORMAL, {})
        await asyncio.sleep(0.01)
        await queue.enqueue("task-3", CriticalityLevel.NORMAL, {})

        # Should dequeue in FIFO order
        task1 = await queue.dequeue(timeout=1.0)
        task2 = await queue.dequeue(timeout=1.0)
        task3 = await queue.dequeue(timeout=1.0)

        assert task1 is not None and task1.task_id == "task-1"
        assert task2 is not None and task2.task_id == "task-2"
        assert task3 is not None and task3.task_id == "task-3"

    @pytest.mark.asyncio
    async def test_dequeue_returns_none_when_empty(self) -> None:
        """Dequeue should return None when queue is empty after timeout."""
        queue = PriorityTaskQueue()

        task = await queue.dequeue(timeout=0.1)
        assert task is None

    @pytest.mark.asyncio
    async def test_execute_with_timeout_success(self) -> None:
        """Task execution should succeed within timeout."""
        queue = PriorityTaskQueue()

        # Create a simple async callback
        async def test_callback() -> str:
            await asyncio.sleep(0.01)
            return "success"

        task = PriorityTask(
            priority=0,
            timestamp=0.0,
            task_id="test",
            task_data={},
            callback=test_callback,
        )

        result = await queue.execute_with_timeout(task, timeout=1.0)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_execute_with_timeout_raises_timeout_error(self) -> None:
        """Task execution should raise TimeoutError if exceeds timeout."""
        queue = PriorityTaskQueue()

        # Create a slow async callback
        async def slow_callback() -> str:
            await asyncio.sleep(1.0)
            return "too slow"

        task = PriorityTask(
            priority=0,
            timestamp=0.0,
            task_id="test",
            task_data={},
            callback=slow_callback,
        )

        with pytest.raises(asyncio.TimeoutError):
            await queue.execute_with_timeout(task, timeout=0.1)

    @pytest.mark.asyncio
    async def test_execute_with_timeout_propagates_exceptions(self) -> None:
        """Task execution should propagate exceptions from callback."""
        queue = PriorityTaskQueue()

        # Create a failing async callback
        async def failing_callback() -> None:
            raise ValueError("Test error")

        task = PriorityTask(
            priority=0,
            timestamp=0.0,
            task_id="test",
            task_data={},
            callback=failing_callback,
        )

        with pytest.raises(ValueError, match="Test error"):
            await queue.execute_with_timeout(task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_execute_without_timeout(self) -> None:
        """Task execution should work without timeout (LOW priority)."""
        queue = PriorityTaskQueue()

        async def test_callback() -> str:
            await asyncio.sleep(0.01)
            return "success"

        task = PriorityTask(
            priority=3,
            timestamp=0.0,
            task_id="test",
            task_data={},
            callback=test_callback,
        )

        result = await queue.execute_with_timeout(task, timeout=None)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_get_metrics(self) -> None:
        """Queue should track metrics correctly."""
        queue = PriorityTaskQueue()

        # Enqueue and dequeue some tasks
        await queue.enqueue("task-1", CriticalityLevel.CRITICAL, {})
        await queue.enqueue("task-2", CriticalityLevel.HIGH, {})
        await queue.enqueue("task-3", CriticalityLevel.NORMAL, {})

        await queue.dequeue(timeout=1.0)
        await queue.dequeue(timeout=1.0)

        metrics = await queue.get_metrics()

        assert metrics.total_enqueued == 3
        assert metrics.total_dequeued == 2
        assert metrics.current_size == 1

    @pytest.mark.asyncio
    async def test_wait_time_metrics(self) -> None:
        """Queue should track wait times per criticality level."""
        queue = PriorityTaskQueue()

        # Enqueue tasks with different criticality
        await queue.enqueue("critical-1", CriticalityLevel.CRITICAL, {})
        await asyncio.sleep(0.05)  # Add some wait time

        await queue.dequeue(timeout=1.0)

        metrics = await queue.get_metrics()

        # Should have wait time metrics for CRITICAL
        assert CriticalityLevel.CRITICAL in metrics.avg_wait_time
        assert metrics.avg_wait_time[CriticalityLevel.CRITICAL] > 0.0

    @pytest.mark.asyncio
    async def test_clear_queue(self) -> None:
        """Clear should remove all tasks from queue."""
        queue = PriorityTaskQueue()

        await queue.enqueue("task-1", CriticalityLevel.NORMAL, {})
        await queue.enqueue("task-2", CriticalityLevel.NORMAL, {})
        await queue.enqueue("task-3", CriticalityLevel.NORMAL, {})

        cleared = await queue.clear()

        assert cleared == 3
        assert await queue.size() == 0

    @pytest.mark.asyncio
    async def test_concurrent_enqueue_dequeue(self) -> None:
        """Queue should handle concurrent operations safely."""
        queue = PriorityTaskQueue()

        # Concurrent enqueue operations
        async def enqueue_tasks() -> None:
            for i in range(10):
                await queue.enqueue(
                    f"task-{i}",
                    CriticalityLevel.NORMAL,
                    {"index": i},
                )

        # Concurrent dequeue operations
        async def dequeue_tasks() -> list[str]:
            tasks = []
            for _ in range(10):
                task = await queue.dequeue(timeout=1.0)
                if task:
                    tasks.append(task.task_id)
                await asyncio.sleep(0.01)
            return tasks

        # Run both concurrently
        enqueue_task = asyncio.create_task(enqueue_tasks())
        dequeue_task = asyncio.create_task(dequeue_tasks())

        await enqueue_task
        dequeued_ids = await dequeue_task

        # All tasks should be processed
        assert len(dequeued_ids) == 10

    @pytest.mark.asyncio
    async def test_timeout_metric_tracking(self) -> None:
        """Queue should track timeout occurrences."""
        queue = PriorityTaskQueue()

        async def slow_callback() -> str:
            await asyncio.sleep(1.0)
            return "too slow"

        task = PriorityTask(
            priority=0,
            timestamp=0.0,
            task_id="test",
            task_data={},
            callback=slow_callback,
        )

        try:
            await queue.execute_with_timeout(task, timeout=0.1)
        except TimeoutError:
            pass

        metrics = await queue.get_metrics()
        assert metrics.total_timeouts == 1

    @pytest.mark.asyncio
    async def test_error_metric_tracking(self) -> None:
        """Queue should track execution errors."""
        queue = PriorityTaskQueue()

        async def failing_callback() -> None:
            raise ValueError("Test error")

        task = PriorityTask(
            priority=0,
            timestamp=0.0,
            task_id="test",
            task_data={},
            callback=failing_callback,
        )

        try:
            await queue.execute_with_timeout(task, timeout=1.0)
        except ValueError:
            pass

        metrics = await queue.get_metrics()
        assert metrics.total_errors == 1


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestLatencyControlIntegration:
    """Integration tests for criticality assessment and priority queue."""

    @pytest.mark.asyncio
    async def test_critical_task_bypass_queue(self) -> None:
        """CRITICAL tasks should bypass lower priority tasks."""
        queue = PriorityTaskQueue()

        # Add normal priority tasks
        await queue.enqueue("normal-1", CriticalityLevel.NORMAL, {})
        await queue.enqueue("normal-2", CriticalityLevel.NORMAL, {})

        # Add critical task (should jump to front)
        await queue.enqueue("critical", CriticalityLevel.CRITICAL, {})

        # Dequeue should return critical first
        task = await queue.dequeue(timeout=1.0)
        assert task is not None
        assert task.task_id == "critical"

    @pytest.mark.asyncio
    async def test_timeout_enforcement_by_criticality(self) -> None:
        """Timeout enforcement should match criticality level."""
        assessor = CriticalityAssessor()

        # CRITICAL should have 5s timeout
        critical_timeout = assessor.get_timeout(CriticalityLevel.CRITICAL)
        assert critical_timeout == 5.0

        # HIGH should have 30s timeout
        high_timeout = assessor.get_timeout(CriticalityLevel.HIGH)
        assert high_timeout == 30.0

        # NORMAL should have 60s timeout
        normal_timeout = assessor.get_timeout(CriticalityLevel.NORMAL)
        assert normal_timeout == 60.0

        # LOW should have no timeout
        low_timeout = assessor.get_timeout(CriticalityLevel.LOW)
        assert low_timeout is None

    @pytest.mark.asyncio
    async def test_fast_path_execution_for_critical(self) -> None:
        """CRITICAL tasks should complete quickly."""
        queue = PriorityTaskQueue()

        # Create a fast callback simulating fast-path execution
        async def fast_path_callback() -> str:
            # Simulate simplified decision flow
            await asyncio.sleep(0.01)  # Very fast execution
            return "fast_path_complete"

        task = PriorityTask(
            priority=0,  # CRITICAL
            timestamp=0.0,
            task_id="critical-fast",
            task_data={},
            callback=fast_path_callback,
        )

        import time

        start = time.time()
        result = await queue.execute_with_timeout(task, timeout=5.0)
        elapsed = time.time() - start

        assert result == "fast_path_complete"
        assert elapsed < 5.0  # Should complete well under CRITICAL timeout

    @pytest.mark.asyncio
    async def test_graceful_degradation_when_queue_full(self) -> None:
        """System should gracefully handle full queue."""
        queue = PriorityTaskQueue(max_size=2)

        # Fill the queue
        await queue.enqueue("task-1", CriticalityLevel.NORMAL, {})
        await queue.enqueue("task-2", CriticalityLevel.NORMAL, {})

        # Try to add more tasks
        success = await queue.enqueue("task-3", CriticalityLevel.NORMAL, {})
        assert success is False

        # Queue should still function
        task = await queue.dequeue(timeout=1.0)
        assert task is not None

        # Now we can add another task
        success = await queue.enqueue("task-4", CriticalityLevel.NORMAL, {})
        assert success is True
