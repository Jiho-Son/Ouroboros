"""Database layer for trade logging."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.order_policy import classify_session_id
from src.markets.schedule import MARKETS


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize the trade logs database and return a connection."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    # Enable WAL mode for concurrent read/write (dashboard + trading loop).
    # WAL does not apply to in-memory databases.
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            action TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            rationale TEXT,
            quantity INTEGER,
            price REAL,
            pnl REAL DEFAULT 0.0,
            strategy_pnl REAL DEFAULT 0.0,
            fx_pnl REAL DEFAULT 0.0,
            market TEXT DEFAULT 'KR',
            exchange_code TEXT DEFAULT 'KRX',
            session_id TEXT DEFAULT 'UNKNOWN',
            selection_context TEXT,
            decision_id TEXT,
            mode TEXT DEFAULT 'paper'
        )
        """
    )

    # Migration: Add columns if they don't exist (backward-compatible schema upgrades)
    cursor = conn.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}

    if "market" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN market TEXT DEFAULT 'KR'")
    if "exchange_code" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN exchange_code TEXT DEFAULT 'KRX'")
    if "selection_context" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN selection_context TEXT")
    if "decision_id" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN decision_id TEXT")
    if "mode" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN mode TEXT DEFAULT 'paper'")
    if "session_id" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN session_id TEXT DEFAULT 'UNKNOWN'")
    if "strategy_pnl" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN strategy_pnl REAL DEFAULT 0.0")
    if "fx_pnl" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN fx_pnl REAL DEFAULT 0.0")
    # Backfill legacy rows where only pnl existed before split accounting columns.
    conn.execute(
        """
        UPDATE trades
        SET strategy_pnl = pnl, fx_pnl = 0.0
        WHERE pnl != 0.0
          AND strategy_pnl = 0.0
          AND fx_pnl = 0.0
        """
    )
    conn.execute(
        """
        UPDATE trades
        SET session_id = 'UNKNOWN'
        WHERE session_id IS NULL OR session_id = ''
        """
    )

    # Context tree tables for multi-layered memory management
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contexts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            layer TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(layer, timeframe, key)
        )
        """
    )

    # Decision logging table for comprehensive audit trail
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_logs (
            decision_id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            market TEXT NOT NULL,
            exchange_code TEXT NOT NULL,
            action TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            rationale TEXT NOT NULL,
            context_snapshot TEXT NOT NULL,
            input_data TEXT NOT NULL,
            outcome_pnl REAL,
            outcome_accuracy INTEGER,
            reviewed INTEGER DEFAULT 0,
            review_notes TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS context_metadata (
            layer TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            retention_days INTEGER,
            aggregation_source TEXT
        )
        """
    )

    # Playbook storage for pre-market strategy persistence
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS playbooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            market TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            playbook_json TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            token_count INTEGER DEFAULT 0,
            scenario_count INTEGER DEFAULT 0,
            match_count INTEGER DEFAULT 0,
            UNIQUE(date, market)
        )
        """
    )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_playbooks_date ON playbooks(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_playbooks_market ON playbooks(market)")

    # Create indices for efficient context queries
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contexts_layer ON contexts(layer)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contexts_timeframe ON contexts(timeframe)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contexts_updated ON contexts(updated_at)")

    # Create indices for efficient decision log queries
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_logs_timestamp ON decision_logs(timestamp)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_logs_reviewed ON decision_logs(reviewed)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_logs_confidence ON decision_logs(confidence)"
    )

    # Index for open-position queries (partition by stock_code, market, ordered by timestamp)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_trades_stock_market_ts"
        " ON trades (stock_code, market, timestamp DESC)"
    )

    # Lightweight key-value store for trading system runtime metrics (dashboard use only)
    # Intentionally separate from the AI context tree to preserve separation of concerns.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_metrics (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.commit()
    return conn


def log_trade(
    conn: sqlite3.Connection,
    stock_code: str,
    action: str,
    confidence: int,
    rationale: str,
    quantity: int = 0,
    price: float = 0.0,
    pnl: float = 0.0,
    strategy_pnl: float | None = None,
    fx_pnl: float | None = None,
    market: str = "KR",
    exchange_code: str = "KRX",
    session_id: str | None = None,
    selection_context: dict[str, any] | None = None,
    decision_id: str | None = None,
    mode: str = "paper",
) -> None:
    """Insert a trade record into the database.

    Args:
        conn: Database connection
        stock_code: Stock code
        action: Trade action (BUY/SELL/HOLD)
        confidence: Confidence level (0-100)
        rationale: AI decision rationale
        quantity: Number of shares
        price: Trade price
        pnl: Total profit/loss (backward compatibility)
        strategy_pnl: Strategy PnL component
        fx_pnl: FX PnL component
        market: Market code
        exchange_code: Exchange code
        session_id: Session identifier (if omitted, auto-derived from market)
        selection_context: Scanner selection data (RSI, volume_ratio, signal, score)
        decision_id: Unique decision identifier for audit linking
        mode: Trading mode ('paper' or 'live') for data separation
    """
    # Serialize selection context to JSON
    context_json = json.dumps(selection_context) if selection_context else None
    resolved_session_id = session_id or "UNKNOWN"
    market_info = MARKETS.get(market)
    if session_id is None and market_info is not None:
        resolved_session_id = classify_session_id(market_info)
    if strategy_pnl is None and fx_pnl is None:
        strategy_pnl = pnl
        fx_pnl = 0.0
    elif strategy_pnl is None:
        strategy_pnl = pnl - float(fx_pnl or 0.0) if pnl != 0.0 else 0.0
    elif fx_pnl is None:
        fx_pnl = pnl - float(strategy_pnl) if pnl != 0.0 else 0.0
    if pnl == 0.0 and (strategy_pnl or fx_pnl):
        pnl = float(strategy_pnl) + float(fx_pnl)

    conn.execute(
        """
        INSERT INTO trades (
            timestamp, stock_code, action, confidence, rationale,
            quantity, price, pnl, strategy_pnl, fx_pnl,
            market, exchange_code, session_id, selection_context, decision_id, mode
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(UTC).isoformat(),
            stock_code,
            action,
            confidence,
            rationale,
            quantity,
            price,
            pnl,
            strategy_pnl,
            fx_pnl,
            market,
            exchange_code,
            resolved_session_id,
            context_json,
            decision_id,
            mode,
        ),
    )
    conn.commit()


