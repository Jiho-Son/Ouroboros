"""Tests for database helper functions."""

import tempfile
import os

from src.db import get_open_position, init_db, log_trade


def test_get_open_position_returns_latest_buy() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=2,
        price=70000.0,
        market="KR",
        exchange_code="KRX",
        decision_id="d-buy-1",
    )

    position = get_open_position(conn, "005930", "KR")
    assert position is not None
    assert position["decision_id"] == "d-buy-1"
    assert position["price"] == 70000.0
    assert position["quantity"] == 2


def test_get_open_position_returns_none_when_latest_is_sell() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=70000.0,
        market="KR",
        exchange_code="KRX",
        decision_id="d-buy-1",
    )
    log_trade(
        conn=conn,
        stock_code="005930",
        action="SELL",
        confidence=95,
        rationale="exit",
        quantity=1,
        price=71000.0,
        market="KR",
        exchange_code="KRX",
        decision_id="d-sell-1",
    )

    assert get_open_position(conn, "005930", "KR") is None


def test_get_open_position_returns_none_when_no_trades() -> None:
    conn = init_db(":memory:")
    assert get_open_position(conn, "AAPL", "US_NASDAQ") is None


# ---------------------------------------------------------------------------
# WAL mode tests (issue #210)
# ---------------------------------------------------------------------------


def test_wal_mode_applied_to_file_db() -> None:
    """File-based DB must use WAL journal mode for dashboard concurrent reads."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        conn = init_db(db_path)
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode == "wal", f"Expected WAL mode, got {mode}"
        conn.close()
    finally:
        os.unlink(db_path)
        # Clean up WAL auxiliary files if they exist
        for ext in ("-wal", "-shm"):
            path = db_path + ext
            if os.path.exists(path):
                os.unlink(path)


def test_wal_mode_not_applied_to_memory_db() -> None:
    """:memory: DB must not apply WAL (SQLite does not support WAL for in-memory)."""
    conn = init_db(":memory:")
    cursor = conn.execute("PRAGMA journal_mode")
    mode = cursor.fetchone()[0]
    # In-memory DBs default to 'memory' journal mode
    assert mode != "wal", "WAL should not be set on in-memory database"
    conn.close()
