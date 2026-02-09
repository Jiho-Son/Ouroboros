"""Tests for the multi-layered context management system."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from src.context.aggregator import ContextAggregator
from src.context.layer import LAYER_CONFIG, ContextLayer
from src.context.store import ContextStore
from src.db import init_db, log_trade


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """Provide an in-memory database connection."""
    return init_db(":memory:")


@pytest.fixture
def store(db_conn: sqlite3.Connection) -> ContextStore:
    """Provide a ContextStore instance."""
    return ContextStore(db_conn)


@pytest.fixture
def aggregator(db_conn: sqlite3.Connection) -> ContextAggregator:
    """Provide a ContextAggregator instance."""
    return ContextAggregator(db_conn)


class TestContextStore:
    """Test suite for ContextStore CRUD operations."""

    def test_set_and_get_context(self, store: ContextStore) -> None:
        """Test setting and retrieving a context value."""
        store.set_context(ContextLayer.L6_DAILY, "2026-02-04", "total_pnl", 1234.56)

        value = store.get_context(ContextLayer.L6_DAILY, "2026-02-04", "total_pnl")
        assert value == 1234.56

    def test_get_nonexistent_context(self, store: ContextStore) -> None:
        """Test retrieving a non-existent context returns None."""
        value = store.get_context(ContextLayer.L6_DAILY, "2026-02-04", "nonexistent")
        assert value is None

    def test_update_existing_context(self, store: ContextStore) -> None:
        """Test updating an existing context value."""
        store.set_context(ContextLayer.L6_DAILY, "2026-02-04", "total_pnl", 100.0)
        store.set_context(ContextLayer.L6_DAILY, "2026-02-04", "total_pnl", 200.0)

        value = store.get_context(ContextLayer.L6_DAILY, "2026-02-04", "total_pnl")
        assert value == 200.0

    def test_get_all_contexts_for_layer(self, store: ContextStore) -> None:
        """Test retrieving all contexts for a specific layer."""
        store.set_context(ContextLayer.L6_DAILY, "2026-02-04", "total_pnl", 100.0)
        store.set_context(ContextLayer.L6_DAILY, "2026-02-04", "trade_count", 10)
        store.set_context(ContextLayer.L6_DAILY, "2026-02-04", "win_rate", 60.5)

        contexts = store.get_all_contexts(ContextLayer.L6_DAILY, "2026-02-04")
        assert len(contexts) == 3
        assert contexts["total_pnl"] == 100.0
        assert contexts["trade_count"] == 10
        assert contexts["win_rate"] == 60.5

    def test_get_latest_timeframe(self, store: ContextStore) -> None:
        """Test getting the most recent timeframe for a layer."""
        store.set_context(ContextLayer.L6_DAILY, "2026-02-01", "total_pnl", 100.0)
        store.set_context(ContextLayer.L6_DAILY, "2026-02-03", "total_pnl", 200.0)
        store.set_context(ContextLayer.L6_DAILY, "2026-02-02", "total_pnl", 150.0)

        latest = store.get_latest_timeframe(ContextLayer.L6_DAILY)
        # Latest by updated_at, which should be the last one set
        assert latest == "2026-02-02"

    def test_delete_old_contexts(
        self, store: ContextStore, db_conn: sqlite3.Connection
    ) -> None:
        """Test deleting contexts older than a cutoff date."""
        # Insert contexts with specific old timestamps
        # (bypassing set_context which uses current time)
        old_date = "2026-01-01T00:00:00+00:00"
        new_date = "2026-02-01T00:00:00+00:00"

        db_conn.execute(
            """
            INSERT INTO contexts (layer, timeframe, key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ContextLayer.L6_DAILY.value, "2026-01-01", "total_pnl", "100.0", old_date, old_date),
        )
        db_conn.execute(
            """
            INSERT INTO contexts (layer, timeframe, key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ContextLayer.L6_DAILY.value, "2026-02-01", "total_pnl", "200.0", new_date, new_date),
        )
        db_conn.commit()

        # Delete contexts before 2026-01-15
        cutoff = "2026-01-15T00:00:00+00:00"
        deleted = store.delete_old_contexts(ContextLayer.L6_DAILY, cutoff)

        # Should delete the 2026-01-01 context
        assert deleted == 1
        assert store.get_context(ContextLayer.L6_DAILY, "2026-02-01", "total_pnl") == 200.0
        assert store.get_context(ContextLayer.L6_DAILY, "2026-01-01", "total_pnl") is None

    def test_cleanup_expired_contexts(
        self, store: ContextStore, db_conn: sqlite3.Connection
    ) -> None:
        """Test automatic cleanup based on retention policies."""
        # Set old contexts for L7 (7 day retention)
        old_date = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        db_conn.execute(
            """
            INSERT INTO contexts (layer, timeframe, key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ContextLayer.L7_REALTIME.value, "2026-01-01", "price", "100.0", old_date, old_date),
        )
        db_conn.commit()

        deleted_counts = store.cleanup_expired_contexts()

        # Should delete the old L7 context (10 days > 7 day retention)
        assert deleted_counts[ContextLayer.L7_REALTIME] == 1

        # L1 has no retention limit, so nothing should be deleted
        assert deleted_counts[ContextLayer.L1_LEGACY] == 0

    def test_context_metadata_initialized(
        self, store: ContextStore, db_conn: sqlite3.Connection
    ) -> None:
        """Test that context metadata is properly initialized."""
        cursor = db_conn.execute("SELECT COUNT(*) FROM context_metadata")
        count = cursor.fetchone()[0]

        # Should have metadata for all 7 layers
        assert count == 7

        # Verify L1 metadata
        cursor = db_conn.execute(
            "SELECT description, retention_days FROM context_metadata WHERE layer = ?",
            (ContextLayer.L1_LEGACY.value,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert "Cumulative trading history" in row[0]
        assert row[1] is None  # No retention limit for L1


class TestContextAggregator:
    """Test suite for ContextAggregator."""

    def test_aggregate_daily_from_trades(
        self, aggregator: ContextAggregator, db_conn: sqlite3.Connection
    ) -> None:
        """Test aggregating daily metrics from trades."""
        date = datetime.now(UTC).date().isoformat()

        # Create sample trades
        log_trade(db_conn, "005930", "BUY", 85, "Good signal", quantity=10, price=70000, pnl=500)
        log_trade(db_conn, "000660", "SELL", 90, "Take profit", quantity=5, price=50000, pnl=1500)
        log_trade(db_conn, "035720", "HOLD", 75, "Wait", quantity=0, price=0, pnl=0)

        # Manually set timestamps to the target date
        db_conn.execute(
            f"UPDATE trades SET timestamp = '{date}T10:00:00+00:00'"
        )
        db_conn.commit()

        # Aggregate
        aggregator.aggregate_daily_from_trades(date, market="KR")

        # Verify L6 contexts
        store = aggregator.store
        assert store.get_context(ContextLayer.L6_DAILY, date, "trade_count_KR") == 3
        assert store.get_context(ContextLayer.L6_DAILY, date, "buys_KR") == 1
        assert store.get_context(ContextLayer.L6_DAILY, date, "sells_KR") == 1
        assert store.get_context(ContextLayer.L6_DAILY, date, "holds_KR") == 1
        assert store.get_context(ContextLayer.L6_DAILY, date, "total_pnl_KR") == 2000.0
        assert store.get_context(ContextLayer.L6_DAILY, date, "unique_stocks_KR") == 3
        # 2 wins, 0 losses
        assert store.get_context(ContextLayer.L6_DAILY, date, "win_rate_KR") == 100.0

    def test_aggregate_weekly_from_daily(self, aggregator: ContextAggregator) -> None:
        """Test aggregating weekly metrics from daily."""
        week = "2026-W06"

        # Set daily contexts
        aggregator.store.set_context(
            ContextLayer.L6_DAILY, "2026-02-02", "total_pnl_KR", 100.0
        )
        aggregator.store.set_context(
            ContextLayer.L6_DAILY, "2026-02-03", "total_pnl_KR", 200.0
        )
        aggregator.store.set_context(
            ContextLayer.L6_DAILY, "2026-02-02", "avg_confidence_KR", 80.0
        )
        aggregator.store.set_context(
            ContextLayer.L6_DAILY, "2026-02-03", "avg_confidence_KR", 85.0
        )

        # Aggregate
        aggregator.aggregate_weekly_from_daily(week)

        # Verify L5 contexts
        store = aggregator.store
        weekly_pnl = store.get_context(ContextLayer.L5_WEEKLY, week, "weekly_pnl_KR")
        avg_conf = store.get_context(ContextLayer.L5_WEEKLY, week, "avg_confidence_KR")

        assert weekly_pnl == 300.0
        assert avg_conf == 82.5

    def test_aggregate_monthly_from_weekly(self, aggregator: ContextAggregator) -> None:
        """Test aggregating monthly metrics from weekly."""
        month = "2026-02"

        # Set weekly contexts
        aggregator.store.set_context(
            ContextLayer.L5_WEEKLY, "2026-W05", "weekly_pnl_KR", 100.0
        )
        aggregator.store.set_context(
            ContextLayer.L5_WEEKLY, "2026-W06", "weekly_pnl_KR", 200.0
        )
        aggregator.store.set_context(
            ContextLayer.L5_WEEKLY, "2026-W07", "weekly_pnl_KR", 150.0
        )

        # Aggregate
        aggregator.aggregate_monthly_from_weekly(month)

        # Verify L4 contexts
        store = aggregator.store
        monthly_pnl = store.get_context(ContextLayer.L4_MONTHLY, month, "monthly_pnl")
        assert monthly_pnl == 450.0

    def test_aggregate_quarterly_from_monthly(self, aggregator: ContextAggregator) -> None:
        """Test aggregating quarterly metrics from monthly."""
        quarter = "2026-Q1"

        # Set monthly contexts for Q1 (Jan, Feb, Mar)
        aggregator.store.set_context(ContextLayer.L4_MONTHLY, "2026-01", "monthly_pnl", 1000.0)
        aggregator.store.set_context(ContextLayer.L4_MONTHLY, "2026-02", "monthly_pnl", 2000.0)
        aggregator.store.set_context(ContextLayer.L4_MONTHLY, "2026-03", "monthly_pnl", 1500.0)

        # Aggregate
        aggregator.aggregate_quarterly_from_monthly(quarter)

        # Verify L3 contexts
        store = aggregator.store
        quarterly_pnl = store.get_context(ContextLayer.L3_QUARTERLY, quarter, "quarterly_pnl")
        assert quarterly_pnl == 4500.0

    def test_aggregate_annual_from_quarterly(self, aggregator: ContextAggregator) -> None:
        """Test aggregating annual metrics from quarterly."""
        year = "2026"

        # Set quarterly contexts for all 4 quarters
        aggregator.store.set_context(ContextLayer.L3_QUARTERLY, "2026-Q1", "quarterly_pnl", 4500.0)
        aggregator.store.set_context(ContextLayer.L3_QUARTERLY, "2026-Q2", "quarterly_pnl", 5000.0)
        aggregator.store.set_context(ContextLayer.L3_QUARTERLY, "2026-Q3", "quarterly_pnl", 4800.0)
        aggregator.store.set_context(ContextLayer.L3_QUARTERLY, "2026-Q4", "quarterly_pnl", 5200.0)

        # Aggregate
        aggregator.aggregate_annual_from_quarterly(year)

        # Verify L2 contexts
        store = aggregator.store
        annual_pnl = store.get_context(ContextLayer.L2_ANNUAL, year, "annual_pnl")
        assert annual_pnl == 19500.0

    def test_aggregate_legacy_from_annual(self, aggregator: ContextAggregator) -> None:
        """Test aggregating legacy metrics from all annual data."""
        # Set annual contexts for multiple years
        aggregator.store.set_context(ContextLayer.L2_ANNUAL, "2024", "annual_pnl", 10000.0)
        aggregator.store.set_context(ContextLayer.L2_ANNUAL, "2025", "annual_pnl", 15000.0)
        aggregator.store.set_context(ContextLayer.L2_ANNUAL, "2026", "annual_pnl", 20000.0)

        # Aggregate
        aggregator.aggregate_legacy_from_annual()

        # Verify L1 contexts
        store = aggregator.store
        total_pnl = store.get_context(ContextLayer.L1_LEGACY, "LEGACY", "total_pnl")
        years_traded = store.get_context(ContextLayer.L1_LEGACY, "LEGACY", "years_traded")
        avg_annual_pnl = store.get_context(ContextLayer.L1_LEGACY, "LEGACY", "avg_annual_pnl")

        assert total_pnl == 45000.0
        assert years_traded == 3
        assert avg_annual_pnl == 15000.0

    def test_run_all_aggregations(
        self, aggregator: ContextAggregator, db_conn: sqlite3.Connection
    ) -> None:
        """Test running all aggregations from L7 to L1."""
        date = datetime.now(UTC).date().isoformat()

        # Create sample trades
        log_trade(db_conn, "005930", "BUY", 85, "Good signal", quantity=10, price=70000, pnl=1000)

        # Set timestamp
        db_conn.execute(f"UPDATE trades SET timestamp = '{date}T10:00:00+00:00'")
        db_conn.commit()

        # Run all aggregations
        aggregator.run_all_aggregations()

        # Verify data exists in each layer
        store = aggregator.store
        assert store.get_context(ContextLayer.L6_DAILY, date, "total_pnl_KR") == 1000.0
        from datetime import date as date_cls
        trade_date = date_cls.fromisoformat(date)
        iso_year, iso_week, _ = trade_date.isocalendar()
        trade_week = f"{iso_year}-W{iso_week:02d}"
        assert store.get_context(ContextLayer.L5_WEEKLY, trade_week, "weekly_pnl_KR") is not None
        trade_month = f"{trade_date.year}-{trade_date.month:02d}"
        trade_quarter = f"{trade_date.year}-Q{(trade_date.month - 1) // 3 + 1}"
        trade_year = str(trade_date.year)
        assert store.get_context(ContextLayer.L4_MONTHLY, trade_month, "monthly_pnl") == 1000.0
        assert store.get_context(ContextLayer.L3_QUARTERLY, trade_quarter, "quarterly_pnl") == 1000.0
        assert store.get_context(ContextLayer.L2_ANNUAL, trade_year, "annual_pnl") == 1000.0


class TestLayerMetadata:
    """Test suite for layer metadata configuration."""

    def test_all_layers_have_metadata(self) -> None:
        """Test that all 7 layers have metadata defined."""
        assert len(LAYER_CONFIG) == 7

        for layer in ContextLayer:
            assert layer in LAYER_CONFIG

    def test_layer_retention_policies(self) -> None:
        """Test layer retention policies are correctly configured."""
        # L1 should have no retention limit
        assert LAYER_CONFIG[ContextLayer.L1_LEGACY].retention_days is None

        # L7 should have the shortest retention (7 days)
        assert LAYER_CONFIG[ContextLayer.L7_REALTIME].retention_days == 7

        # L2 should have a long retention (10 years)
        assert LAYER_CONFIG[ContextLayer.L2_ANNUAL].retention_days == 365 * 10

    def test_layer_aggregation_chain(self) -> None:
        """Test that the aggregation chain is properly configured."""
        # L7 has no source (leaf layer)
        assert LAYER_CONFIG[ContextLayer.L7_REALTIME].aggregation_source is None

        # L6 aggregates from L7
        assert LAYER_CONFIG[ContextLayer.L6_DAILY].aggregation_source == ContextLayer.L7_REALTIME

        # L5 aggregates from L6
        assert LAYER_CONFIG[ContextLayer.L5_WEEKLY].aggregation_source == ContextLayer.L6_DAILY

        # L4 aggregates from L5
        assert LAYER_CONFIG[ContextLayer.L4_MONTHLY].aggregation_source == ContextLayer.L5_WEEKLY

        # L3 aggregates from L4
        assert LAYER_CONFIG[ContextLayer.L3_QUARTERLY].aggregation_source == ContextLayer.L4_MONTHLY

        # L2 aggregates from L3
        assert LAYER_CONFIG[ContextLayer.L2_ANNUAL].aggregation_source == ContextLayer.L3_QUARTERLY

        # L1 aggregates from L2
        assert LAYER_CONFIG[ContextLayer.L1_LEGACY].aggregation_source == ContextLayer.L2_ANNUAL
