"""Tests for dashboard endpoint handlers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from src.dashboard.app import create_dashboard_app
from src.db import init_db


def _seed_db(conn: sqlite3.Connection) -> None:
    today = datetime.now(UTC).date().isoformat()

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
        INSERT INTO playbooks (
            date, market, status, playbook_json, generated_at,
            token_count, scenario_count, match_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            today,
            "US_NASDAQ",
            "ready",
            json.dumps({"market": "US_NASDAQ", "stock_playbooks": []}),
            f"{today}T08:30:00+00:00",
            100,
            1,
            0,
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
            f"{today}T09:10:00+00:00",
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
            f"{today}T21:10:00+00:00",
            "AAPL",
            "US_NASDAQ",
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
            f"{today}T09:11:00+00:00",
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
            f"{today}T21:11:00+00:00",
            "AAPL",
            "SELL",
            80,
            "sell",
            1,
            200,
            -1.0,
            "US_NASDAQ",
            "NASDAQ",
            None,
            "d-us-1",
        ),
    )
    conn.commit()


def _seed_group_us_market_fixture(conn: sqlite3.Connection) -> None:
    today = datetime.now(UTC).date().isoformat()
    decisions = [
        ("d-kr-1", f"{today}T09:10:00+00:00", "005930", "KR", "KRX", "KRX_REG", "BUY", 2.0),
        (
            "d-us-nasdaq-1",
            f"{today}T13:10:00+00:00",
            "AAPL",
            "US_NASDAQ",
            "NASDAQ",
            "US_REG",
            "BUY",
            1.0,
        ),
        (
            "d-us-nyse-1",
            f"{today}T13:20:00+00:00",
            "IBM",
            "US_NYSE",
            "NYSE",
            "US_REG",
            "BUY",
            -0.5,
        ),
        (
            "d-us-amex-1",
            f"{today}T13:30:00+00:00",
            "SPY",
            "US_AMEX",
            "AMEX",
            "US_REG",
            "BUY",
            0.25,
        ),
    ]
    for (
        decision_id,
        timestamp,
        stock_code,
        market,
        exchange_code,
        session_id,
        action,
        pnl,
    ) in decisions:
        conn.execute(
            """
            INSERT INTO decision_logs (
                decision_id, timestamp, stock_code, market, exchange_code,
                session_id, action, confidence, rationale, context_snapshot, input_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                timestamp,
                stock_code,
                market,
                exchange_code,
                session_id,
                action,
                80,
                "group us repro",
                json.dumps({"scenario_match": {"rsi": 28.0}}),
                json.dumps({"current_price": 100}),
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
                timestamp,
                stock_code,
                action,
                80,
                "group us repro",
                1,
                100,
                pnl,
                market,
                exchange_code,
                None,
                decision_id,
            ),
        )

    conn.commit()


def _app(tmp_path: Path) -> Any:
    db_path = tmp_path / "dashboard_test.db"
    conn = init_db(str(db_path))
    _seed_db(conn)
    conn.close()
    return create_dashboard_app(str(db_path))


def _app_with_operating_status(tmp_path: Path) -> Any:
    db_path = tmp_path / "dashboard_status_test.db"
    conn = init_db(str(db_path))
    _seed_db(conn)
    conn.execute(
        "UPDATE decision_logs SET session_id = ? WHERE decision_id = ?",
        ("KRX_REG", "d-kr-1"),
    )
    conn.execute(
        "UPDATE decision_logs SET session_id = ? WHERE decision_id = ?",
        ("US_REG", "d-us-1"),
    )
    _seed_cb_context(conn, -1.5, market="KR")
    _seed_cb_context(conn, -3.5, market="US_NASDAQ")
    conn.commit()
    conn.close()
    return create_dashboard_app(str(db_path))


def _endpoint(app: Any, path: str) -> Callable[..., Any]:
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


