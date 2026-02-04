"""Backup scheduler for automated database backups.

Implements backup policies:
- Daily: Keep for 30 days (hot storage)
- Weekly: Keep for 1 year (warm storage)
- Monthly: Keep forever (cold storage)
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BackupPolicy(str, Enum):
    """Backup retention policies."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass
class BackupMetadata:
    """Metadata for a backup."""

    timestamp: datetime
    policy: BackupPolicy
    file_path: Path
    size_bytes: int
    checksum: str | None = None


class BackupScheduler:
    """Manage automated database backups with retention policies."""

    def __init__(
        self,
        db_path: str,
        backup_dir: Path,
        daily_retention_days: int = 30,
        weekly_retention_days: int = 365,
    ) -> None:
        """Initialize the backup scheduler.

        Args:
            db_path: Path to SQLite database
            backup_dir: Root directory for backups
            daily_retention_days: Days to keep daily backups
            weekly_retention_days: Days to keep weekly backups
        """
        self.db_path = Path(db_path)
        self.backup_dir = backup_dir
        self.daily_retention = timedelta(days=daily_retention_days)
        self.weekly_retention = timedelta(days=weekly_retention_days)

        # Create policy-specific directories
        self.daily_dir = backup_dir / "daily"
        self.weekly_dir = backup_dir / "weekly"
        self.monthly_dir = backup_dir / "monthly"

        for d in [self.daily_dir, self.weekly_dir, self.monthly_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def create_backup(
        self, policy: BackupPolicy, verify: bool = True
    ) -> BackupMetadata:
        """Create a database backup.

        Args:
            policy: Backup policy (daily/weekly/monthly)
            verify: Whether to verify backup integrity

        Returns:
            BackupMetadata object

        Raises:
            FileNotFoundError: If database doesn't exist
            OSError: If backup fails
        """
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")

        timestamp = datetime.now(UTC)
        backup_filename = self._get_backup_filename(timestamp, policy)

        # Determine output directory
        if policy == BackupPolicy.DAILY:
            output_dir = self.daily_dir
        elif policy == BackupPolicy.WEEKLY:
            output_dir = self.weekly_dir
        else:  # MONTHLY
            output_dir = self.monthly_dir

        backup_path = output_dir / backup_filename

        # Create backup (copy database file)
        logger.info("Creating %s backup: %s", policy.value, backup_path)
        shutil.copy2(self.db_path, backup_path)

        # Get file size
        size_bytes = backup_path.stat().st_size

        # Verify backup if requested
        checksum = None
        if verify:
            checksum = self._verify_backup(backup_path)

        metadata = BackupMetadata(
            timestamp=timestamp,
            policy=policy,
            file_path=backup_path,
            size_bytes=size_bytes,
            checksum=checksum,
        )

        logger.info(
            "Backup created: %s (%.2f MB)",
            backup_path.name,
            size_bytes / 1024 / 1024,
        )

        return metadata

    def _get_backup_filename(self, timestamp: datetime, policy: BackupPolicy) -> str:
        """Generate backup filename.

        Args:
            timestamp: Backup timestamp
            policy: Backup policy

        Returns:
            Filename string
        """
        ts_str = timestamp.strftime("%Y%m%d_%H%M%S")
        return f"trade_logs_{policy.value}_{ts_str}.db"

    def _verify_backup(self, backup_path: Path) -> str:
        """Verify backup integrity using SQLite integrity check.

        Args:
            backup_path: Path to backup file

        Returns:
            Checksum string (MD5 hash)

        Raises:
            RuntimeError: If integrity check fails
        """
        import hashlib
        import sqlite3

        # Integrity check
        try:
            conn = sqlite3.connect(str(backup_path))
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()[0]
            conn.close()

            if result != "ok":
                raise RuntimeError(f"Integrity check failed: {result}")
        except sqlite3.Error as exc:
            raise RuntimeError(f"Failed to verify backup: {exc}")

        # Calculate MD5 checksum
        md5 = hashlib.md5()
        with open(backup_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5.update(chunk)

        return md5.hexdigest()

    def cleanup_old_backups(self) -> dict[BackupPolicy, int]:
        """Remove backups older than retention policies.

        Returns:
            Dictionary mapping policy to number of backups removed
        """
        now = datetime.now(UTC)
        removed_counts: dict[BackupPolicy, int] = {}

        # Daily backups: remove older than retention
        removed_counts[BackupPolicy.DAILY] = self._cleanup_directory(
            self.daily_dir, now - self.daily_retention
        )

        # Weekly backups: remove older than retention
        removed_counts[BackupPolicy.WEEKLY] = self._cleanup_directory(
            self.weekly_dir, now - self.weekly_retention
        )

        # Monthly backups: never remove (kept forever)
        removed_counts[BackupPolicy.MONTHLY] = 0

        total = sum(removed_counts.values())
        if total > 0:
            logger.info("Cleaned up %d old backup(s)", total)

        return removed_counts

    def _cleanup_directory(self, directory: Path, cutoff: datetime) -> int:
        """Remove backups older than cutoff date.

        Args:
            directory: Directory to clean
            cutoff: Remove files older than this

        Returns:
            Number of files removed
        """
        removed = 0

        for backup_file in directory.glob("*.db"):
            # Get file modification time
            mtime = datetime.fromtimestamp(backup_file.stat().st_mtime, tz=UTC)

            if mtime < cutoff:
                logger.debug("Removing old backup: %s", backup_file.name)
                backup_file.unlink()
                removed += 1

        return removed

    def list_backups(
        self, policy: BackupPolicy | None = None
    ) -> list[BackupMetadata]:
        """List available backups.

        Args:
            policy: Filter by policy (None for all)

        Returns:
            List of BackupMetadata objects
        """
        backups: list[BackupMetadata] = []

        policies_to_check = (
            [policy] if policy else [BackupPolicy.DAILY, BackupPolicy.WEEKLY, BackupPolicy.MONTHLY]
        )

        for pol in policies_to_check:
            if pol == BackupPolicy.DAILY:
                directory = self.daily_dir
            elif pol == BackupPolicy.WEEKLY:
                directory = self.weekly_dir
            else:
                directory = self.monthly_dir

            for backup_file in sorted(directory.glob("*.db")):
                mtime = datetime.fromtimestamp(backup_file.stat().st_mtime, tz=UTC)
                size = backup_file.stat().st_size

                backups.append(
                    BackupMetadata(
                        timestamp=mtime,
                        policy=pol,
                        file_path=backup_file,
                        size_bytes=size,
                    )
                )

        # Sort by timestamp (newest first)
        backups.sort(key=lambda b: b.timestamp, reverse=True)

        return backups

    def get_backup_stats(self) -> dict[str, Any]:
        """Get backup statistics.

        Returns:
            Dictionary with backup stats
        """
        stats: dict[str, Any] = {}

        for policy in BackupPolicy:
            if policy == BackupPolicy.DAILY:
                directory = self.daily_dir
            elif policy == BackupPolicy.WEEKLY:
                directory = self.weekly_dir
            else:
                directory = self.monthly_dir

            backups = list(directory.glob("*.db"))
            total_size = sum(b.stat().st_size for b in backups)

            stats[policy.value] = {
                "count": len(backups),
                "total_size_bytes": total_size,
                "total_size_mb": total_size / 1024 / 1024,
            }

        return stats

    def restore_backup(self, backup_metadata: BackupMetadata, verify: bool = True) -> None:
        """Restore database from backup.

        Args:
            backup_metadata: Backup to restore
            verify: Whether to verify restored database

        Raises:
            FileNotFoundError: If backup file doesn't exist
            RuntimeError: If verification fails
        """
        if not backup_metadata.file_path.exists():
            raise FileNotFoundError(f"Backup not found: {backup_metadata.file_path}")

        # Create backup of current database
        if self.db_path.exists():
            backup_current = self.db_path.with_suffix(".db.before_restore")
            logger.info("Backing up current database to: %s", backup_current)
            shutil.copy2(self.db_path, backup_current)

        # Restore backup
        logger.info("Restoring backup: %s", backup_metadata.file_path.name)
        shutil.copy2(backup_metadata.file_path, self.db_path)

        # Verify restored database
        if verify:
            try:
                self._verify_backup(self.db_path)
                logger.info("Backup restored and verified successfully")
            except RuntimeError as exc:
                # Restore failed, revert to backup
                if backup_current.exists():
                    logger.error("Restore verification failed, reverting: %s", exc)
                    shutil.copy2(backup_current, self.db_path)
                raise
