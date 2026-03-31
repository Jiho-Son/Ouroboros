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

from src.db import init_db

_DASHBOARD_MARKET_GROUPS: dict[str, tuple[str, ...]] = {
    "US": ("US_NASDAQ", "US_NYSE", "US_AMEX"),
}


def create_dashboard_app(
    db_path: str,
    mode: str = "paper",
    runtime_status_provider: Any | None = None,
) -> FastAPI:
    """Create dashboard FastAPI app bound to a SQLite database path."""
    app = FastAPI(title="The Ouroboros Dashboard", version="1.0.0")
    app.state.db_path = db_path
    app.state.mode = mode
    app.state.runtime_status_provider = runtime_status_provider

    @app.get("/")
    def index() -> FileResponse:
        index_path = Path(__file__).parent / "static" / "index.html"
        return FileResponse(index_path)

    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        today = datetime.now(UTC).date().isoformat()
        provider = app.state.runtime_status_provider
        runtime_status = _group_runtime_status(provider() if provider is not None else {})
        with _connect(db_path) as conn:
            trade_rows = {
                row["market"]: row
                for row in conn.execute(
                    """
                    SELECT market,
                           COUNT(*) AS trade_count,
                           COALESCE(SUM(pnl), 0.0) AS total_pnl
                    FROM trades
                    WHERE DATE(timestamp) = ?
                    GROUP BY market
                    ORDER BY market
                    """,
                    (today,),
                ).fetchall()
            }
            decision_rows = {
                row["market"]: row
                for row in conn.execute(
                    """
                    SELECT market, COUNT(*) AS decision_count
                    FROM decision_logs
                    WHERE DATE(timestamp) = ?
                    GROUP BY market
                    ORDER BY market
                    """,
                    (today,),
                ).fetchall()
            }
            playbook_rows = {
                row["market"]: row["status"]
                for row in conn.execute(
                    """
                    SELECT market, status
                    FROM (
                        SELECT market,
                               status,
                               ROW_NUMBER() OVER (
                                   PARTITION BY market
                                   ORDER BY generated_at DESC
                               ) AS rn
                        FROM playbooks
                        WHERE date = ?
                    )
                    WHERE rn = 1
                    ORDER BY market
                    """,
                    (today,),
                ).fetchall()
            }
            position_rows = {
                row["market"]: int(row["open_position_count"])
                for row in conn.execute(
                    """
                    -- Count symbols whose latest trade is still BUY to reflect
                    -- the current open-position inventory per market.
                    SELECT market, COUNT(*) AS open_position_count
                    FROM (
                        SELECT market,
                               action,
                               ROW_NUMBER() OVER (
                                   PARTITION BY stock_code, market
                                   ORDER BY timestamp DESC
                               ) AS rn
                        FROM trades
                    )
                    WHERE rn = 1 AND action = 'BUY'
                    GROUP BY market
                    ORDER BY market
                    """
                ).fetchall()
            }
            latest_decision_rows = {
                row["market"]: row
                for row in conn.execute(
                    """
                    SELECT market, timestamp, action, session_id
                    FROM (
                        SELECT market,
                               timestamp,
                               action,
                               session_id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY market
                                   ORDER BY timestamp DESC
                               ) AS rn
                        FROM decision_logs
                    )
                    WHERE rn = 1
                    ORDER BY market
                    """
                ).fetchall()
            }
            latest_trade = conn.execute(
                """
                -- External freshness monitors need the most recent persisted activity
                -- across the full runtime history, not only today's rows.
                SELECT market, timestamp, action
                FROM trades
                ORDER BY timestamp DESC
                LIMIT 1
                """
            ).fetchone()
            latest_decision = conn.execute(
                """
                -- External freshness monitors need the most recent persisted activity
                -- across the full runtime history, not only today's rows.
                SELECT market, timestamp, action, session_id
                FROM decision_logs
                ORDER BY timestamp DESC
                LIMIT 1
                """
            ).fetchone()
            market_pnl_pct = _load_market_pnl_pct(conn)

            activity_rows = conn.execute(
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
            activity_markets = {row[0] for row in activity_rows}
            raw_markets = sorted(activity_markets | set(position_rows))
            market_status: dict[str, Any] = {}
            total_trades = 0
            total_pnl_raw = 0.0
            total_decisions = 0
            cb_threshold = float(os.getenv("CIRCUIT_BREAKER_PCT", "-3.0"))
            for raw_market in raw_markets:
                market = _dashboard_market_code(raw_market)
                trade_row = trade_rows.get(raw_market)
                decision_row = decision_rows.get(raw_market)
                latest_decision = latest_decision_rows.get(raw_market)
                bucket = market_status.setdefault(
                    market,
                    {
                        "trade_count": 0,
                        "total_pnl": 0.0,
                        "decision_count": 0,
                        "playbook_status": None,
                        "open_position_count": 0,
                        "latest_decision_at": None,
                        "latest_decision_action": None,
                        "latest_session_id": None,
                        "runtime_tracking": runtime_status.get(market),
                        "_pnl_samples": [],
                    },
                )
                bucket["trade_count"] += int(trade_row["trade_count"] if trade_row else 0)
                bucket["total_pnl"] += float(trade_row["total_pnl"] if trade_row else 0.0)
                bucket["decision_count"] += int(
                    decision_row["decision_count"] if decision_row is not None else 0
                )
                bucket["open_position_count"] += position_rows.get(raw_market, 0)
                bucket["playbook_status"] = _merge_playbook_status(
                    bucket["playbook_status"], playbook_rows.get(raw_market)
                )
                current_market_pnl_pct = market_pnl_pct.get(raw_market)
                if current_market_pnl_pct is not None:
                    bucket["_pnl_samples"].append(current_market_pnl_pct)
                if latest_decision is not None and _is_newer_timestamp(
                    latest_decision["timestamp"], bucket["latest_decision_at"]
                ):
                    bucket["latest_decision_at"] = latest_decision["timestamp"]
                    bucket["latest_decision_action"] = latest_decision["action"]
                    bucket["latest_session_id"] = latest_decision["session_id"]

            for market, bucket in market_status.items():
                pnl_samples = bucket.pop("_pnl_samples")
                # For grouped markets like US, expose the worst live P&L% so the
                # overview card stays aligned with circuit-breaker risk semantics.
                current_market_pnl_pct = round(min(pnl_samples), 4) if pnl_samples else None
                market_cb_status = _cb_status_from_pnl_pct(cb_threshold, current_market_pnl_pct)
                bucket["current_pnl_pct"] = current_market_pnl_pct
                bucket["circuit_breaker_status"] = market_cb_status
                bucket["status_tone"] = _market_status_tone(
                    circuit_breaker_status=market_cb_status,
                    open_position_count=bucket["open_position_count"],
                    decision_count=bucket["decision_count"],
                    playbook_status=bucket["playbook_status"],
                )
                total_pnl_raw += bucket["total_pnl"]
                bucket["total_pnl"] = round(bucket["total_pnl"], 2)
                total_trades += bucket["trade_count"]
                total_decisions += bucket["decision_count"]

            current_pnl_pct = round(min(market_pnl_pct.values()), 4) if market_pnl_pct else None
            cb_status = _cb_status_from_pnl_pct(cb_threshold, current_pnl_pct)

            return {
                "date": today,
                "mode": mode,
                "activity": _build_activity_summary(
                    latest_trade=latest_trade,
                    latest_decision=latest_decision,
                ),
                "markets": market_status,
                "totals": {
                    "trade_count": total_trades,
                    "total_pnl": round(total_pnl_raw, 2),
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
        with _connect(db_path) as conn:
            where_clauses: list[str] = []
            params: list[Any] = []
            _append_market_filter(where_clauses, params, market)
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
            if matched_only:
                where_clauses.append(
                    "json_extract(context_snapshot, '$.scenario_match') IS NOT NULL"
                )
                where_clauses.append("json_extract(context_snapshot, '$.scenario_match') != '{}'")

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
                LIMIT ?
                """,
                (*params, limit),
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
                        "market": _dashboard_market_code(row["market"]),
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
            markets = sorted(
                {
                    _dashboard_market_code(row["market"])
                    for row in conn.execute("SELECT DISTINCT market FROM decision_logs").fetchall()
                }
            )
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
                raw_markets = _dashboard_market_filter_values(market)
                placeholders = ",".join("?" for _ in raw_markets)
                rows = conn.execute(
                    f"""
                    SELECT DATE(timestamp) AS date,
                           SUM(pnl) AS daily_pnl,
                           COUNT(*) AS trade_count
                    FROM trades
                    WHERE pnl IS NOT NULL
                      AND market IN ({placeholders})
                      AND DATE(timestamp) >= DATE('now', ?)
                    GROUP BY DATE(timestamp)
                    ORDER BY DATE(timestamp)
                    """,
                    (*raw_markets, f"-{days} days"),
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
                        "market": _dashboard_market_code(row["market"]),
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
    # Dashboard readers must tolerate a fresh DB file before the trading loop
    # has written the first trade by bootstrapping the shared schema contract.
    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row
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


def _load_market_pnl_pct(conn: sqlite3.Connection) -> dict[str, float]:
    # system_metrics.key is PRIMARY KEY and written via INSERT OR REPLACE,
    # so at most one row exists per market key. LIMIT 20 is a conservative
    # guard in case the schema assumption ever changes.
    rows = conn.execute(
        """
        SELECT key, value
        FROM system_metrics
        WHERE key LIKE 'portfolio_pnl_pct_%'
        ORDER BY updated_at DESC
        LIMIT 20
        """
    ).fetchall()
    market_pnl_pct: dict[str, float] = {}
    prefix = "portfolio_pnl_pct_"
    for row in rows:
        market = row["key"].removeprefix(prefix)
        payload = json.loads(row["value"])
        pnl_pct = payload.get("pnl_pct")
        if pnl_pct is not None:
            market_pnl_pct[market] = round(float(pnl_pct), 4)
    return market_pnl_pct


def _dashboard_market_code(market: str | None) -> str:
    if not market:
        return ""
    normalized = str(market).strip().upper()
    if normalized.startswith("US_"):
        return "US"
    return normalized


def _dashboard_market_filter_values(market: str) -> tuple[str, ...]:
    normalized = _dashboard_market_code(market)
    return _DASHBOARD_MARKET_GROUPS.get(normalized, (normalized,))


def _append_market_filter(
    where_clauses: list[str],
    params: list[Any],
    market: str,
) -> None:
    if market == "all":
        return
    raw_markets = _dashboard_market_filter_values(market)
    if len(raw_markets) == 1:
        where_clauses.append("market = ?")
        params.append(raw_markets[0])
        return
    placeholders = ",".join("?" for _ in raw_markets)
    where_clauses.append(f"market IN ({placeholders})")
    params.extend(raw_markets)


def _is_newer_timestamp(candidate: str | None, current: str | None) -> bool:
    if candidate is None:
        return False
    if current is None:
        return True
    candidate_dt = _parse_timestamp(candidate)
    current_dt = _parse_timestamp(current)
    if candidate_dt is not None and current_dt is not None:
        return candidate_dt >= current_dt
    return candidate >= current


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _merge_playbook_status(current: str | None, incoming: str | None) -> str | None:
    if incoming is None:
        return current
    if current is None:
        return incoming
    severity = {
        "ready": 0,
        "pending": 1,
        "expired": 2,
        "failed": 3,
        "error": 3,
    }
    return incoming if severity.get(incoming, 2) >= severity.get(current, 2) else current


def _build_activity_summary(
    *,
    latest_trade: sqlite3.Row | None,
    latest_decision: sqlite3.Row | None,
) -> dict[str, Any]:
    trade_at = latest_trade["timestamp"] if latest_trade is not None else None
    trade_market = (
        _dashboard_market_code(latest_trade["market"]) if latest_trade is not None else None
    )
    trade_action = latest_trade["action"] if latest_trade is not None else None

    decision_at = latest_decision["timestamp"] if latest_decision is not None else None
    decision_market = (
        _dashboard_market_code(latest_decision["market"]) if latest_decision is not None else None
    )
    decision_action = latest_decision["action"] if latest_decision is not None else None
    decision_session_id = latest_decision["session_id"] if latest_decision is not None else None

    latest_observed_at = trade_at
    latest_observed_market = trade_market
    latest_observed_action = trade_action
    latest_observed_source = "trade" if trade_at is not None else None
    if _is_newer_timestamp(decision_at, trade_at):
        latest_observed_at = decision_at
        latest_observed_market = decision_market
        latest_observed_action = decision_action
        latest_observed_source = "decision"

    return {
        "latest_trade_at": trade_at,
        "latest_trade_market": trade_market,
        "latest_trade_action": trade_action,
        "latest_decision_at": decision_at,
        "latest_decision_market": decision_market,
        "latest_decision_action": decision_action,
        "latest_decision_session_id": decision_session_id,
        "latest_observed_at": latest_observed_at,
        "latest_observed_market": latest_observed_market,
        "latest_observed_action": latest_observed_action,
        "latest_observed_source": latest_observed_source,
    }


def _group_runtime_status(runtime_status: Any) -> dict[str, Any]:
    if not isinstance(runtime_status, dict):
        return {}

    grouped: dict[str, Any] = {}
    for raw_market, payload in runtime_status.items():
        market = _dashboard_market_code(str(raw_market))
        if not isinstance(payload, dict):
            grouped[market] = payload
            continue
        bucket = grouped.setdefault(
            market,
            {
                "session_id": payload.get("session_id"),
                "active_count": 0,
                "active_stocks": [],
                "candidate_count": 0,
                "candidate_codes": [],
                "last_scan_age_seconds": payload.get("last_scan_age_seconds"),
            },
        )
        bucket["active_count"] += int(payload.get("active_count") or 0)
        bucket["candidate_count"] += int(payload.get("candidate_count") or 0)
        bucket["active_stocks"] = _merge_unique_sequence(
            bucket["active_stocks"], payload.get("active_stocks") or []
        )
        bucket["candidate_codes"] = _merge_unique_sequence(
            bucket["candidate_codes"], payload.get("candidate_codes") or []
        )
        if not bucket.get("session_id"):
            bucket["session_id"] = payload.get("session_id")
        age = payload.get("last_scan_age_seconds")
        current_age = bucket.get("last_scan_age_seconds")
        if age is not None and (current_age is None or age < current_age):
            bucket["last_scan_age_seconds"] = age
    return grouped


def _merge_unique_sequence(current: list[Any], incoming: list[Any]) -> list[Any]:
    merged = list(current)
    for item in incoming:
        if item not in merged:
            merged.append(item)
    return merged


def _cb_status_from_pnl_pct(
    threshold_pct: float,
    current_pnl_pct: float | None,
) -> str:
    if current_pnl_pct is None:
        return "unknown"
    if current_pnl_pct <= threshold_pct:
        return "tripped"
    if current_pnl_pct <= threshold_pct + 1.0:
        return "warning"
    return "ok"


def _market_status_tone(
    *,
    circuit_breaker_status: str,
    open_position_count: int,
    decision_count: int,
    playbook_status: str | None,
) -> str:
    if circuit_breaker_status == "tripped":
        return "tripped"
    if circuit_breaker_status == "warning":
        return "warning"
    if open_position_count > 0:
        return "active"
    if decision_count > 0:
        return "watching"
    if playbook_status == "ready":
        return "ready"
    return "idle"