def _app_with_trace_decisions(tmp_path: Path) -> Any:
    db_path = tmp_path / "dashboard_trace_test.db"
    conn = init_db(str(db_path))
    today = datetime.now(UTC).date().isoformat()
    conn.execute(
        """
        INSERT INTO decision_logs (
            decision_id, timestamp, stock_code, market, exchange_code,
            session_id, action, confidence, rationale, context_snapshot, input_data,
            llm_prompt, llm_response
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "d-kr-1",
            f"{today}T09:10:00+00:00",
            "005930",
            "KR",
            "KRX",
            "KRX_REG",
            "BUY",
            85,
            "signal matched",
            json.dumps({"scenario_match": {"rsi": 28.0}}),
            json.dumps({"current_price": 70000}),
            "kr prompt",
            '{"action":"BUY","confidence":85}',
        ),
    )
    conn.execute(
        """
        INSERT INTO decision_logs (
            decision_id, timestamp, stock_code, market, exchange_code,
            session_id, action, confidence, rationale, context_snapshot, input_data,
            llm_prompt, llm_response
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "d-us-1",
            f"{today}T21:10:00+00:00",
            "AAPL",
            "US_NASDAQ",
            "NASDAQ",
            "US_REG",
            "SELL",
            80,
            "no match",
            json.dumps({"scenario_match": {}}),
            json.dumps({"current_price": 200}),
            "us prompt",
            '{"action":"SELL","confidence":80}',
        ),
    )
    conn.execute(
        """
        INSERT INTO decision_logs (
            decision_id, timestamp, stock_code, market, exchange_code,
            session_id, action, confidence, rationale, context_snapshot, input_data,
            llm_prompt, llm_response
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "d-jp-old",
            "2026-02-13T01:10:00+00:00",
            "7203",
            "JP",
            "TSE",
            "JP_REG",
            "HOLD",
            60,
            "older decision",
            json.dumps({"scenario_match": {}}),
            json.dumps({"current_price": 2500}),
            "jp prompt",
            '{"action":"HOLD","confidence":60}',
        ),
    )
    conn.commit()
    conn.close()
    return create_dashboard_app(str(db_path))


def test_index_serves_html(tmp_path: Path) -> None:
    app = _app(tmp_path)
    index = _endpoint(app, "/")
    resp = index()
    assert isinstance(resp, FileResponse)
    assert "index.html" in str(resp.path)


def test_index_exposes_decision_trace_controls(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    html = client.get("/").text
    assert "결정 히스토리 필터" in html
    assert "LLM request" in html
    assert "LLM response" in html
    assert "trace 없음" in html
    assert "Diagnostics" in html


def test_index_exposes_overview_and_diagnostics_surfaces(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    html = client.get("/").text

    assert "surface-overview-tab" in html
    assert "surface-diagnostics-tab" in html
    assert "market-summary-grid" in html
    assert "diagnostics-surface" in html


def test_index_documents_overview_market_linkage_rules(tmp_path: Path) -> None:
    app = _app_with_trace_decisions(tmp_path)
    client = TestClient(app)
    html = client.get("/").text

    assert "activeOverviewMarket" in html
    assert "syncOverviewMarket" in html
    assert "메인 화면에서는 market 필터만 공유합니다." in html


def test_index_prevents_stale_decision_fetch_from_overwriting_market_focus(
    tmp_path: Path,
) -> None:
    app = _app_with_trace_decisions(tmp_path)
    client = TestClient(app)
    html = client.get("/").text

    assert "let latestDecisionRequestId = 0;" in html
    assert "const requestId = ++latestDecisionRequestId;" in html
    assert "if (requestId !== latestDecisionRequestId) return;" in html
    assert html.count("activeOverviewMarket = normalizeOverviewMarket(") == 1


def test_status_endpoint(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_status = _endpoint(app, "/api/status")
    body = get_status()
    assert "KR" in body["markets"]
    assert "US" in body["markets"]
    assert "totals" in body


def test_status_endpoint_returns_market_operating_summary(tmp_path: Path) -> None:
    app = _app_with_operating_status(tmp_path)
    get_status = _endpoint(app, "/api/status")
    body = get_status()

    kr = body["markets"]["KR"]
    us = body["markets"]["US"]

    assert kr["open_position_count"] == 1
    assert kr["latest_decision_action"] == "BUY"
    assert kr["latest_session_id"] == "KRX_REG"
    assert kr["current_pnl_pct"] == -1.5
    assert kr["circuit_breaker_status"] == "ok"
    assert kr["status_tone"] == "active"

    assert us["open_position_count"] == 0
    assert us["latest_decision_action"] == "SELL"
    assert us["latest_session_id"] == "US_REG"
    assert us["current_pnl_pct"] == -3.5
    assert us["circuit_breaker_status"] == "tripped"
    assert us["status_tone"] == "tripped"


def test_group_us_markets_status_and_positions_for_overview(tmp_path: Path) -> None:
    db_path = tmp_path / "dashboard_group_us_status.db"
    conn = init_db(str(db_path))
    _seed_group_us_market_fixture(conn)
    conn.close()

    app = create_dashboard_app(str(db_path))
    get_status = _endpoint(app, "/api/status")
    status_body = get_status()

    assert list(status_body["markets"]) == ["KR", "US"]
    assert status_body["markets"]["KR"]["trade_count"] == 1
    assert status_body["markets"]["KR"]["decision_count"] == 1
    assert status_body["markets"]["US"]["trade_count"] == 3
    assert status_body["markets"]["US"]["decision_count"] == 3
    assert status_body["markets"]["US"]["open_position_count"] == 3
    assert status_body["markets"]["US"]["latest_session_id"] == "US_REG"
    assert status_body["totals"] == {
        "trade_count": 4,
        "total_pnl": 2.75,
        "decision_count": 4,
    }

    get_positions = _endpoint(app, "/api/positions")
    positions_body = get_positions()

    assert positions_body["count"] == 4
    assert sum(1 for position in positions_body["positions"] if position["market"] == "US") == 3
    assert any(position["market"] == "KR" for position in positions_body["positions"])


def test_group_us_market_status_prefers_worst_playbook_state(tmp_path: Path) -> None:
    db_path = tmp_path / "dashboard_group_us_playbook_status.db"
    conn = init_db(str(db_path))
    _seed_group_us_market_fixture(conn)
    today = datetime.now(UTC).date().isoformat()
    conn.executemany(
        """
        INSERT INTO playbooks (
            date, market, status, playbook_json, generated_at,
            token_count, scenario_count, match_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                today,
                "US_NASDAQ",
                "ready",
                json.dumps({"market": "US_NASDAQ", "stock_playbooks": []}),
                f"{today}T08:30:00+00:00",
                100,
                1,
                0,
            ),
            (
                today,
                "US_NYSE",
                "error",
                json.dumps({"market": "US_NYSE", "stock_playbooks": []}),
                f"{today}T08:35:00+00:00",
                100,
                1,
                0,
            ),
        ],
    )
    conn.commit()
    conn.close()

    app = create_dashboard_app(str(db_path))
    get_status = _endpoint(app, "/api/status")
    status_body = get_status()

    assert status_body["markets"]["US"]["playbook_status"] == "error"


