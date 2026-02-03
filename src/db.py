"""Database layer for trade logging."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize the trade logs database and return a connection."""
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
            pnl REAL DEFAULT 0.0
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
) -> None:
    """Insert a trade record into the database."""
    conn.execute(
        """
        INSERT INTO trades (timestamp, stock_code, action, confidence, rationale, quantity, price, pnl)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            stock_code,
            action,
            confidence,
            rationale,
            quantity,
            price,
            pnl,
        ),
    )
    conn.commit()
