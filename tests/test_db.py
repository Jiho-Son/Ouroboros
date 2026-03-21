"""Tests for database helper functions."""

import os
import tempfile

from src.db import (
    get_latest_buy_trade,
    get_latest_sell_trade,
    get_open_position,
    init_db,
    log_trade,
)


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
    assert "session_id" in columns
    assert "strategy_pnl" in columns
    assert "fx_pnl" in columns


def test_mode_migration_adds_column_to_existing_db() -> None:
    """init_db must add mode column to existing DBs that lack it (migration)."""
    import sqlite3

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        # Create DB without mode column (simulate old schema)
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
        old_conn.execute(
            """
            INSERT INTO trades (
                timestamp, stock_code, action, confidence, rationale, quantity, price, pnl
            ) VALUES ('2026-01-01T00:00:00+00:00', 'AAPL', 'SELL', 90, 'legacy', 1, 100.0, 123.45)
            """
        )
        old_conn.commit()
        old_conn.close()

        # Run init_db — should add mode column via migration
        conn = init_db(db_path)
        cursor = conn.execute("PRAGMA table_info(trades)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "mode" in columns
        assert "session_id" in columns
        assert "strategy_pnl" in columns
        assert "fx_pnl" in columns
        migrated = conn.execute(
            "SELECT pnl, strategy_pnl, fx_pnl, session_id "
            "FROM trades WHERE stock_code='AAPL' LIMIT 1"
        ).fetchone()
        assert migrated is not None
        assert migrated[0] == 123.45
        assert migrated[1] == 123.45
        assert migrated[2] == 0.0
        assert migrated[3] == "UNKNOWN"
        conn.close()
    finally:
        os.unlink(db_path)


def test_log_trade_stores_strategy_and_fx_pnl_separately() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="SELL",
        confidence=90,
        rationale="fx split",
        pnl=120.0,
        strategy_pnl=100.0,
        fx_pnl=20.0,
        market="US_NASDAQ",
        exchange_code="NASD",
    )
    row = conn.execute(
        "SELECT pnl, strategy_pnl, fx_pnl FROM trades ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row[0] == 120.0
    assert row[1] == 100.0
    assert row[2] == 20.0


def test_log_trade_backward_compat_sets_strategy_pnl_from_pnl() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="005930",
        action="SELL",
        confidence=80,
        rationale="legacy",
        pnl=50.0,
        market="KR",
        exchange_code="KRX",
    )
    row = conn.execute(
        "SELECT pnl, strategy_pnl, fx_pnl FROM trades ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row[0] == 50.0
    assert row[1] == 50.0
    assert row[2] == 0.0


def test_log_trade_partial_fx_input_does_not_infer_negative_strategy_pnl() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="SELL",
        confidence=70,
        rationale="fx only",
        pnl=0.0,
        fx_pnl=10.0,
        market="US_NASDAQ",
        exchange_code="NASD",
    )
    row = conn.execute(
        "SELECT pnl, strategy_pnl, fx_pnl FROM trades ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row[0] == 10.0
    assert row[1] == 0.0
    assert row[2] == 10.0


def test_log_trade_persists_explicit_session_id() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="BUY",
        confidence=70,
        rationale="session test",
        market="US_NASDAQ",
        exchange_code="NASD",
        session_id="US_PRE",
    )
    row = conn.execute("SELECT session_id FROM trades ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == "US_PRE"


def test_log_trade_auto_derives_session_id_when_not_provided() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="005930",
        action="BUY",
        confidence=70,
        rationale="auto session",
        market="KR",
        exchange_code="KRX",
    )
    row = conn.execute("SELECT session_id FROM trades ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row[0] != "UNKNOWN"


def test_log_trade_unknown_market_falls_back_to_unknown_session() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="X",
        action="BUY",
        confidence=70,
        rationale="unknown market",
        market="MARS",
        exchange_code="MARS",
    )
    row = conn.execute("SELECT session_id FROM trades ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == "UNKNOWN"


def test_get_latest_buy_trade_prefers_exchange_code_match() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="BUY",
        confidence=80,
        rationale="legacy",
        quantity=10,
        price=120.0,
        market="US_NASDAQ",
        exchange_code="",
        decision_id="legacy-buy",
    )
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="BUY",
        confidence=85,
        rationale="matched",
        quantity=5,
        price=125.0,
        market="US_NASDAQ",
        exchange_code="NASD",
        decision_id="matched-buy",
    )
    matched = get_latest_buy_trade(
        conn,
        stock_code="AAPL",
        market="US_NASDAQ",
        exchange_code="NASD",
    )
    assert matched is not None
    assert matched["decision_id"] == "matched-buy"


def test_get_latest_buy_trade_returns_latest_row_when_exchange_code_is_none() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="BUY",
        confidence=80,
        rationale="older buy",
        quantity=10,
        price=120.0,
        market="US_NASDAQ",
        exchange_code="NASD",
        decision_id="older-buy",
    )
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="BUY",
        confidence=85,
        rationale="latest legacy buy",
        quantity=5,
        price=121.0,
        market="US_NASDAQ",
        exchange_code="NYSE",
        decision_id="latest-buy",
    )
    conn.execute(
        "UPDATE trades SET timestamp = ? WHERE decision_id = ?",
        ("2026-03-20T00:00:00+00:00", "older-buy"),
    )
    conn.execute(
        "UPDATE trades SET timestamp = ? WHERE decision_id = ?",
        ("2026-03-20T00:01:00+00:00", "latest-buy"),
    )
    conn.commit()

    matched = get_latest_buy_trade(
        conn,
        stock_code="AAPL",
        market="US_NASDAQ",
        exchange_code=None,
    )

    assert matched is not None
    assert matched["decision_id"] == "latest-buy"
    assert matched["price"] == 121.0