def test_status_totals_sum_raw_market_pnl_before_rounding(tmp_path: Path) -> None:
    db_path = tmp_path / "dashboard_total_pnl_rounding.db"
    conn = init_db(str(db_path))
    today = datetime.now(UTC).date().isoformat()
    conn.executemany(
        """
        INSERT INTO decision_logs (
            decision_id, timestamp, stock_code, market, exchange_code,
            session_id, action, confidence, rationale, context_snapshot, input_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "d-kr-rounding",
                f"{today}T09:10:00+00:00",
                "005930",
                "KR",
                "KRX",
                "KRX_REG",
                "BUY",
                80,
                "rounding repro",
                json.dumps({"scenario_match": {"rsi": 28.0}}),
                json.dumps({"current_price": 100}),
            ),
            (
                "d-us-rounding",
                f"{today}T21:10:00+00:00",
                "AAPL",
                "US_NASDAQ",
                "NASDAQ",
                "US_REG",
                "BUY",
                80,
                "rounding repro",
                json.dumps({"scenario_match": {"rsi": 28.0}}),
                json.dumps({"current_price": 100}),
            ),
        ],
    )
    conn.executemany(
        """
        INSERT INTO trades (
            timestamp, stock_code, action, confidence, rationale,
            quantity, price, pnl, market, exchange_code, selection_context, decision_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                f"{today}T09:11:00+00:00",
                "005930",
                "BUY",
                80,
                "rounding repro",
                1,
                100,
                0.335,
                "KR",
                "KRX",
                None,
                "d-kr-rounding",
            ),
            (
                f"{today}T21:11:00+00:00",
                "AAPL",
                "BUY",
                80,
                "rounding repro",
                1,
                100,
                0.335,
                "US_NASDAQ",
                "NASDAQ",
                None,
                "d-us-rounding",
            ),
        ],
    )
    conn.commit()
    conn.close()

    app = create_dashboard_app(str(db_path))
    get_status = _endpoint(app, "/api/status")
    status_body = get_status()

    assert status_body["markets"]["KR"]["total_pnl"] == 0.34
    assert status_body["markets"]["US"]["total_pnl"] == 0.34
    assert status_body["totals"]["total_pnl"] == 0.67


