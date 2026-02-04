"""Database layer for trade logging."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize the trade logs database and return a connection."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
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
            market TEXT DEFAULT 'KR',
            exchange_code TEXT DEFAULT 'KRX'
        )
        """
    )

    # Migration: Add market and exchange_code columns if they don't exist
    cursor = conn.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}

    if "market" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN market TEXT DEFAULT 'KR'")
    if "exchange_code" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN exchange_code TEXT DEFAULT 'KRX'")

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

    # Create indices for efficient context queries
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contexts_layer ON contexts(layer)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contexts_timeframe ON contexts(timeframe)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contexts_updated ON contexts(updated_at)")

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
    market: str = "KR",
    exchange_code: str = "KRX",
) -> None:
    """Insert a trade record into the database."""
    conn.execute(
        """
        INSERT INTO trades (
            timestamp, stock_code, action, confidence, rationale,
            quantity, price, pnl, market, exchange_code
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            market,
            exchange_code,
        ),
    )
    conn.commit()
