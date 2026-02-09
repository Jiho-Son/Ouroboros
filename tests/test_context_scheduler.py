"""Tests for ContextScheduler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from src.context.scheduler import ContextScheduler


@dataclass
class StubAggregator:
    """Stub aggregator that records calls."""

    weekly_calls: list[str]
    monthly_calls: list[str]
    quarterly_calls: list[str]
    annual_calls: list[str]
    legacy_calls: int

    def aggregate_weekly_from_daily(self, week: str) -> None:
        self.weekly_calls.append(week)

    def aggregate_monthly_from_weekly(self, month: str) -> None:
        self.monthly_calls.append(month)

    def aggregate_quarterly_from_monthly(self, quarter: str) -> None:
        self.quarterly_calls.append(quarter)

    def aggregate_annual_from_quarterly(self, year: str) -> None:
        self.annual_calls.append(year)

    def aggregate_legacy_from_annual(self) -> None:
        self.legacy_calls += 1


@dataclass
class StubStore:
    """Stub store that records cleanup calls."""

    cleanup_calls: int = 0

    def cleanup_expired_contexts(self) -> None:
        self.cleanup_calls += 1


def make_scheduler() -> tuple[ContextScheduler, StubAggregator, StubStore]:
    aggregator = StubAggregator([], [], [], [], 0)
    store = StubStore()
    scheduler = ContextScheduler(aggregator=aggregator, store=store)
    return scheduler, aggregator, store


def test_run_if_due_weekly() -> None:
    scheduler, aggregator, store = make_scheduler()
    now = datetime(2026, 2, 8, 10, 0, tzinfo=UTC)  # Sunday

    result = scheduler.run_if_due(now)

    assert result.weekly is True
    assert aggregator.weekly_calls == ["2026-W06"]
    assert store.cleanup_calls == 1


def test_run_if_due_monthly() -> None:
    scheduler, aggregator, _store = make_scheduler()
    now = datetime(2026, 2, 28, 12, 0, tzinfo=UTC)  # Last day of month

    result = scheduler.run_if_due(now)

    assert result.monthly is True
    assert aggregator.monthly_calls == ["2026-02"]


def test_run_if_due_quarterly() -> None:
    scheduler, aggregator, _store = make_scheduler()
    now = datetime(2026, 3, 31, 12, 0, tzinfo=UTC)  # Last day of Q1

    result = scheduler.run_if_due(now)

    assert result.quarterly is True
    assert aggregator.quarterly_calls == ["2026-Q1"]


def test_run_if_due_annual_and_legacy() -> None:
    scheduler, aggregator, _store = make_scheduler()
    now = datetime(2026, 12, 31, 12, 0, tzinfo=UTC)

    result = scheduler.run_if_due(now)

    assert result.annual is True
    assert result.legacy is True
    assert aggregator.annual_calls == ["2026"]
    assert aggregator.legacy_calls == 1


def test_cleanup_runs_once_per_day() -> None:
    scheduler, _aggregator, store = make_scheduler()
    now = datetime(2026, 2, 9, 9, 0, tzinfo=UTC)

    scheduler.run_if_due(now)
    scheduler.run_if_due(now)

    assert store.cleanup_calls == 1