def test_status_endpoint_includes_runtime_tracking_diagnostics_when_provider_is_present(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dashboard_runtime_tracking.db"
    conn = init_db(str(db_path))
    _seed_db(conn)
    conn.close()

    app = create_dashboard_app(
        str(db_path),
        runtime_status_provider=lambda: {
            "KR": {
                "session_id": "KRX_REG",
                "active_count": 2,
                "active_stocks": ["005930", "000660"],
                "candidate_count": 2,
                "candidate_codes": ["005930", "000660"],
                "last_scan_age_seconds": 8.5,
            }
        },
    )
    get_status = _endpoint(app, "/api/status")
    body = get_status()

    assert body["markets"]["KR"]["runtime_tracking"] == {
        "session_id": "KRX_REG",
        "active_count": 2,
        "active_stocks": ["005930", "000660"],
        "candidate_count": 2,
        "candidate_codes": ["005930", "000660"],
        "last_scan_age_seconds": 8.5,
    }


def test_status_endpoint_reads_runtime_tracking_provider_from_app_state(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dashboard_runtime_tracking_state.db"
    conn = init_db(str(db_path))
    _seed_db(conn)
    conn.close()

    app = create_dashboard_app(str(db_path))
    app.state.runtime_status_provider = lambda: {
        "KR": {
            "session_id": "KRX_REG",
            "active_count": 1,
            "active_stocks": ["005930"],
            "candidate_count": 1,
            "candidate_codes": ["005930"],
            "last_scan_age_seconds": 3.0,
        }
    }

    get_status = _endpoint(app, "/api/status")
    body = get_status()

    assert body["markets"]["KR"]["runtime_tracking"] == {
        "session_id": "KRX_REG",
        "active_count": 1,
        "active_stocks": ["005930"],
        "candidate_count": 1,
        "candidate_codes": ["005930"],
        "last_scan_age_seconds": 3.0,
    }


def test_status_endpoint_excludes_stale_markets_without_today_activity_or_positions(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "dashboard_status_stale_markets.db"
    conn = init_db(str(db_path))
    _seed_db(conn)
    conn.execute(
        """
        INSERT INTO decision_logs (
            decision_id, timestamp, stock_code, market, exchange_code,
            session_id, action, confidence, rationale, context_snapshot, input_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "d-jp-old",
            "2026-02-01T01:00:00+00:00",
            "7203",
            "JP",
            "TSE",
            "JP_REG",
            "BUY",
            70,
            "old signal",
            json.dumps({}),
            json.dumps({}),
        ),
    )
    _seed_cb_context(conn, -0.5, market="JP")
    conn.close()

    app = create_dashboard_app(str(db_path))
    get_status = _endpoint(app, "/api/status")
    body = get_status()

    assert "JP" not in body["markets"]


def test_playbook_found(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_playbook = _endpoint(app, "/api/playbook/{date_str}")
    body = get_playbook("2026-02-14", market="KR")
    assert body["market"] == "KR"


def test_playbook_not_found(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_playbook = _endpoint(app, "/api/playbook/{date_str}")
    with pytest.raises(HTTPException, match="playbook not found"):
        get_playbook("2026-02-15", market="KR")


def test_scorecard_found(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_scorecard = _endpoint(app, "/api/scorecard/{date_str}")
    body = get_scorecard("2026-02-14", market="KR")
    assert body["scorecard"]["total_pnl"] == 1.5


def test_scorecard_not_found(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_scorecard = _endpoint(app, "/api/scorecard/{date_str}")
    with pytest.raises(HTTPException, match="scorecard not found"):
        get_scorecard("2026-02-15", market="KR")


def test_performance_all(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_performance = _endpoint(app, "/api/performance")
    body = get_performance(market="all")
    assert body["market"] == "all"
    assert body["combined"]["total_trades"] == 2
    assert len(body["by_market"]) == 2


def test_performance_market_filter(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_performance = _endpoint(app, "/api/performance")
    body = get_performance(market="KR")
    assert body["market"] == "KR"
    assert body["metrics"]["total_trades"] == 1


def test_performance_empty_market(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_performance = _endpoint(app, "/api/performance")
    body = get_performance(market="JP")
    assert body["metrics"]["total_trades"] == 0


def test_context_layer_all(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_context_layer = _endpoint(app, "/api/context/{layer}")
    body = get_context_layer("L7_REALTIME", timeframe=None, limit=100)
    assert body["layer"] == "L7_REALTIME"
    assert body["count"] == 1


def test_context_layer_timeframe_filter(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_context_layer = _endpoint(app, "/api/context/{layer}")
    body = get_context_layer("L6_DAILY", timeframe="2026-02-14", limit=100)
    assert body["count"] == 1
    assert body["entries"][0]["key"] == "scorecard_KR"


def test_decisions_endpoint(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_decisions = _endpoint(app, "/api/decisions")
    body = get_decisions(
        market="KR",
        session_id="all",
        action="all",
        stock_code=None,
        min_confidence=0,
        from_date=None,
        to_date=None,
        matched_only=False,
        limit=50,
    )
    assert body["count"] == 1
    assert body["decisions"][0]["decision_id"] == "d-kr-1"


def test_decisions_endpoint_supports_rich_filters_and_metadata(tmp_path: Path) -> None:
    app = _app_with_trace_decisions(tmp_path)
    get_decisions = _endpoint(app, "/api/decisions")
    today = datetime.now(UTC).date().isoformat()
    body = get_decisions(
        market="all",
        session_id="KRX_REG",
        action="BUY",
        stock_code="005",
        min_confidence=80,
        from_date=today,
        to_date=today,
        matched_only=True,
        limit=50,
    )
    assert body["count"] == 1
    assert body["decisions"][0]["decision_id"] == "d-kr-1"
    assert body["decisions"][0]["llm_prompt"] == "kr prompt"
    assert body["filters"]["session_id"] == "KRX_REG"
    assert body["markets"] == ["JP", "KR", "US"]
    assert body["sessions"] == ["JP_REG", "KRX_REG", "US_REG"]


def test_group_us_markets_decisions_and_pnl_history_filters(tmp_path: Path) -> None:
    db_path = tmp_path / "dashboard_group_us_filters.db"
    conn = init_db(str(db_path))
    _seed_group_us_market_fixture(conn)
    conn.close()

    app = create_dashboard_app(str(db_path))
    get_decisions = _endpoint(app, "/api/decisions")
    decisions_body = get_decisions(
        market="US",
        session_id="all",
        action="all",
        stock_code=None,
        min_confidence=0,
        from_date=None,
        to_date=None,
        matched_only=False,
        limit=50,
    )

    assert decisions_body["count"] == 3
    assert decisions_body["markets"] == ["KR", "US"]
    assert {decision["exchange_code"] for decision in decisions_body["decisions"]} == {
        "AMEX",
        "NASDAQ",
        "NYSE",
    }
    assert {decision["market"] for decision in decisions_body["decisions"]} == {"US"}

    get_pnl_history = _endpoint(app, "/api/pnl/history")
    pnl_body = get_pnl_history(days=30, market="US")

    assert pnl_body["market"] == "US"
    assert pnl_body["pnl"] == [0.75]
    assert pnl_body["trades"] == [3]


def test_decisions_endpoint_applies_limit_after_matched_only_filter(tmp_path: Path) -> None:
    app = _app_with_trace_decisions(tmp_path)
    get_decisions = _endpoint(app, "/api/decisions")

    body = get_decisions(
        market="all",
        session_id="all",
        action="all",
        stock_code=None,
        min_confidence=0,
        from_date=None,
        to_date=None,
        matched_only=True,
        limit=1,
    )

    assert body["count"] == 1
    assert body["decisions"][0]["decision_id"] == "d-kr-1"


def test_scenarios_active_filters_non_matched(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_active_scenarios = _endpoint(app, "/api/scenarios/active")
    body = get_active_scenarios(
        market="KR",
        date_str=datetime.now(UTC).date().isoformat(),
        limit=50,
    )
    assert body["count"] == 1
    assert body["matches"][0]["stock_code"] == "005930"


def test_scenarios_active_empty_when_no_matches(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_active_scenarios = _endpoint(app, "/api/scenarios/active")
    body = get_active_scenarios(market="US", date_str="2026-02-14", limit=50)
    assert body["count"] == 0


def test_pnl_history_all_markets(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_pnl_history = _endpoint(app, "/api/pnl/history")
    body = get_pnl_history(days=30, market="all")
    assert body["market"] == "all"
    assert isinstance(body["labels"], list)
    assert isinstance(body["pnl"], list)
    assert len(body["labels"]) == len(body["pnl"])


def test_pnl_history_market_filter(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_pnl_history = _endpoint(app, "/api/pnl/history")
    body = get_pnl_history(days=30, market="KR")
    assert body["market"] == "KR"
    # KR has 1 trade with pnl=2.0
    assert len(body["labels"]) >= 1
    assert body["pnl"][0] == 2.0


def test_positions_returns_open_buy(tmp_path: Path) -> None:
    """BUY가 마지막 거래인 종목은 포지션으로 반환되어야 한다."""
    app = _app(tmp_path)
    get_positions = _endpoint(app, "/api/positions")
    body = get_positions()
    # seed_db: 005930은 BUY (오픈), AAPL은 SELL (마지막)
    assert body["count"] == 1
    pos = body["positions"][0]
    assert pos["stock_code"] == "005930"
    assert pos["market"] == "KR"
    assert pos["quantity"] == 1
    assert pos["entry_price"] == 70000


def test_positions_excludes_closed_sell(tmp_path: Path) -> None:
    """마지막 거래가 SELL인 종목은 포지션에 나타나지 않아야 한다."""
    app = _app(tmp_path)
    get_positions = _endpoint(app, "/api/positions")
    body = get_positions()
    codes = [p["stock_code"] for p in body["positions"]]
    assert "AAPL" not in codes


def test_positions_empty_when_no_trades(tmp_path: Path) -> None:
    """거래 내역이 없으면 빈 포지션 목록을 반환해야 한다."""
    db_path = tmp_path / "empty.db"
    conn = init_db(str(db_path))
    conn.close()
    app = create_dashboard_app(str(db_path))
    get_positions = _endpoint(app, "/api/positions")
    body = get_positions()
    assert body["count"] == 0
    assert body["positions"] == []


def test_status_handles_fresh_db_file(tmp_path: Path) -> None:
    """Status endpoint should return an empty state for a fresh SQLite file."""
    db_path = tmp_path / "fresh.db"
    db_path.touch()

    app = create_dashboard_app(str(db_path))
    get_status = _endpoint(app, "/api/status")
    body = get_status()

    assert body["totals"]["trade_count"] == 0
    assert body["totals"]["decision_count"] == 0
    assert body["markets"] == {}


def _seed_cb_context(conn: sqlite3.Connection, pnl_pct: float, market: str = "KR") -> None:
    import json as _json

    conn.execute(
        "INSERT OR REPLACE INTO system_metrics (key, value, updated_at) VALUES (?, ?, ?)",
        (
            f"portfolio_pnl_pct_{market}",
            _json.dumps({"pnl_pct": pnl_pct}),
            "2026-02-22T10:00:00+00:00",
        ),
    )
    conn.commit()


def test_status_circuit_breaker_ok(tmp_path: Path) -> None:
    """pnl_pct가 -2.0%보다 높으면 status=ok를 반환해야 한다."""
    db_path = tmp_path / "cb_ok.db"
    conn = init_db(str(db_path))
    _seed_cb_context(conn, -1.0)
    conn.close()
    app = create_dashboard_app(str(db_path))
    get_status = _endpoint(app, "/api/status")
    body = get_status()
    cb = body["circuit_breaker"]
    assert cb["status"] == "ok"
    assert cb["current_pnl_pct"] == -1.0
    assert cb["threshold_pct"] == -3.0


def test_status_circuit_breaker_warning(tmp_path: Path) -> None:
    """pnl_pct가 -2.0% 이하이면 status=warning을 반환해야 한다."""
    db_path = tmp_path / "cb_warn.db"
    conn = init_db(str(db_path))
    _seed_cb_context(conn, -2.5)
    conn.close()
    app = create_dashboard_app(str(db_path))
    get_status = _endpoint(app, "/api/status")
    body = get_status()
    assert body["circuit_breaker"]["status"] == "warning"


def test_status_circuit_breaker_tripped(tmp_path: Path) -> None:
    """pnl_pct가 임계값(-3.0%) 이하이면 status=tripped를 반환해야 한다."""
    db_path = tmp_path / "cb_tripped.db"
    conn = init_db(str(db_path))
    _seed_cb_context(conn, -3.5)
    conn.close()
    app = create_dashboard_app(str(db_path))
    get_status = _endpoint(app, "/api/status")
    body = get_status()
    assert body["circuit_breaker"]["status"] == "tripped"


def test_status_circuit_breaker_unknown_when_no_data(tmp_path: Path) -> None:
    """L7 context에 pnl_pct 데이터가 없으면 status=unknown을 반환해야 한다."""
    app = _app(tmp_path)  # seed_db에는 portfolio_pnl_pct 없음
    get_status = _endpoint(app, "/api/status")
    body = get_status()
    cb = body["circuit_breaker"]
    assert cb["status"] == "unknown"
    assert cb["current_pnl_pct"] is None


def test_status_mode_paper(tmp_path: Path) -> None:
    """mode=paper로 생성하면 status 응답에 mode=paper가 포함돼야 한다."""
    db_path = tmp_path / "dashboard_test.db"
    conn = init_db(str(db_path))
    _seed_db(conn)
    conn.close()
    app = create_dashboard_app(str(db_path), mode="paper")
    get_status = _endpoint(app, "/api/status")
    body = get_status()
    assert body["mode"] == "paper"


def test_status_mode_live(tmp_path: Path) -> None:
    """mode=live로 생성하면 status 응답에 mode=live가 포함돼야 한다."""
    db_path = tmp_path / "dashboard_test.db"
    conn = init_db(str(db_path))
    _seed_db(conn)
    conn.close()
    app = create_dashboard_app(str(db_path), mode="live")
    get_status = _endpoint(app, "/api/status")
    body = get_status()
    assert body["mode"] == "live"


def test_status_mode_default_paper(tmp_path: Path) -> None:
    """mode 파라미터 미전달 시 기본값은 paper여야 한다."""
    db_path = tmp_path / "dashboard_test.db"
    conn = init_db(str(db_path))
    _seed_db(conn)
    conn.close()
    app = create_dashboard_app(str(db_path))
    get_status = _endpoint(app, "/api/status")
    body = get_status()
    assert body["mode"] == "paper"


def _app_with_open_and_mid_playbook(tmp_path: Path) -> Any:
    """open + mid 두 슬롯이 있는 DB로 앱을 생성한다."""
    db_path = tmp_path / "slot_test.db"
    conn = init_db(str(db_path))
    conn.execute(
        """
        INSERT INTO playbooks (
            date, market, slot, status, playbook_json, generated_at,
            token_count, scenario_count, match_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-02-08",
            "KR",
            "open",
            "ready",
            json.dumps({"market": "KR", "stock_playbooks": []}),
            "2026-02-08T08:30:00+00:00",
            100,
            1,
            0,
        ),
    )
    conn.execute(
        """
        INSERT INTO playbooks (
            date, market, slot, status, playbook_json, generated_at,
            token_count, scenario_count, match_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-02-08",
            "KR",
            "mid",
            "ready",
            json.dumps({"market": "KR", "stock_playbooks": []}),
            "2026-02-08T12:30:00+00:00",
            110,
            2,
            1,
        ),
    )
    conn.commit()
    conn.close()
    return create_dashboard_app(str(db_path))


def test_get_playbook_returns_slot_field(tmp_path: Path) -> None:
    """GET /api/playbook/{date} 응답에 slot 필드가 포함돼야 한다."""
    app = _app(tmp_path)
    get_playbook = _endpoint(app, "/api/playbook/{date_str}")
    body = get_playbook("2026-02-14", market="KR")
    assert "slot" in body


def test_get_playbook_slot_param_mid(tmp_path: Path) -> None:
    """slot=mid 파라미터로 mid 플레이북을 조회할 수 있다."""
    app = _app_with_open_and_mid_playbook(tmp_path)
    get_playbook = _endpoint(app, "/api/playbook/{date_str}")
    body = get_playbook("2026-02-08", market="KR", slot="mid")
    assert body["slot"] == "mid"


def test_get_playbook_default_returns_latest(tmp_path: Path) -> None:
    """slot 미지정 시 가장 최근(mid) 플레이북을 반환한다."""
    app = _app_with_open_and_mid_playbook(tmp_path)
    get_playbook = _endpoint(app, "/api/playbook/{date_str}")
    body = get_playbook("2026-02-08", market="KR")
    assert body["slot"] == "mid"
