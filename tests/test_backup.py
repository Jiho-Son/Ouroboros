"""Tests for backup and disaster recovery system."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.backup.exporter import BackupExporter, ExportFormat
from src.backup.health_monitor import HealthMonitor, HealthStatus
from src.backup.scheduler import BackupPolicy, BackupScheduler


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create a temporary test database."""
    db_path = tmp_path / "test_trades.db"

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create trades table
    cursor.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            action TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            confidence INTEGER NOT NULL,
            rationale TEXT,
            pnl REAL DEFAULT 0.0
        )
    """)

    # Insert test data
    test_trades = [
        ("2024-01-01T10:00:00Z", "005930", "BUY", 10, 70000.0, 85, "Test buy", 0.0),
        ("2024-01-01T11:00:00Z", "005930", "SELL", 10, 71000.0, 90, "Test sell", 10000.0),
        ("2024-01-02T10:00:00Z", "AAPL", "BUY", 5, 180.0, 88, "Tech buy", 0.0),
    ]

    cursor.executemany(
        """
        INSERT INTO trades (timestamp, stock_code, action, quantity, price, confidence, rationale, pnl)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        test_trades,
    )

    conn.commit()
    conn.close()

    return db_path


class TestBackupExporter:
    """Test BackupExporter functionality."""

    def test_exporter_init(self, temp_db: Path) -> None:
        """Test exporter initialization."""
        exporter = BackupExporter(str(temp_db))
        assert exporter.db_path == str(temp_db)

    def test_export_json(self, temp_db: Path, tmp_path: Path) -> None:
        """Test JSON export."""
        exporter = BackupExporter(str(temp_db))
        output_dir = tmp_path / "exports"

        results = exporter.export_all(
            output_dir, formats=[ExportFormat.JSON], compress=False
        )

        assert ExportFormat.JSON in results
        assert results[ExportFormat.JSON].exists()
        assert results[ExportFormat.JSON].suffix == ".json"

    def test_export_json_compressed(self, temp_db: Path, tmp_path: Path) -> None:
        """Test compressed JSON export."""
        exporter = BackupExporter(str(temp_db))
        output_dir = tmp_path / "exports"

        results = exporter.export_all(
            output_dir, formats=[ExportFormat.JSON], compress=True
        )

        assert ExportFormat.JSON in results
        assert results[ExportFormat.JSON].suffix == ".gz"

    def test_export_csv(self, temp_db: Path, tmp_path: Path) -> None:
        """Test CSV export."""
        exporter = BackupExporter(str(temp_db))
        output_dir = tmp_path / "exports"

        results = exporter.export_all(
            output_dir, formats=[ExportFormat.CSV], compress=False
        )

        assert ExportFormat.CSV in results
        assert results[ExportFormat.CSV].exists()

        # Verify CSV content
        with open(results[ExportFormat.CSV], "r") as f:
            lines = f.readlines()
            assert len(lines) == 4  # Header + 3 rows

    def test_export_all_formats(self, temp_db: Path, tmp_path: Path) -> None:
        """Test exporting all formats."""
        exporter = BackupExporter(str(temp_db))
        output_dir = tmp_path / "exports"

        # Skip Parquet if pyarrow not available
        try:
            import pyarrow  # noqa: F401

            formats = [ExportFormat.JSON, ExportFormat.CSV, ExportFormat.PARQUET]
        except ImportError:
            formats = [ExportFormat.JSON, ExportFormat.CSV]

        results = exporter.export_all(output_dir, formats=formats, compress=False)

        for fmt in formats:
            assert fmt in results
            assert results[fmt].exists()

    def test_incremental_export(self, temp_db: Path, tmp_path: Path) -> None:
        """Test incremental export."""
        exporter = BackupExporter(str(temp_db))
        output_dir = tmp_path / "exports"

        # Export only trades after Jan 2
        cutoff = datetime(2024, 1, 2, tzinfo=UTC)
        results = exporter.export_all(
            output_dir,
            formats=[ExportFormat.JSON],
            compress=False,
            incremental_since=cutoff,
        )

        # Should only have 1 trade (AAPL on Jan 2)
        import json

        with open(results[ExportFormat.JSON], "r") as f:
            data = json.load(f)
            assert data["record_count"] == 1
            assert data["trades"][0]["stock_code"] == "AAPL"

    def test_get_export_stats(self, temp_db: Path) -> None:
        """Test export statistics."""
        exporter = BackupExporter(str(temp_db))
        stats = exporter.get_export_stats()

        assert stats["total_trades"] == 3
        assert "date_range" in stats
        assert "db_size_bytes" in stats


