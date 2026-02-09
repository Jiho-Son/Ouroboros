"""Context aggregation logic for rolling up data from lower to higher layers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from src.context.layer import ContextLayer
from src.context.store import ContextStore


class ContextAggregator:
    """Aggregates context data from lower (finer) to higher (coarser) layers."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Initialize the aggregator with a database connection."""
        self.conn = conn
        self.store = ContextStore(conn)

    def aggregate_daily_from_trades(self, date: str | None = None) -> None:
        """Aggregate L6 (daily) context from trades table.

        Args:
            date: Date in YYYY-MM-DD format. If None, uses today.
        """
        if date is None:
            date = datetime.now(UTC).date().isoformat()

        # Calculate daily metrics from trades
        cursor = self.conn.execute(
            """
            SELECT
                COUNT(*) as trade_count,
                SUM(CASE WHEN action = 'BUY' THEN 1 ELSE 0 END) as buys,
                SUM(CASE WHEN action = 'SELL' THEN 1 ELSE 0 END) as sells,
                SUM(CASE WHEN action = 'HOLD' THEN 1 ELSE 0 END) as holds,
                AVG(confidence) as avg_confidence,
                SUM(pnl) as total_pnl,
                COUNT(DISTINCT stock_code) as unique_stocks,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses
            FROM trades
            WHERE DATE(timestamp) = ?
            """,
            (date,),
        )
        row = cursor.fetchone()

        if row and row[0] > 0:  # At least one trade
            trade_count, buys, sells, holds, avg_conf, total_pnl, stocks, wins, losses = row

            # Store daily metrics in L6
            self.store.set_context(ContextLayer.L6_DAILY, date, "trade_count", trade_count)
            self.store.set_context(ContextLayer.L6_DAILY, date, "buys", buys)
            self.store.set_context(ContextLayer.L6_DAILY, date, "sells", sells)
            self.store.set_context(ContextLayer.L6_DAILY, date, "holds", holds)
            self.store.set_context(
                ContextLayer.L6_DAILY, date, "avg_confidence", round(avg_conf, 2)
            )
            self.store.set_context(
                ContextLayer.L6_DAILY, date, "total_pnl", round(total_pnl, 2)
            )
            self.store.set_context(ContextLayer.L6_DAILY, date, "unique_stocks", stocks)
            win_rate = round(wins / max(wins + losses, 1) * 100, 2)
            self.store.set_context(ContextLayer.L6_DAILY, date, "win_rate", win_rate)

    def aggregate_weekly_from_daily(self, week: str | None = None) -> None:
        """Aggregate L5 (weekly) context from L6 (daily).

        Args:
            week: Week in YYYY-Www format (ISO week). If None, uses current week.
        """
        if week is None:
            week = datetime.now(UTC).strftime("%Y-W%V")

        # Get all daily contexts for this week
        cursor = self.conn.execute(
            """
            SELECT key, value FROM contexts
            WHERE layer = ? AND timeframe LIKE ?
            """,
            (ContextLayer.L6_DAILY.value, f"{week[:4]}-%"),  # All days in the year
        )

        # Group by key and collect all values
        import json
        from collections import defaultdict

        daily_data: dict[str, list[Any]] = defaultdict(list)
        for row in cursor.fetchall():
            daily_data[row[0]].append(json.loads(row[1]))

        if daily_data:
            # Sum all PnL values
            if "total_pnl" in daily_data:
                total_pnl = sum(daily_data["total_pnl"])
                self.store.set_context(
                    ContextLayer.L5_WEEKLY, week, "weekly_pnl", round(total_pnl, 2)
                )

            # Average all confidence values
            if "avg_confidence" in daily_data:
                conf_values = daily_data["avg_confidence"]
                avg_conf = sum(conf_values) / len(conf_values)
                self.store.set_context(
                    ContextLayer.L5_WEEKLY, week, "avg_confidence", round(avg_conf, 2)
                )

    def aggregate_monthly_from_weekly(self, month: str | None = None) -> None:
        """Aggregate L4 (monthly) context from L5 (weekly).

        Args:
            month: Month in YYYY-MM format. If None, uses current month.
        """
        if month is None:
            month = datetime.now(UTC).strftime("%Y-%m")

        # Get all weekly contexts for this month
        cursor = self.conn.execute(
            """
            SELECT key, value FROM contexts
            WHERE layer = ? AND timeframe LIKE ?
            """,
            (ContextLayer.L5_WEEKLY.value, f"{month[:4]}-W%"),
        )

        # Group by key and collect all values
        import json
        from collections import defaultdict

        weekly_data: dict[str, list[Any]] = defaultdict(list)
        for row in cursor.fetchall():
            weekly_data[row[0]].append(json.loads(row[1]))

        if weekly_data:
            # Sum all weekly PnL values
            if "weekly_pnl" in weekly_data:
                total_pnl = sum(weekly_data["weekly_pnl"])
                self.store.set_context(
                    ContextLayer.L4_MONTHLY, month, "monthly_pnl", round(total_pnl, 2)
                )

    def aggregate_quarterly_from_monthly(self, quarter: str | None = None) -> None:
        """Aggregate L3 (quarterly) context from L4 (monthly).

        Args:
            quarter: Quarter in YYYY-Qn format. If None, uses current quarter.
        """
        if quarter is None:
            from datetime import datetime

            now = datetime.now(UTC)
            q = (now.month - 1) // 3 + 1
            quarter = f"{now.year}-Q{q}"

        # Get all monthly contexts for this quarter
        # Q1: 01-03, Q2: 04-06, Q3: 07-09, Q4: 10-12
        q_num = int(quarter.split("-Q")[1])
        months = [f"{quarter[:4]}-{m:02d}" for m in range((q_num - 1) * 3 + 1, q_num * 3 + 1)]

        total_pnl = 0.0
        for month in months:
            monthly_pnl = self.store.get_context(
                ContextLayer.L4_MONTHLY, month, "monthly_pnl"
            )
            if monthly_pnl is not None:
                total_pnl += monthly_pnl

        self.store.set_context(
            ContextLayer.L3_QUARTERLY, quarter, "quarterly_pnl", round(total_pnl, 2)
        )

    def aggregate_annual_from_quarterly(self, year: str | None = None) -> None:
        """Aggregate L2 (annual) context from L3 (quarterly).

        Args:
            year: Year in YYYY format. If None, uses current year.
        """
        if year is None:
            year = str(datetime.now(UTC).year)

        # Get all quarterly contexts for this year
        total_pnl = 0.0
        for q in range(1, 5):
            quarter = f"{year}-Q{q}"
            quarterly_pnl = self.store.get_context(
                ContextLayer.L3_QUARTERLY, quarter, "quarterly_pnl"
            )
            if quarterly_pnl is not None:
                total_pnl += quarterly_pnl

        self.store.set_context(
            ContextLayer.L2_ANNUAL, year, "annual_pnl", round(total_pnl, 2)
        )

    def aggregate_legacy_from_annual(self) -> None:
        """Aggregate L1 (legacy) context from all L2 (annual) data."""
        # Get all annual PnL
        cursor = self.conn.execute(
            """
            SELECT timeframe, value FROM contexts
            WHERE layer = ? AND key = ?
            ORDER BY timeframe
            """,
            (ContextLayer.L2_ANNUAL.value, "annual_pnl"),
        )

        import json

        annual_data = [(row[0], json.loads(row[1])) for row in cursor.fetchall()]

        if annual_data:
            total_pnl = sum(pnl for _, pnl in annual_data)
            years_traded = len(annual_data)
            avg_annual_pnl = total_pnl / years_traded

            # Store in L1 (single "LEGACY" timeframe)
            self.store.set_context(
                ContextLayer.L1_LEGACY, "LEGACY", "total_pnl", round(total_pnl, 2)
            )
            self.store.set_context(
                ContextLayer.L1_LEGACY, "LEGACY", "years_traded", years_traded
            )
            self.store.set_context(
                ContextLayer.L1_LEGACY,
                "LEGACY",
                "avg_annual_pnl",
                round(avg_annual_pnl, 2),
            )

    def run_all_aggregations(self) -> None:
        """Run all aggregations from L7 to L1 (bottom-up).

        All timeframes are derived from the latest trade timestamp so that
        past data re-aggregation produces consistent results across layers.
        """
        cursor = self.conn.execute("SELECT MAX(timestamp) FROM trades")
        row = cursor.fetchone()
        if not row or row[0] is None:
            return

        ts_raw = row[0]
        if ts_raw.endswith("Z"):
            ts_raw = ts_raw.replace("Z", "+00:00")
        latest_ts = datetime.fromisoformat(ts_raw)
        trade_date = latest_ts.date()
        date_str = trade_date.isoformat()

        iso_year, iso_week, _ = trade_date.isocalendar()
        week_str = f"{iso_year}-W{iso_week:02d}"
        month_str = f"{trade_date.year}-{trade_date.month:02d}"
        quarter = (trade_date.month - 1) // 3 + 1
        quarter_str = f"{trade_date.year}-Q{quarter}"
        year_str = str(trade_date.year)

        # L7 (trades) → L6 (daily)
        self.aggregate_daily_from_trades(date_str)

        # L6 (daily) → L5 (weekly)
        self.aggregate_weekly_from_daily(week_str)

        # L5 (weekly) → L4 (monthly)
        self.aggregate_monthly_from_weekly(month_str)

        # L4 (monthly) → L3 (quarterly)
        self.aggregate_quarterly_from_monthly(quarter_str)

        # L3 (quarterly) → L2 (annual)
        self.aggregate_annual_from_quarterly(year_str)

        # L2 (annual) → L1 (legacy)
        self.aggregate_legacy_from_annual()
