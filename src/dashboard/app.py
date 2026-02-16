"""FastAPI application for observability dashboard endpoints."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse


def create_dashboard_app(db_path: str) -> FastAPI:
    """Create dashboard FastAPI app bound to a SQLite database path."""
    app = FastAPI(title="The Ouroboros Dashboard", version="1.0.0")
    app.state.db_path = db_path

    @app.get("/")
    def index() -> FileResponse:
        index_path = Path(__file__).parent / "static" / "index.html"
        return FileResponse(index_path)

    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        today = datetime.now(UTC).date().isoformat()
        with _connect(db_path) as conn:
            market_rows = conn.execute(
                """
                SELECT DISTINCT market FROM (
                    SELECT market FROM trades WHERE DATE(timestamp) = ?
                    UNION
                    SELECT market FROM decision_logs WHERE DATE(timestamp) = ?
                    UNION
                    SELECT market FROM playbooks WHERE date = ?
                ) ORDER BY market
                """,
                (today, today, today),
            ).fetchall()
            markets = [row[0] for row in market_rows] if market_rows else []
            market_status: dict[str, Any] = {}
            total_trades = 0
            total_pnl = 0.0
            total_decisions = 0
            for market in markets:
                trade_row = conn.execute(
                    """
                    SELECT COUNT(*) AS c, COALESCE(SUM(pnl), 0.0) AS p
                    FROM trades
                    WHERE DATE(timestamp) = ? AND market = ?
                    """,
                    (today, market),
                ).fetchone()
                decision_row = conn.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM decision_logs
                    WHERE DATE(timestamp) = ? AND market = ?
                    """,
                    (today, market),
                ).fetchone()
                playbook_row = conn.execute(
                    """
                    SELECT status
                    FROM playbooks
                    WHERE date = ? AND market = ?
                    LIMIT 1
                    """,
                    (today, market),
                ).fetchone()
                market_status[market] = {
                    "trade_count": int(trade_row["c"] if trade_row else 0),
                    "total_pnl": float(trade_row["p"] if trade_row else 0.0),
                    "decision_count": int(decision_row["c"] if decision_row else 0),
                    "playbook_status": playbook_row["status"] if playbook_row else None,
                }
                total_trades += market_status[market]["trade_count"]
                total_pnl += market_status[market]["total_pnl"]
                total_decisions += market_status[market]["decision_count"]

            return {
                "date": today,
                "markets": market_status,
                "totals": {
                    "trade_count": total_trades,
                    "total_pnl": round(total_pnl, 2),
                    "decision_count": total_decisions,
                },
            }

    @app.get("/api/playbook/{date_str}")
    def get_playbook(date_str: str, market: str = Query("KR")) -> dict[str, Any]:
        with _connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT date, market, status, playbook_json, generated_at,
                       token_count, scenario_count, match_count
                FROM playbooks
                WHERE date = ? AND market = ?
                """,
                (date_str, market),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="playbook not found")
            return {
                "date": row["date"],
                "market": row["market"],
                "status": row["status"],
                "playbook": json.loads(row["playbook_json"]),
                "generated_at": row["generated_at"],
                "token_count": row["token_count"],
                "scenario_count": row["scenario_count"],
                "match_count": row["match_count"],
            }

    @app.get("/api/scorecard/{date_str}")
    def get_scorecard(date_str: str, market: str = Query("KR")) -> dict[str, Any]:
        key = f"scorecard_{market}"
        with _connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT value
                FROM contexts
                WHERE layer = 'L6_DAILY' AND timeframe = ? AND key = ?
                """,
                (date_str, key),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="scorecard not found")
            return {"date": date_str, "market": market, "scorecard": json.loads(row["value"])}

    @app.get("/api/performance")
    def get_performance(market: str = Query("all")) -> dict[str, Any]:
        with _connect(db_path) as conn:
            if market == "all":
                by_market_rows = conn.execute(
                    """
                    SELECT market,
                           COUNT(*) AS total_trades,
                           SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                           SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                           COALESCE(SUM(pnl), 0.0) AS total_pnl,
                           COALESCE(AVG(confidence), 0.0) AS avg_confidence
                    FROM trades
                    GROUP BY market
                    ORDER BY market
                    """
                ).fetchall()
                combined = _performance_from_rows(by_market_rows)
                return {
                    "market": "all",
                    "combined": combined,
                    "by_market": [
                        _row_to_performance(row)
                        for row in by_market_rows
                    ],
                }

            row = conn.execute(
                """
                SELECT market,
                       COUNT(*) AS total_trades,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
                       COALESCE(SUM(pnl), 0.0) AS total_pnl,
                       COALESCE(AVG(confidence), 0.0) AS avg_confidence
                FROM trades
                WHERE market = ?
                GROUP BY market
                """,
                (market,),
            ).fetchone()
            if row is None:
                return {"market": market, "metrics": _empty_performance(market)}
            return {"market": market, "metrics": _row_to_performance(row)}

    @app.get("/api/context/{layer}")
    def get_context_layer(
        layer: str,
        timeframe: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        with _connect(db_path) as conn:
            if timeframe is None:
                rows = conn.execute(
                    """
                    SELECT timeframe, key, value, updated_at
                    FROM contexts
                    WHERE layer = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (layer, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT timeframe, key, value, updated_at
                    FROM contexts
                    WHERE layer = ? AND timeframe = ?
                    ORDER BY key
                    LIMIT ?
                    """,
                    (layer, timeframe, limit),
                ).fetchall()

            entries = [
                {
                    "timeframe": row["timeframe"],
                    "key": row["key"],
                    "value": json.loads(row["value"]),
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]
            return {
                "layer": layer,
                "timeframe": timeframe,
                "count": len(entries),
                "entries": entries,
            }

    @app.get("/api/decisions")
    def get_decisions(
        market: str = Query("KR"),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        with _connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT decision_id, timestamp, stock_code, market, exchange_code,
                       action, confidence, rationale, context_snapshot, input_data,
                       outcome_pnl, outcome_accuracy
                FROM decision_logs
                WHERE market = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (market, limit),
            ).fetchall()
            decisions = []
            for row in rows:
                decisions.append(
                    {
                        "decision_id": row["decision_id"],
                        "timestamp": row["timestamp"],
                        "stock_code": row["stock_code"],
                        "market": row["market"],
                        "exchange_code": row["exchange_code"],
                        "action": row["action"],
                        "confidence": row["confidence"],
                        "rationale": row["rationale"],
                        "context_snapshot": json.loads(row["context_snapshot"]),
                        "input_data": json.loads(row["input_data"]),
                        "outcome_pnl": row["outcome_pnl"],
                        "outcome_accuracy": row["outcome_accuracy"],
                    }
                )
            return {"market": market, "count": len(decisions), "decisions": decisions}

    @app.get("/api/scenarios/active")
    def get_active_scenarios(
        market: str = Query("US"),
        date_str: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        if date_str is None:
            date_str = datetime.now(UTC).date().isoformat()

        with _connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT timestamp, stock_code, action, confidence, rationale, context_snapshot
                FROM decision_logs
                WHERE market = ? AND DATE(timestamp) = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (market, date_str, limit),
            ).fetchall()
            matches: list[dict[str, Any]] = []
            for row in rows:
                snapshot = json.loads(row["context_snapshot"])
                scenario_match = snapshot.get("scenario_match", {})
                if not isinstance(scenario_match, dict) or not scenario_match:
                    continue
                matches.append(
                    {
                        "timestamp": row["timestamp"],
                        "stock_code": row["stock_code"],
                        "action": row["action"],
                        "confidence": row["confidence"],
                        "rationale": row["rationale"],
                        "scenario_match": scenario_match,
                    }
                )
            return {"market": market, "date": date_str, "count": len(matches), "matches": matches}

    return app


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_performance(row: sqlite3.Row) -> dict[str, Any]:
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    total = int(row["total_trades"] or 0)
    win_rate = round((wins / (wins + losses) * 100), 2) if (wins + losses) > 0 else 0.0
    return {
        "market": row["market"],
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": round(float(row["total_pnl"] or 0.0), 2),
        "avg_confidence": round(float(row["avg_confidence"] or 0.0), 2),
    }


def _performance_from_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
    total_trades = 0
    wins = 0
    losses = 0
    total_pnl = 0.0
    confidence_weighted = 0.0
    for row in rows:
        market_total = int(row["total_trades"] or 0)
        market_conf = float(row["avg_confidence"] or 0.0)
        total_trades += market_total
        wins += int(row["wins"] or 0)
        losses += int(row["losses"] or 0)
        total_pnl += float(row["total_pnl"] or 0.0)
        confidence_weighted += market_total * market_conf
    win_rate = round((wins / (wins + losses) * 100), 2) if (wins + losses) > 0 else 0.0
    avg_confidence = round(confidence_weighted / total_trades, 2) if total_trades > 0 else 0.0
    return {
        "market": "all",
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "avg_confidence": avg_confidence,
    }


def _empty_performance(market: str) -> dict[str, Any]:
    return {
        "market": market,
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
        "avg_confidence": 0.0,
    }
