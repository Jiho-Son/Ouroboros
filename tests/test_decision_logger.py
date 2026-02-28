"""Tests for decision logging and audit trail."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from src.db import init_db
from src.logging.decision_logger import DecisionLog, DecisionLogger


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """Provide an in-memory database with initialized schema."""
    conn = init_db(":memory:")
    return conn


@pytest.fixture
def logger(db_conn: sqlite3.Connection) -> DecisionLogger:
    """Provide a DecisionLogger instance."""
    return DecisionLogger(db_conn)


def test_log_decision_creates_record(logger: DecisionLogger, db_conn: sqlite3.Connection) -> None:
    """Test that log_decision creates a database record."""
    context_snapshot = {
        "L1": {"quote": {"price": 100.0, "volume": 1000}},
        "L2": {"orderbook": {"bid": [99.0], "ask": [101.0]}},
    }
    input_data = {"price": 100.0, "volume": 1000, "foreigner_net": 500}

    decision_id = logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=85,
        rationale="Strong upward momentum",
        context_snapshot=context_snapshot,
        input_data=input_data,
    )

    # Verify decision_id is a valid UUID
    assert decision_id is not None
    assert len(decision_id) == 36  # UUID v4 format

    # Verify record exists in database
    cursor = db_conn.execute(
        "SELECT decision_id, action, confidence, session_id FROM decision_logs WHERE decision_id = ?",
        (decision_id,),
    )
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == decision_id
    assert row[1] == "BUY"
    assert row[2] == 85
    assert row[3] == "UNKNOWN"


def test_log_decision_stores_context_snapshot(logger: DecisionLogger) -> None:
    """Test that context snapshot is stored as JSON."""
    context_snapshot = {
        "L1": {"real_time": "data"},
        "L3": {"daily": "aggregate"},
        "L7": {"legacy": "wisdom"},
    }
    input_data = {"price": 50000.0, "volume": 2000}

    decision_id = logger.log_decision(
        stock_code="035420",
        market="KR",
        exchange_code="KRX",
        action="HOLD",
        confidence=75,
        rationale="Waiting for clearer signal",
        context_snapshot=context_snapshot,
        input_data=input_data,
    )

    # Retrieve and verify context snapshot
    decision = logger.get_decision_by_id(decision_id)
    assert decision is not None
    assert decision.context_snapshot == context_snapshot
    assert decision.input_data == input_data
    assert decision.session_id == "UNKNOWN"


def test_log_decision_stores_explicit_session_id(logger: DecisionLogger) -> None:
    decision_id = logger.log_decision(
        stock_code="AAPL",
        market="US_NASDAQ",
        exchange_code="NASD",
        action="BUY",
        confidence=88,
        rationale="session check",
        context_snapshot={},
        input_data={},
        session_id="US_PRE",
    )
    decision = logger.get_decision_by_id(decision_id)
    assert decision is not None
    assert decision.session_id == "US_PRE"


def test_get_unreviewed_decisions(logger: DecisionLogger) -> None:
    """Test retrieving unreviewed decisions with confidence filter."""
    # Log multiple decisions with varying confidence
    logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="High confidence buy",
        context_snapshot={},
        input_data={},
    )
    logger.log_decision(
        stock_code="000660",
        market="KR",
        exchange_code="KRX",
        action="SELL",
        confidence=75,
        rationale="Low confidence sell",
        context_snapshot={},
        input_data={},
    )
    logger.log_decision(
        stock_code="035420",
        market="KR",
        exchange_code="KRX",
        action="HOLD",
        confidence=85,
        rationale="Medium confidence hold",
        context_snapshot={},
        input_data={},
    )

    # Get unreviewed decisions with default threshold (80)
    unreviewed = logger.get_unreviewed_decisions()
    assert len(unreviewed) == 2  # Only confidence >= 80
    assert all(d.confidence >= 80 for d in unreviewed)
    assert all(not d.reviewed for d in unreviewed)

    # Get with lower threshold
    unreviewed_all = logger.get_unreviewed_decisions(min_confidence=70)
    assert len(unreviewed_all) == 3


def test_mark_reviewed(logger: DecisionLogger) -> None:
    """Test marking a decision as reviewed."""
    decision_id = logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=85,
        rationale="Test decision",
        context_snapshot={},
        input_data={},
    )

    # Initially unreviewed
    decision = logger.get_decision_by_id(decision_id)
    assert decision is not None
    assert not decision.reviewed
    assert decision.review_notes is None

    # Mark as reviewed
    review_notes = "Good decision, captured bullish momentum correctly"
    logger.mark_reviewed(decision_id, review_notes)

    # Verify updated
    decision = logger.get_decision_by_id(decision_id)
    assert decision is not None
    assert decision.reviewed
    assert decision.review_notes == review_notes

    # Should not appear in unreviewed list
    unreviewed = logger.get_unreviewed_decisions()
    assert all(d.decision_id != decision_id for d in unreviewed)


def test_update_outcome(logger: DecisionLogger) -> None:
    """Test updating decision outcome with P&L and accuracy."""
    decision_id = logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=90,
        rationale="Expecting price increase",
        context_snapshot={},
        input_data={},
    )

    # Initially no outcome
    decision = logger.get_decision_by_id(decision_id)
    assert decision is not None
    assert decision.outcome_pnl is None
    assert decision.outcome_accuracy is None

    # Update outcome (profitable trade)
    logger.update_outcome(decision_id, pnl=5000.0, accuracy=1)

    # Verify updated
    decision = logger.get_decision_by_id(decision_id)
    assert decision is not None
    assert decision.outcome_pnl == 5000.0
    assert decision.outcome_accuracy == 1


def test_get_losing_decisions(logger: DecisionLogger) -> None:
    """Test retrieving high-confidence losing decisions."""
    # Profitable decision
    id1 = logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=85,
        rationale="Correct prediction",
        context_snapshot={},
        input_data={},
    )
    logger.update_outcome(id1, pnl=3000.0, accuracy=1)

    # High-confidence loss
    id2 = logger.log_decision(
        stock_code="000660",
        market="KR",
        exchange_code="KRX",
        action="SELL",
        confidence=90,
        rationale="Wrong prediction",
        context_snapshot={},
        input_data={},
    )
    logger.update_outcome(id2, pnl=-2000.0, accuracy=0)

    # Low-confidence loss (should be ignored)
    id3 = logger.log_decision(
        stock_code="035420",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=70,
        rationale="Low confidence, wrong",
        context_snapshot={},
        input_data={},
    )
    logger.update_outcome(id3, pnl=-1500.0, accuracy=0)

    # Get high-confidence losing decisions
    losers = logger.get_losing_decisions(min_confidence=80, min_loss=-1000.0)
    assert len(losers) == 1
    assert losers[0].decision_id == id2
    assert losers[0].outcome_pnl == -2000.0
    assert losers[0].confidence == 90


def test_get_decision_by_id_not_found(logger: DecisionLogger) -> None:
    """Test that get_decision_by_id returns None for non-existent ID."""
    decision = logger.get_decision_by_id("non-existent-uuid")
    assert decision is None


def test_unreviewed_limit(logger: DecisionLogger) -> None:
    """Test that get_unreviewed_decisions respects limit parameter."""
    # Create 5 unreviewed decisions
    for i in range(5):
        logger.log_decision(
            stock_code=f"00{i}",
            market="KR",
            exchange_code="KRX",
            action="HOLD",
            confidence=85,
            rationale=f"Decision {i}",
            context_snapshot={},
            input_data={},
        )

    # Get only 3
    unreviewed = logger.get_unreviewed_decisions(limit=3)
    assert len(unreviewed) == 3


def test_decision_log_dataclass() -> None:
    """Test DecisionLog dataclass creation."""
    now = datetime.now(UTC).isoformat()
    log = DecisionLog(
        decision_id="test-uuid",
        timestamp=now,
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        session_id="KRX_REG",
        action="BUY",
        confidence=85,
        rationale="Test",
        context_snapshot={"L1": "data"},
        input_data={"price": 100.0},
    )

    assert log.decision_id == "test-uuid"
    assert log.session_id == "KRX_REG"
    assert log.action == "BUY"
    assert log.confidence == 85
    assert log.reviewed is False
    assert log.outcome_pnl is None
