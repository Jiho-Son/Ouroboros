"""Economic calendar integration for major market events.

Tracks FOMC meetings, GDP releases, CPI, earnings calendars, and other
market-moving events.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EconomicEvent:
    """Single economic event."""

    name: str
    event_type: str  # "FOMC", "GDP", "CPI", "EARNINGS", etc.
    datetime: datetime
    impact: str  # "HIGH", "MEDIUM", "LOW"
    country: str
    description: str


@dataclass
class UpcomingEvents:
    """Collection of upcoming economic events."""

    events: list[EconomicEvent]
    high_impact_count: int
    next_major_event: EconomicEvent | None


class EconomicCalendar:
    """Economic calendar with event tracking and impact scoring."""

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize economic calendar.

        Args:
            api_key: API key for calendar provider (None for testing/hardcoded)
        """
        self._api_key = api_key
        # For now, use hardcoded major events (can be extended with API)
        self._events: list[EconomicEvent] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_upcoming_events(
        self, days_ahead: int = 7, min_impact: str = "MEDIUM"
    ) -> UpcomingEvents:
        """Get upcoming economic events within specified timeframe.

        Args:
            days_ahead: Number of days to look ahead
            min_impact: Minimum impact level ("LOW", "MEDIUM", "HIGH")

        Returns:
            UpcomingEvents with filtered events
        """
        now = datetime.now()
        end_date = now + timedelta(days=days_ahead)

        # Filter events by timeframe and impact
        upcoming = [
            event
            for event in self._events
            if now <= event.datetime <= end_date
            and self._impact_level(event.impact) >= self._impact_level(min_impact)
        ]

        # Sort by datetime
        upcoming.sort(key=lambda e: e.datetime)

        # Count high-impact events
        high_impact_count = sum(1 for e in upcoming if e.impact == "HIGH")

        # Get next major event
        next_major = None
        for event in upcoming:
            if event.impact == "HIGH":
                next_major = event
                break

        return UpcomingEvents(
            events=upcoming,
            high_impact_count=high_impact_count,
            next_major_event=next_major,
        )

    def add_event(self, event: EconomicEvent) -> None:
        """Add an economic event to the calendar."""
        self._events.append(event)

    def clear_events(self) -> None:
        """Clear all events (useful for testing)."""
        self._events.clear()

    def get_earnings_date(self, stock_code: str) -> datetime | None:
        """Get next earnings date for a stock.

        Args:
            stock_code: Stock ticker symbol

        Returns:
            Next earnings datetime or None if not found
        """
        now = datetime.now()
        earnings_events = [
            event
            for event in self._events
            if event.event_type == "EARNINGS"
            and stock_code.upper() in event.name.upper()
            and event.datetime > now
        ]

        if not earnings_events:
            return None

        # Return earliest upcoming earnings
        earnings_events.sort(key=lambda e: e.datetime)
        return earnings_events[0].datetime

    def load_hardcoded_events(self) -> None:
        """Load hardcoded major economic events for 2026.

        This is a fallback when no API is available.
        """
        # Major FOMC meetings in 2026 (estimated)
        fomc_dates = [
            datetime(2026, 3, 18),
            datetime(2026, 5, 6),
            datetime(2026, 6, 17),
            datetime(2026, 7, 29),
            datetime(2026, 9, 16),
            datetime(2026, 11, 4),
            datetime(2026, 12, 16),
        ]

        for date in fomc_dates:
            self.add_event(
                EconomicEvent(
                    name="FOMC Meeting",
                    event_type="FOMC",
                    datetime=date,
                    impact="HIGH",
                    country="US",
                    description="Federal Reserve interest rate decision",
                )
            )

        # Quarterly GDP releases (estimated)
        gdp_dates = [
            datetime(2026, 4, 28),
            datetime(2026, 7, 30),
            datetime(2026, 10, 29),
        ]

        for date in gdp_dates:
            self.add_event(
                EconomicEvent(
                    name="US GDP Release",
                    event_type="GDP",
                    datetime=date,
                    impact="HIGH",
                    country="US",
                    description="Quarterly GDP growth rate",
                )
            )

        # Monthly CPI releases (12th of each month, estimated)
        for month in range(1, 13):
            try:
                cpi_date = datetime(2026, month, 12)
                self.add_event(
                    EconomicEvent(
                        name="US CPI Release",
                        event_type="CPI",
                        datetime=cpi_date,
                        impact="HIGH",
                        country="US",
                        description="Consumer Price Index inflation data",
                    )
                )
            except ValueError:
                continue

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _impact_level(self, impact: str) -> int:
        """Convert impact string to numeric level."""
        levels = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
        return levels.get(impact.upper(), 0)

    def is_high_volatility_period(self, hours_ahead: int = 24) -> bool:
        """Check if we're near a high-impact event.

        Args:
            hours_ahead: Number of hours to look ahead

        Returns:
            True if high-impact event is imminent
        """
        now = datetime.now()
        threshold = now + timedelta(hours=hours_ahead)

        for event in self._events:
            if event.impact == "HIGH" and now <= event.datetime <= threshold:
                return True

        return False
