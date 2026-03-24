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


def _app(tmp_path: Path) -> Any:
    db_path = tmp_path / "dashboard_test.db"
    conn = init_db(str(db_path))
    _seed_db(conn)
    conn.close()
    return create_dashboard_app(str(db_path))


def _endpoint(app: Any, path: str) -> Callable[..., Any]:
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


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
    assert "Diagnostics" in html


def test_status_endpoint(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_status = _endpoint(app, "/api/status")
    body = get_status()
    assert "KR" in body["markets"]
    assert "US_NASDAQ" in body["markets"]
    assert "totals" in body


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
    body = get_decisions(market="KR", limit=50)
    assert body["count"] == 1
    assert body["decisions"][0]["decision_id"] == "d-kr-1"
    assert body["decisions"][0]["llm_prompt"] == "kr prompt"


def test_decisions_endpoint_supports_rich_filters_and_metadata(tmp_path: Path) -> None:
    app = _app(tmp_path)
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
    assert body["filters"]["session_id"] == "KRX_REG"
    assert body["markets"] == ["JP", "KR", "US_NASDAQ"]
    assert body["sessions"] == ["JP_REG", "KRX_REG", "US_REG"]


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
