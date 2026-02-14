"""Tests for FastAPI dashboard endpoints."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from src.dashboard.app import create_dashboard_app
from src.db import init_db


def _seed_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO playbooks (
            date, market, status, playbook_json, generated_at,
            token_count, scenario_count, match_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-02-14",
            "KR",
            "ready",
            json.dumps({"market": "KR", "stock_playbooks": []}),
            "2026-02-14T08:30:00+00:00",
            123,
            2,
            1,
        ),
    )
    conn.execute(
        """
        INSERT INTO contexts (layer, timeframe, key, value, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "L6_DAILY",
            "2026-02-14",
            "scorecard_KR",
            json.dumps({"market": "KR", "total_pnl": 1.5, "win_rate": 60.0}),
            "2026-02-14T15:30:00+00:00",
            "2026-02-14T15:30:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO contexts (layer, timeframe, key, value, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "L7_REALTIME",
            "2026-02-14T10:00:00+00:00",
            "volatility_KR_005930",
            json.dumps({"momentum_score": 70.0}),
            "2026-02-14T10:00:00+00:00",
            "2026-02-14T10:00:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO decision_logs (
            decision_id, timestamp, stock_code, market, exchange_code,
            action, confidence, rationale, context_snapshot, input_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "d-kr-1",
            "2026-02-14T09:10:00+00:00",
            "005930",
            "KR",
            "KRX",
            "BUY",
            85,
            "signal matched",
            json.dumps({"scenario_match": {"rsi": 28.0}}),
            json.dumps({"current_price": 70000}),
        ),
    )
    conn.execute(
        """
        INSERT INTO decision_logs (
            decision_id, timestamp, stock_code, market, exchange_code,
            action, confidence, rationale, context_snapshot, input_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "d-us-1",
            "2026-02-14T21:10:00+00:00",
            "AAPL",
            "US",
            "NASDAQ",
            "SELL",
            80,
            "no match",
            json.dumps({"scenario_match": {}}),
            json.dumps({"current_price": 200}),
        ),
    )
    conn.execute(
        """
        INSERT INTO trades (
            timestamp, stock_code, action, confidence, rationale,
            quantity, price, pnl, market, exchange_code, selection_context, decision_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-02-14T09:11:00+00:00",
            "005930",
            "BUY",
            85,
            "buy",
            1,
            70000,
            2.0,
            "KR",
            "KRX",
            None,
            "d-kr-1",
        ),
    )
    conn.execute(
        """
        INSERT INTO trades (
            timestamp, stock_code, action, confidence, rationale,
            quantity, price, pnl, market, exchange_code, selection_context, decision_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-02-14T21:11:00+00:00",
            "AAPL",
            "SELL",
            80,
            "sell",
            1,
            200,
            -1.0,
            "US",
            "NASDAQ",
            None,
            "d-us-1",
        ),
    )
    conn.commit()


def _client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "dashboard_test.db"
    conn = init_db(str(db_path))
    _seed_db(conn)
    conn.close()
    app = create_dashboard_app(str(db_path))
    return TestClient(app)


def test_index_serves_html(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "The Ouroboros Dashboard API" in resp.text


def test_status_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "KR" in body["markets"]
    assert "US" in body["markets"]
    assert "totals" in body


def test_playbook_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/playbook/2026-02-14?market=KR")
    assert resp.status_code == 200
    assert resp.json()["market"] == "KR"


def test_playbook_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/playbook/2026-02-15?market=KR")
    assert resp.status_code == 404


def test_scorecard_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/scorecard/2026-02-14?market=KR")
    assert resp.status_code == 200
    assert resp.json()["scorecard"]["total_pnl"] == 1.5


def test_scorecard_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/scorecard/2026-02-15?market=KR")
    assert resp.status_code == 404


def test_performance_all(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/performance?market=all")
    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "all"
    assert body["combined"]["total_trades"] == 2
    assert len(body["by_market"]) == 2


def test_performance_market_filter(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/performance?market=KR")
    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KR"
    assert body["metrics"]["total_trades"] == 1


def test_performance_empty_market(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/performance?market=JP")
    assert resp.status_code == 200
    assert resp.json()["metrics"]["total_trades"] == 0


def test_context_layer_all(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/context/L7_REALTIME")
    assert resp.status_code == 200
    body = resp.json()
    assert body["layer"] == "L7_REALTIME"
    assert body["count"] == 1


def test_context_layer_timeframe_filter(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/context/L6_DAILY?timeframe=2026-02-14")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["entries"][0]["key"] == "scorecard_KR"


def test_decisions_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/decisions?market=KR")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["decisions"][0]["decision_id"] == "d-kr-1"


def test_scenarios_active_filters_non_matched(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/scenarios/active?market=KR&date_str=2026-02-14")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["matches"][0]["stock_code"] == "005930"


def test_scenarios_active_empty_when_no_matches(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/api/scenarios/active?market=US&date_str=2026-02-14")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
