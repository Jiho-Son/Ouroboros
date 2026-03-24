"""FastAPI application for observability dashboard endpoints."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse


def create_dashboard_app(db_path: str, mode: str = "paper") -> FastAPI:
    """Create dashboard FastAPI app bound to a SQLite database path."""
    app = FastAPI(title="The Ouroboros Dashboard", version="1.0.0")
    app.state.db_path = db_path
    app.state.mode = mode

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
                    ORDER BY generated_at DESC
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

            cb_threshold = float(os.getenv("CIRCUIT_BREAKER_PCT", "-3.0"))
            pnl_pct_rows = conn.execute(
                """
                SELECT key, value
                FROM system_metrics
                WHERE key LIKE 'portfolio_pnl_pct_%'
                ORDER BY updated_at DESC
                LIMIT 20
                """
            ).fetchall()
            current_pnl_pct: float | None = None
            if pnl_pct_rows:
                values = [
                    json.loads(row["value"]).get("pnl_pct")
                    for row in pnl_pct_rows
                    if json.loads(row["value"]).get("pnl_pct") is not None
                ]
                if values:
                    current_pnl_pct = round(min(values), 4)

            if current_pnl_pct is None:
                cb_status = "unknown"
            elif current_pnl_pct <= cb_threshold:
                cb_status = "tripped"
            elif current_pnl_pct <= cb_threshold + 1.0:
                cb_status = "warning"
            else:
                cb_status = "ok"

            return {
                "date": today,
                "mode": mode,
                "markets": market_status,
                "totals": {
                    "trade_count": total_trades,
                    "total_pnl": round(total_pnl, 2),
                    "decision_count": total_decisions,
                },
                "circuit_breaker": {
                    "threshold_pct": cb_threshold,
                    "current_pnl_pct": current_pnl_pct,
                    "status": cb_status,
                },
            }

    @app.get("/api/playbook/{date_str}")
    def get_playbook(
        date_str: str,
        market: str = Query("KR"),
        slot: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        with _connect(db_path) as conn:
            if slot is not None:
                row = conn.execute(
                    """
                    SELECT date, market, slot, status, playbook_json, generated_at,
                           token_count, scenario_count, match_count
                    FROM playbooks
                    WHERE date = ? AND market = ? AND slot = ?
                    """,
                    (date_str, market, slot),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT date, market, slot, status, playbook_json, generated_at,
                           token_count, scenario_count, match_count
                    FROM playbooks
                    WHERE date = ? AND market = ?
                    ORDER BY generated_at DESC
                    LIMIT 1
                    """,
                    (date_str, market),
                ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="playbook not found")
            return {
                "date": row["date"],
                "market": row["market"],
                "slot": row["slot"],
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
                    "by_market": [_row_to_performance(row) for row in by_market_rows],
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
        session_id: str = Query("all"),
        action: str = Query("all"),
        stock_code: str | None = Query(default=None),
        min_confidence: int = Query(default=0, ge=0, le=100),
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        matched_only: bool = Query(default=False),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        market = market if isinstance(market, str) else "KR"
        session_id = session_id if isinstance(session_id, str) else "all"
        action = action if isinstance(action, str) else "all"
        stock_code = stock_code if isinstance(stock_code, str) else None
        min_confidence = min_confidence if isinstance(min_confidence, int) else 0
        from_date = from_date if isinstance(from_date, str) else None
        to_date = to_date if isinstance(to_date, str) else None
        matched_only = matched_only if isinstance(matched_only, bool) else False
        limit = limit if isinstance(limit, int) else 50
        with _connect(db_path) as conn:
            where_clauses: list[str] = []
            params: list[Any] = []
            if market != "all":
                where_clauses.append("market = ?")
                params.append(market)
            if session_id != "all":
                where_clauses.append("session_id = ?")
                params.append(session_id)
            if action != "all":
                where_clauses.append("action = ?")
                params.append(action)
            if stock_code:
                where_clauses.append("stock_code LIKE ?")
                params.append(f"%{stock_code.upper()}%")
            if min_confidence > 0:
                where_clauses.append("confidence >= ?")
                params.append(min_confidence)
            if from_date:
                where_clauses.append("DATE(timestamp) >= ?")
                params.append(from_date)
            if to_date:
                where_clauses.append("DATE(timestamp) <= ?")
                params.append(to_date)

            where_sql = ""
            if where_clauses:
                where_sql = "WHERE " + " AND ".join(where_clauses)
            rows = conn.execute(
                f"""
                SELECT decision_id, timestamp, stock_code, market, exchange_code,
                       session_id, action, confidence, rationale, context_snapshot, input_data,
                       llm_prompt, llm_response, outcome_pnl, outcome_accuracy
                FROM decision_logs
                {where_sql}
                ORDER BY timestamp DESC
                """,
                params,
            ).fetchall()
            decisions = []
            for row in rows:
                context_snapshot = json.loads(row["context_snapshot"])
                scenario_match = context_snapshot.get("scenario_match", {})
                has_match = isinstance(scenario_match, dict) and bool(scenario_match)
                if matched_only and not has_match:
                    continue
                decisions.append(
                    {
                        "decision_id": row["decision_id"],
                        "timestamp": row["timestamp"],
                        "stock_code": row["stock_code"],
                        "market": row["market"],
                        "exchange_code": row["exchange_code"],
                        "session_id": row["session_id"],
                        "action": row["action"],
                        "confidence": row["confidence"],
                        "rationale": row["rationale"],
                        "context_snapshot": context_snapshot,
                        "input_data": json.loads(row["input_data"]),
                        "llm_prompt": row["llm_prompt"],
                        "llm_response": row["llm_response"],
                        "has_scenario_match": has_match,
                        "outcome_pnl": row["outcome_pnl"],
                        "outcome_accuracy": row["outcome_accuracy"],
                    }
                )
            decisions = decisions[:limit]
            markets = [
                row["market"]
                for row in conn.execute(
                    "SELECT DISTINCT market FROM decision_logs ORDER BY market"
                ).fetchall()
            ]
            sessions = [
                row["session_id"]
                for row in conn.execute(
                    "SELECT DISTINCT session_id FROM decision_logs ORDER BY session_id"
                ).fetchall()
            ]
            return {
                "market": market,
                "count": len(decisions),
                "filters": {
                    "market": market,
                    "session_id": session_id,
                    "action": action,
                    "stock_code": stock_code or "",
                    "min_confidence": min_confidence,
                    "from_date": from_date,
                    "to_date": to_date,
                    "matched_only": matched_only,
                    "limit": limit,
                },
                "markets": markets,
                "sessions": sessions,
                "decisions": decisions,
            }

    @app.get("/api/pnl/history")
    def get_pnl_history(
        days: int = Query(default=30, ge=1, le=365),
        market: str = Query("all"),
    ) -> dict[str, Any]:
        """Return daily P&L history for charting."""
        with _connect(db_path) as conn:
            if market == "all":
                rows = conn.execute(
                    """
                    SELECT DATE(timestamp) AS date,
                           SUM(pnl) AS daily_pnl,
                           COUNT(*) AS trade_count
                    FROM trades
                    WHERE pnl IS NOT NULL
                      AND DATE(timestamp) >= DATE('now', ?)
                    GROUP BY DATE(timestamp)
                    ORDER BY DATE(timestamp)
                    """,
                    (f"-{days} days",),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT DATE(timestamp) AS date,
                           SUM(pnl) AS daily_pnl,
                           COUNT(*) AS trade_count
                    FROM trades
                    WHERE pnl IS NOT NULL
                      AND market = ?
                      AND DATE(timestamp) >= DATE('now', ?)
                    GROUP BY DATE(timestamp)
                    ORDER BY DATE(timestamp)
                    """,
                    (market, f"-{days} days"),
                ).fetchall()
            return {
                "days": days,
                "market": market,
                "labels": [row["date"] for row in rows],
                "pnl": [round(float(row["daily_pnl"]), 2) for row in rows],
                "trades": [int(row["trade_count"]) for row in rows],
            }

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

    @app.get("/api/positions")
    def get_positions() -> dict[str, Any]:
        """Return all currently open positions (last trade per symbol is BUY)."""
        with _connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT stock_code, market, exchange_code,
                       price AS entry_price, quantity, timestamp AS entry_time,
                       decision_id
                FROM (
                    SELECT stock_code, market, exchange_code, price, quantity,
                           timestamp, decision_id, action,
                           ROW_NUMBER() OVER (
                               PARTITION BY stock_code, market
                               ORDER BY timestamp DESC
                           ) AS rn
                    FROM trades
                )
                WHERE rn = 1 AND action = 'BUY'
                ORDER BY entry_time DESC
                """
            ).fetchall()

            now = datetime.now(UTC)
            positions = []
            for row in rows:
                entry_time_str = row["entry_time"]
                try:
                    entry_dt = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
                    held_seconds = int((now - entry_dt).total_seconds())
                    held_hours = held_seconds // 3600
                    held_minutes = (held_seconds % 3600) // 60
                    if held_hours >= 1:
                        held_display = f"{held_hours}h {held_minutes}m"
                    else:
                        held_display = f"{held_minutes}m"
                except (ValueError, TypeError):
                    held_display = "--"

                positions.append(
                    {
                        "stock_code": row["stock_code"],
                        "market": row["market"],
                        "exchange_code": row["exchange_code"],
                        "entry_price": row["entry_price"],
                        "quantity": row["quantity"],
                        "entry_time": entry_time_str,
                        "held": held_display,
                        "decision_id": row["decision_id"],
                    }
                )

            return {"count": len(positions), "positions": positions}

    return app


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
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
