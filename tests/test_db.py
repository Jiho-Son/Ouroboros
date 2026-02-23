"""Tests for database helper functions."""

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
# mode column tests (issue #212)
# ---------------------------------------------------------------------------


def test_log_trade_stores_mode_paper() -> None:
    """log_trade must persist mode='paper' in the trades table."""
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="005930",
        action="BUY",
        confidence=85,
        rationale="test",
        mode="paper",
    )
    row = conn.execute("SELECT mode FROM trades ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == "paper"


def test_log_trade_stores_mode_live() -> None:
    """log_trade must persist mode='live' in the trades table."""
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="005930",
        action="BUY",
        confidence=85,
        rationale="test",
        mode="live",
    )
    row = conn.execute("SELECT mode FROM trades ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == "live"


def test_log_trade_default_mode_is_paper() -> None:
    """log_trade without explicit mode must default to 'paper'."""
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="005930",
        action="HOLD",
        confidence=50,
        rationale="test",
    )
    row = conn.execute("SELECT mode FROM trades ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == "paper"


def test_mode_column_exists_in_schema() -> None:
    """trades table must have a mode column after init_db."""
    conn = init_db(":memory:")
    cursor = conn.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    assert "mode" in columns


def test_mode_migration_adds_column_to_existing_db() -> None:
    """init_db must add mode column to existing DBs that lack it (migration)."""
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        # Create DB without mode column (simulate old schema)
        import sqlite3
        old_conn = sqlite3.connect(db_path)
        old_conn.execute(
            """CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                rationale TEXT,
                quantity INTEGER,
                price REAL,
                pnl REAL DEFAULT 0.0,
                market TEXT DEFAULT 'KR',
                exchange_code TEXT DEFAULT 'KRX',
                decision_id TEXT
            )"""
        )
        old_conn.commit()
        old_conn.close()

        # Run init_db — should add mode column via migration
        conn = init_db(db_path)
        cursor = conn.execute("PRAGMA table_info(trades)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "mode" in columns
        conn.close()
    finally:
        os.unlink(db_path)
