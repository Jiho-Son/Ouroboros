"""Playbook persistence layer — CRUD for DayPlaybook in SQLite.

Stores and retrieves market/session-specific daily playbooks with JSON
serialization.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date

from src.strategy.models import DayPlaybook, PlaybookStatus

logger = logging.getLogger(__name__)


def _normalize_session_id(session_id: str | None) -> str:
    resolved = (session_id or "").strip()
    return resolved or "UNKNOWN"


class PlaybookStore:
    """CRUD operations for DayPlaybook persistence."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, playbook: DayPlaybook, slot: str = "open") -> int:
        """Save or replace a playbook for a given date+market+session+slot.

        Uses INSERT OR REPLACE to enforce
        UNIQUE(date, market, session_id, slot).

        Returns:
            The row id of the inserted/replaced record.
        """
        session_id = _normalize_session_id(playbook.session_id)
        playbook_json = playbook.model_dump_json()
        cursor = self._conn.execute(
            """
            INSERT OR REPLACE INTO playbooks
                (date, market, session_id, slot, status, playbook_json, generated_at,
                 token_count, scenario_count, match_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playbook.date.isoformat(),
                playbook.market,
                session_id,
                slot,
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
            "Saved playbook for %s/%s session=%s slot=%s (%d stocks, %d scenarios)",
            playbook.date,
            playbook.market,
            session_id,
            slot,
            playbook.stock_count,
            playbook.scenario_count,
        )
        return row_id

    def load(
        self,
        target_date: date,
        market: str,
        session_id: str = "UNKNOWN",
        slot: str = "open",
    ) -> DayPlaybook | None:
        """Load a playbook for a specific date, market, session, and slot.

        Returns:
            DayPlaybook if found, None otherwise.
        """
        resolved_session_id = _normalize_session_id(session_id)
        row = self._conn.execute(
            """
            SELECT playbook_json
            FROM playbooks
            WHERE date = ? AND market = ? AND session_id = ? AND slot = ?
            """,
            (target_date.isoformat(), market, resolved_session_id, slot),
        ).fetchone()
        if row is None:
            return None
        return DayPlaybook.model_validate_json(row[0])

    def load_latest(
        self,
        target_date: date,
        market: str,
        session_id: str = "UNKNOWN",
    ) -> DayPlaybook | None:
        """Load the most recent playbook for the requested session.

        Within a session, mid is preferred over open for restart/resume.
        """
        resolved_session_id = _normalize_session_id(session_id)
        row = self._conn.execute(
            """
            SELECT playbook_json FROM playbooks
            WHERE date = ? AND market = ? AND session_id = ?
            ORDER BY CASE slot WHEN 'mid' THEN 0 ELSE 1 END, generated_at DESC
            LIMIT 1
            """,
            (target_date.isoformat(), market, resolved_session_id),
        ).fetchone()
        if row is None:
            return None
        return DayPlaybook.model_validate_json(row[0])

    def get_status(
        self,
        target_date: date,
        market: str,
        session_id: str = "UNKNOWN",
        slot: str = "open",
    ) -> PlaybookStatus | None:
        """Get the status of a playbook without deserializing the full JSON."""
        resolved_session_id = _normalize_session_id(session_id)
        row = self._conn.execute(
            """
            SELECT status
            FROM playbooks
            WHERE date = ? AND market = ? AND session_id = ? AND slot = ?
            """,
            (target_date.isoformat(), market, resolved_session_id, slot),
        ).fetchone()
        if row is None:
            return None
        return PlaybookStatus(row[0])

    def update_status(
        self,
        target_date: date,
        market: str,
        status: PlaybookStatus,
        session_id: str = "UNKNOWN",
        slot: str = "open",
    ) -> bool:
        """Update the status of a playbook.

        Returns:
            True if a row was updated, False if not found.
        """
        resolved_session_id = _normalize_session_id(session_id)
        cursor = self._conn.execute(
            """
            UPDATE playbooks
            SET status = ?
            WHERE date = ? AND market = ? AND session_id = ? AND slot = ?
            """,
            (status.value, target_date.isoformat(), market, resolved_session_id, slot),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def increment_match_count(
        self,
        target_date: date,
        market: str,
        session_id: str = "UNKNOWN",
        slot: str = "open",
    ) -> bool:
        """Increment the match_count for tracking scenario hits during the day.

        Returns:
            True if a row was updated, False if not found.
        """
        resolved_session_id = _normalize_session_id(session_id)
        cursor = self._conn.execute(
            "UPDATE playbooks SET match_count = match_count + 1"
            " WHERE date = ? AND market = ? AND session_id = ? AND slot = ?",
            (target_date.isoformat(), market, resolved_session_id, slot),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_stats(
        self,
        target_date: date,
        market: str,
        session_id: str = "UNKNOWN",
        slot: str = "open",
    ) -> dict | None:
        """Get playbook stats without full deserialization.

        Returns:
            Dict with status, token_count, scenario_count, match_count, or None.
        """
        resolved_session_id = _normalize_session_id(session_id)
        row = self._conn.execute(
            """
            SELECT status, token_count, scenario_count, match_count, generated_at
            FROM playbooks
            WHERE date = ? AND market = ? AND session_id = ? AND slot = ?
            """,
            (target_date.isoformat(), market, resolved_session_id, slot),
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

        Returns one row per (date, market, session_id). Within each session,
        'mid' is preferred over 'open'.

        Args:
            market: Filter by market code. None for all markets.
            limit: Max number of results.

        Returns:
            List of dicts with date, market, session_id, slot, status,
            scenario_count, match_count.
        """
        if market is not None:
            rows = self._conn.execute(
                """
                SELECT p.date, p.market, p.session_id, p.slot, p.status,
                       p.scenario_count, p.match_count
                FROM playbooks p
                INNER JOIN (
                    SELECT date, market, session_id,
                           MAX(CASE slot WHEN 'mid' THEN 1 ELSE 0 END) AS has_mid
                    FROM playbooks
                    WHERE market = ?
                    GROUP BY date, market, session_id
                ) latest ON p.market = latest.market
                        AND p.date = latest.date
                        AND p.session_id = latest.session_id
                        AND (
                            (latest.has_mid = 1 AND p.slot = 'mid')
                            OR (latest.has_mid = 0 AND p.slot = 'open')
                        )
                ORDER BY p.date DESC LIMIT ?
                """,
                (market, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT p.date, p.market, p.session_id, p.slot, p.status,
                       p.scenario_count, p.match_count
                FROM playbooks p
                INNER JOIN (
                    SELECT date, market, session_id,
                           MAX(CASE slot WHEN 'mid' THEN 1 ELSE 0 END) AS has_mid
                    FROM playbooks
                    GROUP BY date, market, session_id
                ) latest ON p.market = latest.market
                        AND p.date = latest.date
                        AND p.session_id = latest.session_id
                        AND (
                            (latest.has_mid = 1 AND p.slot = 'mid')
                            OR (latest.has_mid = 0 AND p.slot = 'open')
                        )
                ORDER BY p.date DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "date": row[0],
                "market": row[1],
                "session_id": row[2],
                "slot": row[3],
                "status": row[4],
                "scenario_count": row[5],
                "match_count": row[6],
            }
            for row in rows
        ]

    def delete(
        self,
        target_date: date,
        market: str,
        session_id: str = "UNKNOWN",
        slot: str = "open",
    ) -> bool:
        """Delete a playbook.

        Returns:
            True if a row was deleted, False if not found.
        """
        resolved_session_id = _normalize_session_id(session_id)
        cursor = self._conn.execute(
            """
            DELETE FROM playbooks
            WHERE date = ? AND market = ? AND session_id = ? AND slot = ?
            """,
            (target_date.isoformat(), market, resolved_session_id, slot),
        )
        self._conn.commit()
        return cursor.rowcount > 0
