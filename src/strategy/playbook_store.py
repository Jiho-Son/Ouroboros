"""Playbook persistence layer — CRUD for DayPlaybook in SQLite.

Stores and retrieves market-specific daily playbooks with JSON serialization.
Designed for the pre-market strategy system (one playbook per market per day).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date

from src.strategy.models import DayPlaybook, PlaybookStatus

logger = logging.getLogger(__name__)


class PlaybookStore:
    """CRUD operations for DayPlaybook persistence."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, playbook: DayPlaybook) -> int:
        """Save or replace a playbook for a given date+market.

        Uses INSERT OR REPLACE to enforce UNIQUE(date, market).

        Returns:
            The row id of the inserted/replaced record.
        """
        playbook_json = playbook.model_dump_json()
        cursor = self._conn.execute(
            """
            INSERT OR REPLACE INTO playbooks
                (date, market, status, playbook_json, generated_at,
                 token_count, scenario_count, match_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playbook.date.isoformat(),
                playbook.market,
                PlaybookStatus.READY.value,
                playbook_json,
                playbook.generated_at,
                playbook.token_count,
                playbook.scenario_count,
                0,
            ),
        )
        self._conn.commit()
        row_id = cursor.lastrowid or 0
        logger.info(
            "Saved playbook for %s/%s (%d stocks, %d scenarios)",
            playbook.date,
            playbook.market,
            playbook.stock_count,
            playbook.scenario_count,
        )
        return row_id

    def load(self, target_date: date, market: str) -> DayPlaybook | None:
        """Load a playbook for a specific date and market.

        Returns:
            DayPlaybook if found, None otherwise.
        """
        row = self._conn.execute(
            "SELECT playbook_json FROM playbooks WHERE date = ? AND market = ?",
            (target_date.isoformat(), market),
        ).fetchone()
        if row is None:
            return None
        return DayPlaybook.model_validate_json(row[0])

    def get_status(self, target_date: date, market: str) -> PlaybookStatus | None:
        """Get the status of a playbook without deserializing the full JSON."""
        row = self._conn.execute(
            "SELECT status FROM playbooks WHERE date = ? AND market = ?",
            (target_date.isoformat(), market),
        ).fetchone()
        if row is None:
            return None
        return PlaybookStatus(row[0])

    def update_status(self, target_date: date, market: str, status: PlaybookStatus) -> bool:
        """Update the status of a playbook.

        Returns:
            True if a row was updated, False if not found.
        """
        cursor = self._conn.execute(
            "UPDATE playbooks SET status = ? WHERE date = ? AND market = ?",
            (status.value, target_date.isoformat(), market),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def increment_match_count(self, target_date: date, market: str) -> bool:
        """Increment the match_count for tracking scenario hits during the day.

        Returns:
            True if a row was updated, False if not found.
        """
        cursor = self._conn.execute(
            "UPDATE playbooks SET match_count = match_count + 1 WHERE date = ? AND market = ?",
            (target_date.isoformat(), market),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_stats(self, target_date: date, market: str) -> dict | None:
        """Get playbook stats without full deserialization.

        Returns:
            Dict with status, token_count, scenario_count, match_count, or None.
        """
        row = self._conn.execute(
            """
            SELECT status, token_count, scenario_count, match_count, generated_at
            FROM playbooks WHERE date = ? AND market = ?
            """,
            (target_date.isoformat(), market),
        ).fetchone()
        if row is None:
            return None
        return {
            "status": row[0],
            "token_count": row[1],
            "scenario_count": row[2],
            "match_count": row[3],
            "generated_at": row[4],
        }

    def list_recent(self, market: str | None = None, limit: int = 7) -> list[dict]:
        """List recent playbooks with summary info.

        Args:
            market: Filter by market code. None for all markets.
            limit: Max number of results.

        Returns:
            List of dicts with date, market, status, scenario_count, match_count.
        """
        if market is not None:
            rows = self._conn.execute(
                """
                SELECT date, market, status, scenario_count, match_count
                FROM playbooks WHERE market = ?
                ORDER BY date DESC LIMIT ?
                """,
                (market, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT date, market, status, scenario_count, match_count
                FROM playbooks
                ORDER BY date DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "date": row[0],
                "market": row[1],
                "status": row[2],
                "scenario_count": row[3],
                "match_count": row[4],
            }
            for row in rows
        ]

    def delete(self, target_date: date, market: str) -> bool:
        """Delete a playbook.

        Returns:
            True if a row was deleted, False if not found.
        """
        cursor = self._conn.execute(
            "DELETE FROM playbooks WHERE date = ? AND market = ?",
            (target_date.isoformat(), market),
        )
        self._conn.commit()
        return cursor.rowcount > 0
