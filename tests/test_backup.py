"""Tests for backup and disaster recovery system."""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        INSERT INTO trades (
            timestamp, stock_code, action, quantity, price, confidence, rationale, pnl
        )
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

        results = exporter.export_all(output_dir, formats=[ExportFormat.JSON], compress=False)

        assert ExportFormat.JSON in results
        assert results[ExportFormat.JSON].exists()
        assert results[ExportFormat.JSON].suffix == ".json"

    def test_export_json_compressed(self, temp_db: Path, tmp_path: Path) -> None:
        """Test compressed JSON export."""
        exporter = BackupExporter(str(temp_db))
        output_dir = tmp_path / "exports"

        results = exporter.export_all(output_dir, formats=[ExportFormat.JSON], compress=True)

        assert ExportFormat.JSON in results
        assert results[ExportFormat.JSON].suffix == ".gz"

    def test_export_csv(self, temp_db: Path, tmp_path: Path) -> None:
        """Test CSV export."""
        exporter = BackupExporter(str(temp_db))
        output_dir = tmp_path / "exports"

        results = exporter.export_all(output_dir, formats=[ExportFormat.CSV], compress=False)

        assert ExportFormat.CSV in results
        assert results[ExportFormat.CSV].exists()

        # Verify CSV content
        with open(results[ExportFormat.CSV]) as f:
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

        with open(results[ExportFormat.JSON]) as f:
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

    def test_check_database_health_bootstraps_empty_db(self, tmp_path: Path) -> None:
        """빈 SQLite 파일도 health check 시 스키마를 bootstrap 해야 한다."""
        db_path = tmp_path / "fresh.db"
        db_path.touch()

        monitor = HealthMonitor(str(db_path), tmp_path / "backups")
        result = monitor.check_database_health()

        assert result.status == HealthStatus.HEALTHY
        assert result.details is not None
        assert result.details["trade_count"] == 0

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


