"""Context storage and retrieval for the 7-tier memory system."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from src.context.layer import LAYER_CONFIG, ContextLayer


class ContextStore:
    """Manages context data across the 7-tier hierarchy."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Initialize the context store with a database connection."""
        self.conn = conn
        self._init_metadata()

    def _init_metadata(self) -> None:
        """Initialize context_metadata table with layer configurations."""
        for config in LAYER_CONFIG.values():
            self.conn.execute(
                """
                INSERT OR REPLACE INTO context_metadata
                (layer, description, retention_days, aggregation_source)
                VALUES (?, ?, ?, ?)
                """,
                (
                    config.layer.value,
                    config.description,
                    config.retention_days,
                    config.aggregation_source.value if config.aggregation_source else None,
                ),
            )
        self.conn.commit()

    def set_context(
        self,
        layer: ContextLayer,
        timeframe: str,
        key: str,
        value: Any,
    ) -> None:
        """Set a context value for a given layer and timeframe.

        Args:
            layer: The context layer (L1-L7)
            timeframe: Time identifier (e.g., "2026", "2026-Q1", "2026-01",
                "2026-W05", "2026-02-04")
            key: Context key (e.g., "sharpe_ratio", "win_rate", "lesson_learned")
            value: Context value (will be JSON-serialized)
        """
        now = datetime.now(UTC).isoformat()
        value_json = json.dumps(value)

        self.conn.execute(
            """
            INSERT INTO contexts (layer, timeframe, key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(layer, timeframe, key)
            DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (layer.value, timeframe, key, value_json, now, now),
        )
        self.conn.commit()

    def get_context(
        self,
        layer: ContextLayer,
        timeframe: str,
        key: str,
    ) -> Any | None:
        """Get a context value for a given layer and timeframe.

        Args:
            layer: The context layer (L1-L7)
            timeframe: Time identifier
            key: Context key

        Returns:
            The context value (deserialized from JSON), or None if not found
        """
        cursor = self.conn.execute(
            """
            SELECT value FROM contexts
            WHERE layer = ? AND timeframe = ? AND key = ?
            """,
            (layer.value, timeframe, key),
        )
        row = cursor.fetchone()
        if row:
            return json.loads(row[0])
        return None

    def get_all_contexts(
        self,
        layer: ContextLayer,
        timeframe: str | None = None,
    ) -> dict[str, Any]:
        """Get all context values for a given layer and optional timeframe.

        Args:
            layer: The context layer (L1-L7)
            timeframe: Optional time identifier filter

        Returns:
            Dictionary of key-value pairs for the specified layer/timeframe
        """
        if timeframe:
            cursor = self.conn.execute(
                """
                SELECT key, value FROM contexts
                WHERE layer = ? AND timeframe = ?
                ORDER BY key
                """,
                (layer.value, timeframe),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT key, value FROM contexts
                WHERE layer = ?
                ORDER BY timeframe DESC, key
                """,
                (layer.value,),
            )

        return {row[0]: json.loads(row[1]) for row in cursor.fetchall()}

    def get_latest_context_entry(
        self,
        layer: ContextLayer,
        key: str,
    ) -> tuple[str, Any] | None:
        """Get the latest timeframe and value stored for a layer/key pair."""
        cursor = self.conn.execute(
            """
            SELECT timeframe, value FROM contexts
            WHERE layer = ? AND key = ?
            ORDER BY timeframe DESC, updated_at DESC
            LIMIT 1
            """,
            (layer.value, key),
        )
        row = cursor.fetchone()
        if row:
            return row[0], json.loads(row[1])
        return None

    def get_latest_timeframe(self, layer: ContextLayer) -> str | None:
        """Get the most recent timeframe for a given layer.

        Args:
            layer: The context layer (L1-L7)

        Returns:
            The latest timeframe string, or None if no data exists
        """
        cursor = self.conn.execute(
            """
            SELECT timeframe FROM contexts
            WHERE layer = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (layer.value,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def delete_old_contexts(self, layer: ContextLayer, cutoff_date: str) -> int:
        """Delete contexts older than the cutoff date for a given layer.

        Args:
            layer: The context layer (L1-L7)
            cutoff_date: ISO format date string (contexts before this will be deleted)

        Returns:
            Number of rows deleted
        """
        cursor = self.conn.execute(
            """
            DELETE FROM contexts
            WHERE layer = ? AND updated_at < ?
            """,
            (layer.value, cutoff_date),
        )
        self.conn.commit()
        return cursor.rowcount

    def cleanup_expired_contexts(self) -> dict[ContextLayer, int]:
        """Delete expired contexts based on retention policies.

        Returns:
            Dictionary mapping layer to number of deleted rows
        """
        deleted_counts: dict[ContextLayer, int] = {}

        for layer, config in LAYER_CONFIG.items():
            if config.retention_days is None:
                # Keep forever (e.g., L1_LEGACY)
                deleted_counts[layer] = 0
                continue

            # Calculate cutoff date
            from datetime import timedelta

            cutoff = datetime.now(UTC) - timedelta(days=config.retention_days)
            deleted_counts[layer] = self.delete_old_contexts(layer, cutoff.isoformat())

        return deleted_counts