class TestBackupScheduler:
    """Test BackupScheduler functionality."""

    def test_scheduler_init(self, temp_db: Path, tmp_path: Path) -> None:
        """Test scheduler initialization."""
        backup_dir = tmp_path / "backups"
        scheduler = BackupScheduler(str(temp_db), backup_dir)

        assert scheduler.db_path == temp_db
        assert (backup_dir / "daily").exists()
        assert (backup_dir / "weekly").exists()
        assert (backup_dir / "monthly").exists()

    def test_create_daily_backup(self, temp_db: Path, tmp_path: Path) -> None:
        """Test daily backup creation."""
        backup_dir = tmp_path / "backups"
        scheduler = BackupScheduler(str(temp_db), backup_dir)

        metadata = scheduler.create_backup(BackupPolicy.DAILY, verify=True)

        assert metadata.policy == BackupPolicy.DAILY
        assert metadata.file_path.exists()
        assert metadata.size_bytes > 0
        assert metadata.checksum is not None

    def test_create_weekly_backup(self, temp_db: Path, tmp_path: Path) -> None:
        """Test weekly backup creation."""
        backup_dir = tmp_path / "backups"
        scheduler = BackupScheduler(str(temp_db), backup_dir)

        metadata = scheduler.create_backup(BackupPolicy.WEEKLY, verify=False)

        assert metadata.policy == BackupPolicy.WEEKLY
        assert metadata.file_path.exists()
        assert metadata.checksum is None  # verify=False

    def test_list_backups(self, temp_db: Path, tmp_path: Path) -> None:
        """Test listing backups."""
        backup_dir = tmp_path / "backups"
        scheduler = BackupScheduler(str(temp_db), backup_dir)

        scheduler.create_backup(BackupPolicy.DAILY)
        scheduler.create_backup(BackupPolicy.WEEKLY)

        backups = scheduler.list_backups()
        assert len(backups) == 2

        daily_backups = scheduler.list_backups(BackupPolicy.DAILY)
        assert len(daily_backups) == 1
        assert daily_backups[0].policy == BackupPolicy.DAILY

    def test_cleanup_old_backups(self, temp_db: Path, tmp_path: Path) -> None:
        """Test cleanup of old backups."""
        backup_dir = tmp_path / "backups"
        scheduler = BackupScheduler(str(temp_db), backup_dir, daily_retention_days=0)

        # Create a backup
        scheduler.create_backup(BackupPolicy.DAILY)

        # Cleanup should remove it (0 day retention)
        removed = scheduler.cleanup_old_backups()
        assert removed[BackupPolicy.DAILY] >= 1

    def test_backup_stats(self, temp_db: Path, tmp_path: Path) -> None:
        """Test backup statistics."""
        backup_dir = tmp_path / "backups"
        scheduler = BackupScheduler(str(temp_db), backup_dir)

        scheduler.create_backup(BackupPolicy.DAILY)
        scheduler.create_backup(BackupPolicy.MONTHLY)

        stats = scheduler.get_backup_stats()

        assert stats["daily"]["count"] == 1
        assert stats["monthly"]["count"] == 1
        assert stats["daily"]["total_size_bytes"] > 0

    def test_restore_backup(self, temp_db: Path, tmp_path: Path) -> None:
        """Test backup restoration."""
        backup_dir = tmp_path / "backups"
        scheduler = BackupScheduler(str(temp_db), backup_dir)

        # Create backup
        metadata = scheduler.create_backup(BackupPolicy.DAILY)

        # Modify database
        conn = sqlite3.connect(str(temp_db))
        conn.execute("DELETE FROM trades")
        conn.commit()
        conn.close()

        # Restore
        scheduler.restore_backup(metadata, verify=True)

        # Verify restoration
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.execute("SELECT COUNT(*) FROM trades")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 3  # Original 3 trades restored