# ---------------------------------------------------------------------------
# BackupExporter — additional coverage for previously uncovered branches
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """Create a temporary database with NO trade records."""
    db_path = tmp_path / "empty_trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            action TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            confidence INTEGER NOT NULL,
            rationale TEXT,
            pnl REAL DEFAULT 0.0
        )"""
    )
    conn.commit()
    conn.close()
    return db_path


class TestBackupExporterAdditional:
    """Cover branches missed in the original TestBackupExporter suite."""

    def test_export_all_default_formats(self, temp_db: Path, tmp_path: Path) -> None:
        """export_all with formats=None must default to JSON+CSV+Parquet path."""
        exporter = BackupExporter(str(temp_db))
        # formats=None triggers the default list assignment (line 62)
        results = exporter.export_all(tmp_path / "out", formats=None, compress=False)
        # JSON and CSV must always succeed; Parquet needs pyarrow
        assert ExportFormat.JSON in results
        assert ExportFormat.CSV in results

    def test_export_all_logs_error_on_failure(self, temp_db: Path, tmp_path: Path) -> None:
        """export_all must log an error and continue when one format fails."""
        exporter = BackupExporter(str(temp_db))
        # Patch _export_format to raise on JSON, succeed on CSV
        original = exporter._export_format

        def failing_export(fmt, *args, **kwargs):  # type: ignore[no-untyped-def]
            if fmt == ExportFormat.JSON:
                raise RuntimeError("simulated failure")
            return original(fmt, *args, **kwargs)

        exporter._export_format = failing_export  # type: ignore[method-assign]
        results = exporter.export_all(
            tmp_path / "out",
            formats=[ExportFormat.JSON, ExportFormat.CSV],
            compress=False,
        )
        # JSON failed → not in results; CSV succeeded → in results
        assert ExportFormat.JSON not in results
        assert ExportFormat.CSV in results

    def test_export_csv_empty_trades_no_compress(self, empty_db: Path, tmp_path: Path) -> None:
        """CSV export with no trades and compress=False must write header row only."""
        exporter = BackupExporter(str(empty_db))
        results = exporter.export_all(
            tmp_path / "out",
            formats=[ExportFormat.CSV],
            compress=False,
        )
        assert ExportFormat.CSV in results
        out = results[ExportFormat.CSV]
        assert out.exists()
        content = out.read_text()
        assert "timestamp" in content

    def test_export_csv_empty_trades_compressed(self, empty_db: Path, tmp_path: Path) -> None:
        """CSV export with no trades and compress=True must write gzipped header."""
        import gzip

        exporter = BackupExporter(str(empty_db))
        results = exporter.export_all(
            tmp_path / "out",
            formats=[ExportFormat.CSV],
            compress=True,
        )
        assert ExportFormat.CSV in results
        out = results[ExportFormat.CSV]
        assert out.suffix == ".gz"
        with gzip.open(out, "rt", encoding="utf-8") as f:
            content = f.read()
        assert "timestamp" in content

    def test_export_csv_with_data_compressed(self, temp_db: Path, tmp_path: Path) -> None:
        """CSV export with data and compress=True must write gzipped rows."""
        import gzip

        exporter = BackupExporter(str(temp_db))
        results = exporter.export_all(
            tmp_path / "out",
            formats=[ExportFormat.CSV],
            compress=True,
        )
        assert ExportFormat.CSV in results
        out = results[ExportFormat.CSV]
        with gzip.open(out, "rt", encoding="utf-8") as f:
            lines = f.readlines()
        # Header + 3 data rows
        assert len(lines) == 4

    def test_export_parquet_raises_import_error_without_pyarrow(
        self, temp_db: Path, tmp_path: Path
    ) -> None:
        """Parquet export must raise ImportError when pyarrow is not installed."""
        exporter = BackupExporter(str(temp_db))
        with patch.dict(sys.modules, {"pyarrow": None, "pyarrow.parquet": None}):
            try:
                import pyarrow  # noqa: F401

                pytest.skip("pyarrow is installed; cannot test ImportError path")
            except ImportError:
                pass
            results = exporter.export_all(
                tmp_path / "out",
                formats=[ExportFormat.PARQUET],
                compress=False,
            )
            # Parquet export fails gracefully; result dict should not contain it
            assert ExportFormat.PARQUET not in results


# ---------------------------------------------------------------------------
# CloudStorage — mocked boto3 tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_boto3_module():
    """Inject a fake boto3 into sys.modules for the duration of the test."""
    mock = MagicMock()
    with patch.dict(sys.modules, {"boto3": mock}):
        yield mock


@pytest.fixture
def s3_config():
    """Minimal S3Config for tests."""
    from src.backup.cloud_storage import S3Config

    return S3Config(
        endpoint_url="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket_name="test-bucket",
        region="us-east-1",
    )


class TestCloudStorage:
    """Test CloudStorage using mocked boto3."""

    def test_init_creates_s3_client(self, mock_boto3_module, s3_config) -> None:
        """CloudStorage.__init__ must call boto3.client with the correct args."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        mock_boto3_module.client.assert_called_once()
        call_kwargs = mock_boto3_module.client.call_args[1]
        assert call_kwargs["aws_access_key_id"] == "minioadmin"
        assert call_kwargs["aws_secret_access_key"] == "minioadmin"
        assert storage.config == s3_config

    def test_init_raises_if_boto3_missing(self, s3_config) -> None:
        """CloudStorage.__init__ must raise ImportError when boto3 is absent."""
        with patch.dict(sys.modules, {"boto3": None}):  # type: ignore[dict-item]
            with pytest.raises((ImportError, TypeError)):
                # Re-import to trigger the try/except inside __init__
                import importlib

                import src.backup.cloud_storage as m

                importlib.reload(m)
                m.CloudStorage(s3_config)

    def test_upload_file_success(self, mock_boto3_module, s3_config, tmp_path: Path) -> None:
        """upload_file must call client.upload_file and return the object key."""
        from src.backup.cloud_storage import CloudStorage

        test_file = tmp_path / "backup.json.gz"
        test_file.write_bytes(b"data")

        storage = CloudStorage(s3_config)
        key = storage.upload_file(test_file, object_key="backups/backup.json.gz")

        assert key == "backups/backup.json.gz"
        storage.client.upload_file.assert_called_once()

    def test_upload_file_default_key(self, mock_boto3_module, s3_config, tmp_path: Path) -> None:
        """upload_file without object_key must use the filename as key."""
        from src.backup.cloud_storage import CloudStorage

        test_file = tmp_path / "myfile.gz"
        test_file.write_bytes(b"data")

        storage = CloudStorage(s3_config)
        key = storage.upload_file(test_file)

        assert key == "myfile.gz"

    def test_upload_file_not_found(self, mock_boto3_module, s3_config, tmp_path: Path) -> None:
        """upload_file must raise FileNotFoundError for missing files."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        with pytest.raises(FileNotFoundError):
            storage.upload_file(tmp_path / "nonexistent.gz")

    def test_upload_file_propagates_client_error(
        self, mock_boto3_module, s3_config, tmp_path: Path
    ) -> None:
        """upload_file must re-raise exceptions from the boto3 client."""
        from src.backup.cloud_storage import CloudStorage

        test_file = tmp_path / "backup.gz"
        test_file.write_bytes(b"data")

        storage = CloudStorage(s3_config)
        storage.client.upload_file.side_effect = RuntimeError("network error")

        with pytest.raises(RuntimeError, match="network error"):
            storage.upload_file(test_file)

    def test_download_file_success(self, mock_boto3_module, s3_config, tmp_path: Path) -> None:
        """download_file must call client.download_file and return local path."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        dest = tmp_path / "downloads" / "backup.gz"

        result = storage.download_file("backups/backup.gz", dest)

        assert result == dest
        storage.client.download_file.assert_called_once()

    def test_download_file_propagates_error(
        self, mock_boto3_module, s3_config, tmp_path: Path
    ) -> None:
        """download_file must re-raise exceptions from the boto3 client."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        storage.client.download_file.side_effect = RuntimeError("timeout")

        with pytest.raises(RuntimeError, match="timeout"):
            storage.download_file("key", tmp_path / "dest.gz")

    def test_list_files_returns_objects(self, mock_boto3_module, s3_config) -> None:
        """list_files must return parsed file metadata from S3 response."""

        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        storage.client.list_objects_v2.return_value = {
            "Contents": [
                {
                    "Key": "backups/a.gz",
                    "Size": 1024,
                    "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
                    "ETag": '"abc123"',
                }
            ]
        }

        files = storage.list_files(prefix="backups/")
        assert len(files) == 1
        assert files[0]["key"] == "backups/a.gz"
        assert files[0]["size_bytes"] == 1024

    def test_list_files_empty_bucket(self, mock_boto3_module, s3_config) -> None:
        """list_files must return empty list when bucket has no objects."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        storage.client.list_objects_v2.return_value = {}

        files = storage.list_files()
        assert files == []

    def test_list_files_propagates_error(self, mock_boto3_module, s3_config) -> None:
        """list_files must re-raise exceptions from the boto3 client."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        storage.client.list_objects_v2.side_effect = RuntimeError("auth error")

        with pytest.raises(RuntimeError):
            storage.list_files()

    def test_delete_file_success(self, mock_boto3_module, s3_config) -> None:
        """delete_file must call client.delete_object with the correct key."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        storage.delete_file("backups/old.gz")
        storage.client.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key="backups/old.gz"
        )

    def test_delete_file_propagates_error(self, mock_boto3_module, s3_config) -> None:
        """delete_file must re-raise exceptions from the boto3 client."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        storage.client.delete_object.side_effect = RuntimeError("permission denied")

        with pytest.raises(RuntimeError):
            storage.delete_file("backups/old.gz")

    def test_get_storage_stats_success(self, mock_boto3_module, s3_config) -> None:
        """get_storage_stats must aggregate file sizes correctly."""

        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        storage.client.list_objects_v2.return_value = {
            "Contents": [
                {
                    "Key": "a.gz",
                    "Size": 1024 * 1024,
                    "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
                    "ETag": '"x"',
                },
                {
                    "Key": "b.gz",
                    "Size": 1024 * 1024,
                    "LastModified": datetime(2026, 1, 2, tzinfo=UTC),
                    "ETag": '"y"',
                },
            ]
        }

        stats = storage.get_storage_stats()
        assert stats["total_files"] == 2
        assert stats["total_size_bytes"] == 2 * 1024 * 1024
        assert stats["total_size_mb"] == pytest.approx(2.0)

    def test_get_storage_stats_on_error(self, mock_boto3_module, s3_config) -> None:
        """get_storage_stats must return error dict without raising on failure."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        storage.client.list_objects_v2.side_effect = RuntimeError("no connection")

        stats = storage.get_storage_stats()
        assert "error" in stats
        assert stats["total_files"] == 0

    def test_verify_connection_success(self, mock_boto3_module, s3_config) -> None:
        """verify_connection must return True when head_bucket succeeds."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        result = storage.verify_connection()
        assert result is True

    def test_verify_connection_failure(self, mock_boto3_module, s3_config) -> None:
        """verify_connection must return False when head_bucket raises."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        storage.client.head_bucket.side_effect = RuntimeError("no such bucket")

        result = storage.verify_connection()
        assert result is False

    def test_enable_versioning(self, mock_boto3_module, s3_config) -> None:
        """enable_versioning must call put_bucket_versioning."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        storage.enable_versioning()
        storage.client.put_bucket_versioning.assert_called_once()

    def test_enable_versioning_propagates_error(self, mock_boto3_module, s3_config) -> None:
        """enable_versioning must re-raise exceptions from the boto3 client."""
        from src.backup.cloud_storage import CloudStorage

        storage = CloudStorage(s3_config)
        storage.client.put_bucket_versioning.side_effect = RuntimeError("denied")

        with pytest.raises(RuntimeError):
            storage.enable_versioning()
