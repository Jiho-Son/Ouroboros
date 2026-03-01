"""Multi-format database exporter for backups.

Supports JSON, CSV, and Parquet formats for different use cases:
- JSON: Human-readable, easy to inspect
- CSV: Analysis tools (Excel, pandas)
- Parquet: Big data tools (Spark, DuckDB)
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ExportFormat(StrEnum):
    """Supported export formats."""

    JSON = "json"
    CSV = "csv"
    PARQUET = "parquet"


class BackupExporter:
    """Export database to multiple formats."""

    def __init__(self, db_path: str) -> None:
        """Initialize the exporter.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path

    def export_all(
        self,
        output_dir: Path,
        formats: list[ExportFormat] | None = None,
        compress: bool = True,
        incremental_since: datetime | None = None,
    ) -> dict[ExportFormat, Path]:
        """Export database to multiple formats.

        Args:
            output_dir: Directory to write export files
            formats: List of formats to export (default: all)
            compress: Whether to gzip compress exports
            incremental_since: Only export records after this timestamp

        Returns:
            Dictionary mapping format to output file path
        """
        if formats is None:
            formats = [ExportFormat.JSON, ExportFormat.CSV, ExportFormat.PARQUET]

        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

        results: dict[ExportFormat, Path] = {}

        for fmt in formats:
            try:
                output_file = self._export_format(
                    fmt, output_dir, timestamp, compress, incremental_since
                )
                results[fmt] = output_file
                logger.info("Exported to %s: %s", fmt.value, output_file)
            except Exception as exc:
                logger.error("Failed to export to %s: %s", fmt.value, exc)

        return results

    def _export_format(
        self,
        fmt: ExportFormat,
        output_dir: Path,
        timestamp: str,
        compress: bool,
        incremental_since: datetime | None,
    ) -> Path:
        """Export to a specific format.

        Args:
            fmt: Export format
            output_dir: Output directory
            timestamp: Timestamp string for filename
            compress: Whether to compress
            incremental_since: Incremental export cutoff

        Returns:
            Path to output file
        """
        if fmt == ExportFormat.JSON:
            return self._export_json(output_dir, timestamp, compress, incremental_since)
        elif fmt == ExportFormat.CSV:
            return self._export_csv(output_dir, timestamp, compress, incremental_since)
        elif fmt == ExportFormat.PARQUET:
            return self._export_parquet(output_dir, timestamp, compress, incremental_since)
        else:
            raise ValueError(f"Unsupported format: {fmt}")

    def _get_trades(self, incremental_since: datetime | None = None) -> list[dict[str, Any]]:
        """Fetch trades from database.

        Args:
            incremental_since: Only fetch trades after this timestamp

        Returns:
            List of trade records
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        if incremental_since:
            cursor = conn.execute(
                "SELECT * FROM trades WHERE timestamp > ?",
                (incremental_since.isoformat(),),
            )
        else:
            cursor = conn.execute("SELECT * FROM trades")

        trades = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return trades

    def _export_json(
        self,
        output_dir: Path,
        timestamp: str,
        compress: bool,
        incremental_since: datetime | None,
    ) -> Path:
        """Export to JSON format.

        Args:
            output_dir: Output directory
            timestamp: Timestamp for filename
            compress: Whether to gzip
            incremental_since: Incremental cutoff

        Returns:
            Path to output file
        """
        trades = self._get_trades(incremental_since)

        filename = f"trades_{timestamp}.json"
        if compress:
            filename += ".gz"

        output_file = output_dir / filename

        data = {
            "export_timestamp": datetime.now(UTC).isoformat(),
            "incremental_since": (incremental_since.isoformat() if incremental_since else None),
            "record_count": len(trades),
            "trades": trades,
        }

        if compress:
            with gzip.open(output_file, "wt", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        else:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        return output_file

    def _export_csv(
        self,
        output_dir: Path,
        timestamp: str,
        compress: bool,
        incremental_since: datetime | None,
    ) -> Path:
        """Export to CSV format.

        Args:
            output_dir: Output directory
            timestamp: Timestamp for filename
            compress: Whether to gzip
            incremental_since: Incremental cutoff

        Returns:
            Path to output file
        """
        trades = self._get_trades(incremental_since)

        filename = f"trades_{timestamp}.csv"
        if compress:
            filename += ".gz"

        output_file = output_dir / filename

        if not trades:
            # Write empty CSV with headers
            if compress:
                with gzip.open(output_file, "wt", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        [
                            "timestamp",
                            "stock_code",
                            "action",
                            "quantity",
                            "price",
                            "confidence",
                            "rationale",
                            "pnl",
                        ]
                    )
            else:
                with open(output_file, "w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        [
                            "timestamp",
                            "stock_code",
                            "action",
                            "quantity",
                            "price",
                            "confidence",
                            "rationale",
                            "pnl",
                        ]
                    )
            return output_file

        # Get column names from first trade
        fieldnames = list(trades[0].keys())

        if compress:
            with gzip.open(output_file, "wt", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(trades)
        else:
            with open(output_file, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(trades)

        return output_file

    def _export_parquet(
        self,
        output_dir: Path,
        timestamp: str,
        compress: bool,
        incremental_since: datetime | None,
    ) -> Path:
        """Export to Parquet format.

        Args:
            output_dir: Output directory
            timestamp: Timestamp for filename
            compress: Whether to compress (Parquet has built-in compression)
            incremental_since: Incremental cutoff

        Returns:
            Path to output file
        """
        trades = self._get_trades(incremental_since)

        filename = f"trades_{timestamp}.parquet"
        output_file = output_dir / filename

        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            raise ImportError(
                "pyarrow is required for Parquet export. Install with: pip install pyarrow"
            )

        # Convert to pyarrow table
        table = pa.Table.from_pylist(trades)

        # Write with compression
        compression = "gzip" if compress else "none"
        pq.write_table(table, output_file, compression=compression)

        return output_file

    def get_export_stats(self) -> dict[str, Any]:
        """Get statistics about exportable data.

        Returns:
            Dictionary with data statistics
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        stats = {}

        # Total trades
        cursor.execute("SELECT COUNT(*) FROM trades")
        stats["total_trades"] = cursor.fetchone()[0]

        # Date range
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM trades")
        min_date, max_date = cursor.fetchone()
        stats["date_range"] = {"earliest": min_date, "latest": max_date}

        # Database size
        cursor.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
        stats["db_size_bytes"] = cursor.fetchone()[0]

        conn.close()

        return stats