class TestHealthMonitor:
    """Test HealthMonitor functionality."""

    def test_monitor_init(self, temp_db: Path, tmp_path: Path) -> None:
        """Test monitor initialization."""
        backup_dir = tmp_path / "backups"
        monitor = HealthMonitor(str(temp_db), backup_dir)

        assert monitor.db_path == temp_db

    def test_check_database_health_ok(self, temp_db: Path, tmp_path: Path) -> None:
        """Test database health check (healthy)."""
        monitor = HealthMonitor(str(temp_db), tmp_path / "backups")
        result = monitor.check_database_health()

        assert result.status == HealthStatus.HEALTHY
        assert "healthy" in result.message.lower()
        assert result.details is not None
        assert result.details["trade_count"] == 3

    def test_check_database_health_missing(self, tmp_path: Path) -> None:
        """Test database health check (missing file)."""
        non_existent = tmp_path / "missing.db"
        monitor = HealthMonitor(str(non_existent), tmp_path / "backups")
        result = monitor.check_database_health()

        assert result.status == HealthStatus.UNHEALTHY
        assert "not found" in result.message.lower()

    def test_check_disk_space(self, temp_db: Path, tmp_path: Path) -> None:
        """Test disk space check."""
        monitor = HealthMonitor(str(temp_db), tmp_path, min_disk_space_gb=0.001)
        result = monitor.check_disk_space()

        # Should be healthy with minimal requirement
        assert result.status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED]
        assert result.details is not None
        assert "free_gb" in result.details

    def test_check_backup_recency_no_backups(self, temp_db: Path, tmp_path: Path) -> None:
        """Test backup recency check (no backups)."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "daily").mkdir()

        monitor = HealthMonitor(str(temp_db), backup_dir)
        result = monitor.check_backup_recency()

        assert result.status == HealthStatus.UNHEALTHY
        assert "no" in result.message.lower()

    def test_check_backup_recency_recent(self, temp_db: Path, tmp_path: Path) -> None:
        """Test backup recency check (recent backup)."""
        backup_dir = tmp_path / "backups"
        scheduler = BackupScheduler(str(temp_db), backup_dir)
        scheduler.create_backup(BackupPolicy.DAILY)

        monitor = HealthMonitor(str(temp_db), backup_dir)
        result = monitor.check_backup_recency()

        assert result.status == HealthStatus.HEALTHY
        assert "recent" in result.message.lower()

    def test_run_all_checks(self, temp_db: Path, tmp_path: Path) -> None:
        """Test running all health checks."""
        backup_dir = tmp_path / "backups"
        scheduler = BackupScheduler(str(temp_db), backup_dir)
        scheduler.create_backup(BackupPolicy.DAILY)

        monitor = HealthMonitor(str(temp_db), backup_dir, min_disk_space_gb=0.001)
        checks = monitor.run_all_checks()

        assert "database" in checks
        assert "disk_space" in checks
        assert "backup_recency" in checks
        assert checks["database"].status == HealthStatus.HEALTHY

    def test_get_overall_status(self, temp_db: Path, tmp_path: Path) -> None:
        """Test overall health status."""
        backup_dir = tmp_path / "backups"
        scheduler = BackupScheduler(str(temp_db), backup_dir)
        scheduler.create_backup(BackupPolicy.DAILY)

        monitor = HealthMonitor(str(temp_db), backup_dir, min_disk_space_gb=0.001)
        status = monitor.get_overall_status()

        assert status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED]

    def test_get_health_report(self, temp_db: Path, tmp_path: Path) -> None:
        """Test health report generation."""
        backup_dir = tmp_path / "backups"
        scheduler = BackupScheduler(str(temp_db), backup_dir)
        scheduler.create_backup(BackupPolicy.DAILY)

        monitor = HealthMonitor(str(temp_db), backup_dir, min_disk_space_gb=0.001)
        report = monitor.get_health_report()

        assert "overall_status" in report
        assert "timestamp" in report
        assert "checks" in report
        assert len(report["checks"]) == 3