def get_latest_buy_trade(
    conn: sqlite3.Connection, stock_code: str, market: str
) -> dict[str, Any] | None:
    """Fetch the most recent BUY trade for a stock and market."""
    cursor = conn.execute(
        """
        SELECT decision_id, price, quantity
        FROM trades
        WHERE stock_code = ?
          AND market = ?
          AND action = 'BUY'
          AND decision_id IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (stock_code, market),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {"decision_id": row[0], "price": row[1], "quantity": row[2]}


def get_open_position(
    conn: sqlite3.Connection, stock_code: str, market: str
) -> dict[str, Any] | None:
    """Return open position if latest trade is BUY, else None."""
    cursor = conn.execute(
        """
        SELECT action, decision_id, price, quantity, timestamp
        FROM trades
        WHERE stock_code = ?
          AND market = ?
          AND action IN ('BUY', 'SELL')
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (stock_code, market),
    )
    row = cursor.fetchone()
    if not row or row[0] != "BUY":
        return None
    return {"decision_id": row[1], "price": row[2], "quantity": row[3], "timestamp": row[4]}


def get_recent_symbols(
    conn: sqlite3.Connection, market: str, limit: int = 30
) -> list[str]:
    """Return recent unique symbols for a market, newest first."""
    cursor = conn.execute(
        """
        SELECT stock_code, MAX(timestamp) AS last_ts
        FROM trades
        WHERE market = ?
        GROUP BY stock_code
        ORDER BY last_ts DESC
        LIMIT ?
        """,
        (market, limit),
    )
    return [row[0] for row in cursor.fetchall() if row and row[0]]
