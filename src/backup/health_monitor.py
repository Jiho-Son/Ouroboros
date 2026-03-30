"""Health monitoring for backup system.

Checks:
- Database accessibility and integrity
- Disk space availability
- Backup success/failure tracking
- Self-healing capabilities
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from src.db import init_db

logger = logging.getLogger(__name__)


class HealthStatus(StrEnum):
    """Health check status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    status: HealthStatus
    message: str
    details: dict[str, Any] | None = None
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now(UTC)


class HealthMonitor:
    """Monitor system health and backup status."""

    def __init__(
        self,
        db_path: str,
        backup_dir: Path,
        min_disk_space_gb: float = 10.0,
        max_backup_age_hours: int = 25,  # Daily backups should be < 25 hours old
    ) -> None:
        """Initialize health monitor.

        Args:
            db_path: Path to SQLite database
            backup_dir: Backup directory
            min_disk_space_gb: Minimum required disk space in GB
            max_backup_age_hours: Maximum acceptable backup age in hours
        """
        self.db_path = Path(db_path)
        self.backup_dir = backup_dir
        self.min_disk_space_bytes = int(min_disk_space_gb * 1024 * 1024 * 1024)
        self.max_backup_age = timedelta(hours=max_backup_age_hours)

    def check_database_health(self) -> HealthCheckResult:
        """Check database accessibility and integrity.

        Returns:
            HealthCheckResult
        """
        # Check if database exists
        if not self.db_path.exists():
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Database not found: {self.db_path}",
            )

        # Check if database is accessible
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Run integrity check
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()[0]

            if result != "ok":
                conn.close()
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Database integrity check failed: {result}",
                )

            # Get database size
            cursor.execute(
                "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"
            )
            db_size = cursor.fetchone()[0]

            # Get row counts
            try:
                cursor.execute("SELECT COUNT(*) FROM trades")
            except sqlite3.OperationalError as exc:
                if "no such table: trades" not in str(exc):
                    raise
                conn.close()
                # Bootstrap only when the shared schema has not been created yet.
                conn = init_db(str(self.db_path))
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"
                )
                db_size = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM trades")
            trade_count = cursor.fetchone()[0]

            conn.close()

            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                message="Database is healthy",
                details={
                    "size_bytes": db_size,
                    "size_mb": db_size / 1024 / 1024,
                    "trade_count": trade_count,
                },
            )

        except sqlite3.Error as exc:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Database access error: {exc}",
            )

    def check_disk_space(self) -> HealthCheckResult:
        """Check available disk space.

        Returns:
            HealthCheckResult
        """
        try:
            stat = shutil.disk_usage(self.backup_dir)

            free_gb = stat.free / 1024 / 1024 / 1024
            total_gb = stat.total / 1024 / 1024 / 1024
            used_percent = (stat.used / stat.total) * 100

            if stat.free < self.min_disk_space_bytes:
                min_disk_gb = self.min_disk_space_bytes / 1024 / 1024 / 1024
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=(
                        f"Low disk space: {free_gb:.2f} GB free "
                        f"(minimum: {min_disk_gb:.2f} GB)"
                    ),
                    details={
                        "free_gb": free_gb,
                        "total_gb": total_gb,
                        "used_percent": used_percent,
                    },
                )
            elif stat.free < self.min_disk_space_bytes * 2:
                return HealthCheckResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Disk space low: {free_gb:.2f} GB free",
                    details={
                        "free_gb": free_gb,
                        "total_gb": total_gb,
                        "used_percent": used_percent,
                    },
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message=f"Disk space healthy: {free_gb:.2f} GB free",
                    details={
                        "free_gb": free_gb,
                        "total_gb": total_gb,
                        "used_percent": used_percent,
                    },
                )

        except Exception as exc:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Failed to check disk space: {exc}",
            )

    def check_backup_recency(self) -> HealthCheckResult:
        """Check if backups are recent enough.

        Returns:
            HealthCheckResult
        """
        daily_dir = self.backup_dir / "daily"

        if not daily_dir.exists():
            return HealthCheckResult(
                status=HealthStatus.DEGRADED,
                message="Daily backup directory not found",
            )

        # Find most recent backup
        backups = sorted(daily_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)

        if not backups:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message="No daily backups found",
            )

        most_recent = backups[0]
        mtime = datetime.fromtimestamp(most_recent.stat().st_mtime, tz=UTC)
        age = datetime.now(UTC) - mtime

        if age > self.max_backup_age:
            return HealthCheckResult(
                status=HealthStatus.DEGRADED,
                message=f"Most recent backup is {age.total_seconds() / 3600:.1f} hours old",
                details={
                    "backup_file": most_recent.name,
                    "age_hours": age.total_seconds() / 3600,
                    "threshold_hours": self.max_backup_age.total_seconds() / 3600,
                },
            )
        else:
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                message=f"Recent backup found ({age.total_seconds() / 3600:.1f} hours old)",
                details={
                    "backup_file": most_recent.name,
                    "age_hours": age.total_seconds() / 3600,
                },
            )

    def run_all_checks(self) -> dict[str, HealthCheckResult]:
        """Run all health checks.

        Returns:
            Dictionary mapping check name to result
        """
        checks = {
            "database": self.check_database_health(),
            "disk_space": self.check_disk_space(),
            "backup_recency": self.check_backup_recency(),
        }

        # Log results
        for check_name, result in checks.items():
            if result.status == HealthStatus.UNHEALTHY:
                logger.error("[%s] %s: %s", check_name, result.status.value, result.message)
            elif result.status == HealthStatus.DEGRADED:
                logger.warning("[%s] %s: %s", check_name, result.status.value, result.message)
            else:
                logger.info("[%s] %s: %s", check_name, result.status.value, result.message)

        return checks

    def get_overall_status(self) -> HealthStatus:
        """Get overall system health status.

        Returns:
            HealthStatus (worst status from all checks)
        """
        checks = self.run_all_checks()

        # Return worst status
        if any(c.status == HealthStatus.UNHEALTHY for c in checks.values()):
            return HealthStatus.UNHEALTHY
        elif any(c.status == HealthStatus.DEGRADED for c in checks.values()):
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY

    def get_health_report(self) -> dict[str, Any]:
        """Get comprehensive health report.

        Returns:
            Dictionary with health report
        """
        checks = self.run_all_checks()
        overall = self.get_overall_status()

        return {
            "overall_status": overall.value,
            "timestamp": datetime.now(UTC).isoformat(),
            "checks": {
                name: {
                    "status": result.status.value,
                    "message": result.message,
                    "details": result.details,
                }
                for name, result in checks.items()
            },
        }