def test_get_latest_sell_trade_prefers_exchange_code_match() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="SELL",
        confidence=80,
        rationale="legacy sell",
        quantity=10,
        price=120.0,
        market="US_NASDAQ",
        exchange_code="",
        decision_id="legacy-sell",
    )
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="SELL",
        confidence=85,
        rationale="matched sell",
        quantity=5,
        price=125.0,
        market="US_NASDAQ",
        exchange_code="NASD",
        decision_id="matched-sell",
    )
    conn.execute(
        "UPDATE trades SET timestamp = ? WHERE decision_id IN (?, ?)",
        ("2026-03-20T00:00:00+00:00", "legacy-sell", "matched-sell"),
    )
    conn.commit()

    matched = get_latest_sell_trade(
        conn,
        stock_code="AAPL",
        market="US_NASDAQ",
        exchange_code="NASD",
    )
    assert matched is not None
    assert matched["decision_id"] == "matched-sell"
    assert matched["price"] == 125.0
    assert matched["timestamp"] == "2026-03-20T00:00:00+00:00"


def test_get_latest_sell_trade_returns_latest_row_when_exchange_code_is_none() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="SELL",
        confidence=80,
        rationale="older sell",
        quantity=10,
        price=120.0,
        market="US_NASDAQ",
        exchange_code="NASD",
        decision_id="older-sell",
    )
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="SELL",
        confidence=85,
        rationale="latest legacy sell",
        quantity=5,
        price=121.0,
        market="US_NASDAQ",
        exchange_code="",
        decision_id="latest-sell",
    )
    conn.execute(
        "UPDATE trades SET timestamp = ? WHERE decision_id = ?",
        ("2026-03-20T00:00:00+00:00", "older-sell"),
    )
    conn.execute(
        "UPDATE trades SET timestamp = ? WHERE decision_id = ?",
        ("2026-03-20T00:01:00+00:00", "latest-sell"),
    )
    conn.commit()

    matched = get_latest_sell_trade(
        conn,
        stock_code="AAPL",
        market="US_NASDAQ",
        exchange_code=None,
    )

    assert matched is not None
    assert matched["decision_id"] == "latest-sell"
    assert matched["price"] == 121.0
    assert matched["timestamp"] == "2026-03-20T00:01:00+00:00"


def test_get_latest_sell_trade_prefers_latest_timestamp_before_exchange_code() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="SELL",
        confidence=80,
        rationale="older matched sell",
        quantity=10,
        price=120.0,
        market="US_NASDAQ",
        exchange_code="NASD",
        decision_id="older-matched-sell",
    )
    log_trade(
        conn=conn,
        stock_code="AAPL",
        action="SELL",
        confidence=85,
        rationale="newer legacy sell",
        quantity=5,
        price=121.0,
        market="US_NASDAQ",
        exchange_code="",
        decision_id="newer-legacy-sell",
    )
    conn.execute(
        "UPDATE trades SET timestamp = ? WHERE decision_id = ?",
        ("2026-03-20T00:00:00+00:00", "older-matched-sell"),
    )
    conn.execute(
        "UPDATE trades SET timestamp = ? WHERE decision_id = ?",
        ("2026-03-20T00:01:00+00:00", "newer-legacy-sell"),
    )
    conn.commit()

    matched = get_latest_sell_trade(
        conn,
        stock_code="AAPL",
        market="US_NASDAQ",
        exchange_code="NASD",
    )

    assert matched is not None
    assert matched["decision_id"] == "newer-legacy-sell"
    assert matched["price"] == 121.0
    assert matched["timestamp"] == "2026-03-20T00:01:00+00:00"


def test_decision_logs_session_id_migration_backfills_unknown() -> None:
    import sqlite3

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        old_conn = sqlite3.connect(db_path)
        old_conn.execute(
            """
            CREATE TABLE decision_logs (
                decision_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                market TEXT NOT NULL,
                exchange_code TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                rationale TEXT NOT NULL,
                context_snapshot TEXT NOT NULL,
                input_data TEXT NOT NULL
            )
            """
        )
        old_conn.execute(
            """
            INSERT INTO decision_logs (
                decision_id, timestamp, stock_code, market, exchange_code,
                action, confidence, rationale, context_snapshot, input_data
            ) VALUES (
                'd1', '2026-01-01T00:00:00+00:00', 'AAPL', 'US_NASDAQ', 'NASD',
                'BUY', 80, 'legacy row', '{}', '{}'
            )
            """
        )
        old_conn.commit()
        old_conn.close()

        conn = init_db(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(decision_logs)").fetchall()}
        assert "session_id" in columns
        row = conn.execute("SELECT session_id FROM decision_logs WHERE decision_id='d1'").fetchone()
        assert row is not None
        assert row[0] == "UNKNOWN"
        conn.close()
    finally:
        os.unlink(db_path)
