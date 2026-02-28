"""Decision logging system with context snapshots for comprehensive audit trail."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass
class DecisionLog:
    """A logged trading decision with context and outcome."""

    decision_id: str
    timestamp: str
    stock_code: str
    market: str
    exchange_code: str
    session_id: str
    action: str
    confidence: int
    rationale: str
    context_snapshot: dict[str, Any]
    input_data: dict[str, Any]
    outcome_pnl: float | None = None
    outcome_accuracy: int | None = None
    reviewed: bool = False
    review_notes: str | None = None


class DecisionLogger:
    """Logs trading decisions with full context for review and evolution."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Initialize the decision logger with a database connection."""
        self.conn = conn

    def log_decision(
        self,
        stock_code: str,
        market: str,
        exchange_code: str,
        action: str,
        confidence: int,
        rationale: str,
        context_snapshot: dict[str, Any],
        input_data: dict[str, Any],
        session_id: str | None = None,
    ) -> str:
        """Log a trading decision with full context.

        Args:
            stock_code: Stock symbol
            market: Market code (e.g., "KR", "US_NASDAQ")
            exchange_code: Exchange code (e.g., "KRX", "NASDAQ")
            action: Trading action (BUY/SELL/HOLD)
            confidence: Confidence level (0-100)
            rationale: Reasoning for the decision
            context_snapshot: L1-L7 context snapshot at decision time
            input_data: Market data inputs (price, volume, orderbook, etc.)
            session_id: Runtime session identifier

        Returns:
            decision_id: Unique identifier for this decision
        """
        decision_id = str(uuid.uuid4())
        timestamp = datetime.now(UTC).isoformat()
        resolved_session = session_id or "UNKNOWN"

        self.conn.execute(
            """
            INSERT INTO decision_logs (
                decision_id, timestamp, stock_code, market, exchange_code,
                session_id, action, confidence, rationale, context_snapshot, input_data
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                timestamp,
                stock_code,
                market,
                exchange_code,
                resolved_session,
                action,
                confidence,
                rationale,
                json.dumps(context_snapshot),
                json.dumps(input_data),
            ),
        )
        self.conn.commit()

        return decision_id

    def get_unreviewed_decisions(
        self, min_confidence: int = 80, limit: int | None = None
    ) -> list[DecisionLog]:
        """Get unreviewed decisions with high confidence.

        Args:
            min_confidence: Minimum confidence threshold (default 80)
            limit: Maximum number of results (None = unlimited)

        Returns:
            List of unreviewed DecisionLog objects
        """
        query = """
            SELECT
                decision_id, timestamp, stock_code, market, exchange_code,
                session_id, action, confidence, rationale, context_snapshot, input_data,
                outcome_pnl, outcome_accuracy, reviewed, review_notes
            FROM decision_logs
            WHERE reviewed = 0 AND confidence >= ?
            ORDER BY timestamp DESC
        """
        if limit is not None:
            query += f" LIMIT {limit}"

        cursor = self.conn.execute(query, (min_confidence,))
        return [self._row_to_decision_log(row) for row in cursor.fetchall()]

    def mark_reviewed(self, decision_id: str, notes: str) -> None:
        """Mark a decision as reviewed with notes.

        Args:
            decision_id: Decision identifier
            notes: Review notes and insights
        """
        self.conn.execute(
            """
            UPDATE decision_logs
            SET reviewed = 1, review_notes = ?
            WHERE decision_id = ?
            """,
            (notes, decision_id),
        )
        self.conn.commit()

    def update_outcome(
        self, decision_id: str, pnl: float, accuracy: int
    ) -> None:
        """Update the outcome of a decision after trade execution.

        Args:
            decision_id: Decision identifier
            pnl: Actual profit/loss realized
            accuracy: 1 if decision was correct, 0 if wrong
        """
        self.conn.execute(
            """
            UPDATE decision_logs
            SET outcome_pnl = ?, outcome_accuracy = ?
            WHERE decision_id = ?
            """,
            (pnl, accuracy, decision_id),
        )
        self.conn.commit()

    def get_decision_by_id(self, decision_id: str) -> DecisionLog | None:
        """Get a specific decision by ID.

        Args:
            decision_id: Decision identifier

        Returns:
            DecisionLog object or None if not found
        """
        cursor = self.conn.execute(
            """
            SELECT
                decision_id, timestamp, stock_code, market, exchange_code,
                session_id, action, confidence, rationale, context_snapshot, input_data,
                outcome_pnl, outcome_accuracy, reviewed, review_notes
            FROM decision_logs
            WHERE decision_id = ?
            """,
            (decision_id,),
        )
        row = cursor.fetchone()
        return self._row_to_decision_log(row) if row else None

    def get_losing_decisions(
        self, min_confidence: int = 80, min_loss: float = -100.0
    ) -> list[DecisionLog]:
        """Get high-confidence decisions that resulted in losses.

        Useful for identifying patterns in failed predictions.

        Args:
            min_confidence: Minimum confidence threshold (default 80)
            min_loss: Minimum loss amount (default -100.0, i.e., loss >= 100)

        Returns:
            List of losing DecisionLog objects
        """
        cursor = self.conn.execute(
            """
            SELECT
                decision_id, timestamp, stock_code, market, exchange_code,
                session_id, action, confidence, rationale, context_snapshot, input_data,
                outcome_pnl, outcome_accuracy, reviewed, review_notes
            FROM decision_logs
            WHERE confidence >= ?
              AND outcome_pnl IS NOT NULL
              AND outcome_pnl <= ?
            ORDER BY outcome_pnl ASC
            """,
            (min_confidence, min_loss),
        )
        return [self._row_to_decision_log(row) for row in cursor.fetchall()]

    def _row_to_decision_log(self, row: tuple[Any, ...]) -> DecisionLog:
        """Convert a database row to a DecisionLog object.

        Args:
            row: Database row tuple

        Returns:
            DecisionLog object
        """
        return DecisionLog(
            decision_id=row[0],
            timestamp=row[1],
            stock_code=row[2],
            market=row[3],
            exchange_code=row[4],
            session_id=row[5] or "UNKNOWN",
            action=row[6],
            confidence=row[7],
            rationale=row[8],
            context_snapshot=json.loads(row[9]),
            input_data=json.loads(row[10]),
            outcome_pnl=row[11],
            outcome_accuracy=row[12],
            reviewed=bool(row[13]),
            review_notes=row[14],
        )
