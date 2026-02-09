"""Context aggregation scheduler for periodic rollups and cleanup."""

from __future__ import annotations

import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime

from src.context.aggregator import ContextAggregator
from src.context.store import ContextStore


@dataclass(frozen=True)
class ScheduleResult:
    """Represents which scheduled tasks ran."""

    weekly: bool = False
    monthly: bool = False
    quarterly: bool = False
    annual: bool = False
    legacy: bool = False
    cleanup: bool = False


class ContextScheduler:
    """Run periodic context aggregations and cleanup when due."""

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        aggregator: ContextAggregator | None = None,
        store: ContextStore | None = None,
    ) -> None:
        if aggregator is None:
            if conn is None:
                raise ValueError("conn is required when aggregator is not provided")
            aggregator = ContextAggregator(conn)
        self.aggregator = aggregator

        if store is None:
            store = getattr(aggregator, "store", None)
        if store is None:
            if conn is None:
                raise ValueError("conn is required when store is not provided")
            store = ContextStore(conn)
        self.store = store

        self._last_run: dict[str, str] = {}

    def run_if_due(self, now: datetime | None = None) -> ScheduleResult:
        """Run scheduled aggregations if their schedule is due.

        Args:
            now: Current datetime (UTC). If None, uses current time.

        Returns:
            ScheduleResult indicating which tasks ran.
        """
        if now is None:
            now = datetime.now(UTC)

        today = now.date().isoformat()
        result = ScheduleResult()

        if self._should_run("cleanup", today):
            self.store.cleanup_expired_contexts()
            result = self._with(result, cleanup=True)

        if self._is_sunday(now) and self._should_run("weekly", today):
            week = now.strftime("%Y-W%V")
            self.aggregator.aggregate_weekly_from_daily(week)
            result = self._with(result, weekly=True)

        if self._is_last_day_of_month(now) and self._should_run("monthly", today):
            month = now.strftime("%Y-%m")
            self.aggregator.aggregate_monthly_from_weekly(month)
            result = self._with(result, monthly=True)

        if self._is_last_day_of_quarter(now) and self._should_run("quarterly", today):
            quarter = self._current_quarter(now)
            self.aggregator.aggregate_quarterly_from_monthly(quarter)
            result = self._with(result, quarterly=True)

        if self._is_last_day_of_year(now) and self._should_run("annual", today):
            year = str(now.year)
            self.aggregator.aggregate_annual_from_quarterly(year)
            result = self._with(result, annual=True)

            # Legacy rollup runs after annual aggregation.
            self.aggregator.aggregate_legacy_from_annual()
            result = self._with(result, legacy=True)

        return result

    def _should_run(self, key: str, date_str: str) -> bool:
        if self._last_run.get(key) == date_str:
            return False
        self._last_run[key] = date_str
        return True

    @staticmethod
    def _is_sunday(now: datetime) -> bool:
        return now.weekday() == 6

    @staticmethod
    def _is_last_day_of_month(now: datetime) -> bool:
        last_day = monthrange(now.year, now.month)[1]
        return now.day == last_day

    @classmethod
    def _is_last_day_of_quarter(cls, now: datetime) -> bool:
        if now.month not in (3, 6, 9, 12):
            return False
        return cls._is_last_day_of_month(now)

    @staticmethod
    def _is_last_day_of_year(now: datetime) -> bool:
        return now.month == 12 and now.day == 31

    @staticmethod
    def _current_quarter(now: datetime) -> str:
        quarter = (now.month - 1) // 3 + 1
        return f"{now.year}-Q{quarter}"

    @staticmethod
    def _with(result: ScheduleResult, **kwargs: bool) -> ScheduleResult:
        return ScheduleResult(
            weekly=kwargs.get("weekly", result.weekly),
            monthly=kwargs.get("monthly", result.monthly),
            quarterly=kwargs.get("quarterly", result.quarterly),
            annual=kwargs.get("annual", result.annual),
            legacy=kwargs.get("legacy", result.legacy),
            cleanup=kwargs.get("cleanup", result.cleanup),
        )
