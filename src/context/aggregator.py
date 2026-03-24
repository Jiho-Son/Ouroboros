"""Context aggregation logic for rolling up data from lower to higher layers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.context.layer import ContextLayer
from src.context.store import ContextStore


class ContextAggregator:
    """Aggregates context data from lower (finer) to higher (coarser) layers."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Initialize the aggregator with a database connection."""
        self.conn = conn
        self.store = ContextStore(conn)

    def aggregate_daily_from_trades(
        self, date: str | None = None, market: str | None = None
    ) -> None:
        """Aggregate L6 (daily) context from trades table.

        Args:
            date: Date in YYYY-MM-DD format. If None, uses today.
            market: Market code filter (e.g., "KR", "US"). If None, aggregates all markets.
        """
        if date is None:
            date = datetime.now(UTC).date().isoformat()

        if market is None:
            cursor = self.conn.execute(
                """
                SELECT DISTINCT market
                FROM trades
                WHERE DATE(timestamp) = ?
                """,
                (date,),
            )
            markets = [row[0] for row in cursor.fetchall() if row[0]]
        else:
            markets = [market]

        for market_code in markets:
            # Calculate daily metrics from trades for the market
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
                WHERE DATE(timestamp) = ? AND market = ?
                """,
                (date, market_code),
            )
            row = cursor.fetchone()

            if row and row[0] > 0:  # At least one trade
                trade_count, buys, sells, holds, avg_conf, total_pnl, stocks, wins, losses = row

                key_suffix = f"_{market_code}"

                # Store daily metrics in L6 with market suffix
                self.store.set_context(
                    ContextLayer.L6_DAILY, date, f"trade_count{key_suffix}", trade_count
                )
                self.store.set_context(ContextLayer.L6_DAILY, date, f"buys{key_suffix}", buys)
                self.store.set_context(ContextLayer.L6_DAILY, date, f"sells{key_suffix}", sells)
                self.store.set_context(ContextLayer.L6_DAILY, date, f"holds{key_suffix}", holds)
                self.store.set_context(
                    ContextLayer.L6_DAILY,
                    date,
                    f"avg_confidence{key_suffix}",
                    round(avg_conf, 2),
                )
                self.store.set_context(
                    ContextLayer.L6_DAILY,
                    date,
                    f"total_pnl{key_suffix}",
                    round(total_pnl, 2),
                )
                self.store.set_context(
                    ContextLayer.L6_DAILY, date, f"unique_stocks{key_suffix}", stocks
                )
                win_rate = round(wins / max(wins + losses, 1) * 100, 2)
                self.store.set_context(
                    ContextLayer.L6_DAILY, date, f"win_rate{key_suffix}", win_rate
                )

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
            # Sum all PnL values (market-specific if suffixed)
            if "total_pnl" in daily_data:
                total_pnl = sum(daily_data["total_pnl"])
                self.store.set_context(
                    ContextLayer.L5_WEEKLY, week, "weekly_pnl", round(total_pnl, 2)
                )

            for key, values in daily_data.items():
                if key.startswith("total_pnl_"):
                    market_code = key.split("total_pnl_", 1)[1]
                    total_pnl = sum(values)
                    self.store.set_context(
                        ContextLayer.L5_WEEKLY,
                        week,
                        f"weekly_pnl_{market_code}",
                        round(total_pnl, 2),
                    )

            # Average all confidence values (market-specific if suffixed)
            if "avg_confidence" in daily_data:
                conf_values = daily_data["avg_confidence"]
                avg_conf = sum(conf_values) / len(conf_values)
                self.store.set_context(
                    ContextLayer.L5_WEEKLY, week, "avg_confidence", round(avg_conf, 2)
                )

            for key, values in daily_data.items():
                if key.startswith("avg_confidence_"):
                    market_code = key.split("avg_confidence_", 1)[1]
                    avg_conf = sum(values) / len(values)
                    self.store.set_context(
                        ContextLayer.L5_WEEKLY,
                        week,
                        f"avg_confidence_{market_code}",
                        round(avg_conf, 2),
                    )

    def aggregate_monthly_from_weekly(self, month: str | None = None) -> None:
        """Aggregate L4 (monthly) context from L5 (weekly).

        Args:
            month: Month in YYYY-MM format. If None, uses current month.
        """
        if month is None:
            month = datetime.now(UTC).strftime("%Y-%m")

        weekly_timeframes = self._iso_weeks_for_month(month)
        total_pnl, market_totals = self._collect_rollup_from_timeframes(
            ContextLayer.L5_WEEKLY,
            weekly_timeframes,
            "weekly_pnl",
        )

        if total_pnl is not None:
            self.store.set_context(
                ContextLayer.L4_MONTHLY, month, "monthly_pnl", round(total_pnl, 2)
            )
        for market_code, market_total in market_totals.items():
            self.store.set_context(
                ContextLayer.L4_MONTHLY,
                month,
                f"monthly_pnl_{market_code}",
                round(market_total, 2),
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

        total_pnl, market_totals = self._collect_rollup_from_timeframes(
            ContextLayer.L4_MONTHLY,
            months,
            "monthly_pnl",
        )

        if total_pnl is not None:
            self.store.set_context(
                ContextLayer.L3_QUARTERLY, quarter, "quarterly_pnl", round(total_pnl, 2)
            )
        for market_code, market_total in market_totals.items():
            self.store.set_context(
                ContextLayer.L3_QUARTERLY,
                quarter,
                f"quarterly_pnl_{market_code}",
                round(market_total, 2),
            )

    def aggregate_annual_from_quarterly(self, year: str | None = None) -> None:
        """Aggregate L2 (annual) context from L3 (quarterly).

        Args:
            year: Year in YYYY format. If None, uses current year.
        """
        if year is None:
            year = str(datetime.now(UTC).year)

        # Get all quarterly contexts for this year
        quarters = [f"{year}-Q{q}" for q in range(1, 5)]
        total_pnl, market_totals = self._collect_rollup_from_timeframes(
            ContextLayer.L3_QUARTERLY,
            quarters,
            "quarterly_pnl",
        )

        if total_pnl is not None:
            self.store.set_context(ContextLayer.L2_ANNUAL, year, "annual_pnl", round(total_pnl, 2))
        for market_code, market_total in market_totals.items():
            self.store.set_context(
                ContextLayer.L2_ANNUAL,
                year,
                f"annual_pnl_{market_code}",
                round(market_total, 2),
            )

    def aggregate_legacy_from_annual(self) -> None:
        """Aggregate L1 (legacy) context from all L2 (annual) data."""
        annual_timeframes = self._list_rollup_timeframes(ContextLayer.L2_ANNUAL, "annual_pnl")

        total_pnl, market_totals = self._collect_rollup_from_timeframes(
            ContextLayer.L2_ANNUAL,
            annual_timeframes,
            "annual_pnl",
        )
        years_traded = self._count_rollup_timeframes(
            ContextLayer.L2_ANNUAL,
            annual_timeframes,
            "annual_pnl",
        )

        if total_pnl is not None and years_traded > 0:
            avg_annual_pnl = total_pnl / years_traded

            # Store in L1 (single "LEGACY" timeframe)
            self.store.set_context(
                ContextLayer.L1_LEGACY, "LEGACY", "total_pnl", round(total_pnl, 2)
            )
            self.store.set_context(ContextLayer.L1_LEGACY, "LEGACY", "years_traded", years_traded)
            self.store.set_context(
                ContextLayer.L1_LEGACY,
                "LEGACY",
                "avg_annual_pnl",
                round(avg_annual_pnl, 2),
            )
            for market_code, market_total in market_totals.items():
                self.store.set_context(
                    ContextLayer.L1_LEGACY,
                    "LEGACY",
                    f"total_pnl_{market_code}",
                    round(market_total, 2),
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

    def _list_rollup_timeframes(
        self,
        layer: ContextLayer,
        base_key: str,
        *,
        timeframe_like: str | None = None,
    ) -> list[str]:
        """List distinct timeframes containing either global or market-scoped rollup keys."""
        query = """
            SELECT DISTINCT timeframe FROM contexts
            WHERE layer = ? AND (key = ? OR key LIKE ?)
        """
        params: list[Any] = [layer.value, base_key, f"{base_key}_%"]
        if timeframe_like is not None:
            query += " AND timeframe LIKE ?"
            params.append(timeframe_like)
        query += " ORDER BY timeframe"

        cursor = self.conn.execute(query, tuple(params))
        return [row[0] for row in cursor.fetchall()]

    def _collect_rollup_from_timeframes(
        self,
        layer: ContextLayer,
        timeframes: list[str],
        base_key: str,
    ) -> tuple[float | None, dict[str, float]]:
        """Collect global and market-scoped rollups from a source layer."""
        prefix = f"{base_key}_"
        total_pnl = 0.0
        market_totals: dict[str, float] = {}
        saw_value = False

        for timeframe in timeframes:
            contexts = self.store.get_all_contexts(layer, timeframe)
            market_values = {
                key[len(prefix) :]: float(value)
                for key, value in contexts.items()
                if key.startswith(prefix)
            }
            if market_values:
                total_pnl += sum(market_values.values())
                for market_code, value in market_values.items():
                    market_totals[market_code] = market_totals.get(market_code, 0.0) + value
                saw_value = True
                continue

            base_value = contexts.get(base_key)
            if base_value is None:
                continue

            total_pnl += float(base_value)
            saw_value = True

        if not saw_value:
            return None, {}
        return round(total_pnl, 2), {
            market_code: round(value, 2) for market_code, value in market_totals.items()
        }

    def _iso_weeks_for_month(self, month: str) -> list[str]:
        """List ISO week identifiers that overlap the given calendar month.

        The returned sequence is calendar-derived and may include week keys that
        have no persisted contexts yet; callers must tolerate missing store rows.
        """
        year, month_num = (int(part) for part in month.split("-"))
        current_day = date(year, month_num, 1)
        if month_num == 12:
            month_end = date(year + 1, 1, 1)
        else:
            month_end = date(year, month_num + 1, 1)

        seen: set[str] = set()
        iso_weeks: list[str] = []
        while current_day < month_end:
            iso_year, iso_week, _ = current_day.isocalendar()
            week_key = f"{iso_year}-W{iso_week:02d}"
            if week_key not in seen:
                seen.add(week_key)
                iso_weeks.append(week_key)
            current_day += timedelta(days=1)
        return iso_weeks

    def _count_rollup_timeframes(
        self,
        layer: ContextLayer,
        timeframes: list[str],
        base_key: str,
    ) -> int:
        """Count distinct timeframes that contain either global or market-scoped rollups."""
        prefix = f"{base_key}_"
        years_traded = 0
        for timeframe in timeframes:
            contexts = self.store.get_all_contexts(layer, timeframe)
            if base_key in contexts or any(key.startswith(prefix) for key in contexts):
                years_traded += 1
        return years_traded
