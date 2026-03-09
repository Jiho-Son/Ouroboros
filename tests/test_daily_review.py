"""Tests for DailyReviewer."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.context.layer import ContextLayer
from src.context.store import ContextStore
from src.db import init_db, log_trade
from src.decision_logging.decision_logger import DecisionLogger
from src.evolution.daily_review import DailyReviewer
from src.evolution.scorecard import DailyScorecard

TODAY = datetime.now(UTC).strftime("%Y-%m-%d")


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    return init_db(":memory:")


@pytest.fixture
def context_store(db_conn: sqlite3.Connection) -> ContextStore:
    return ContextStore(db_conn)


def _log_decision(
    logger: DecisionLogger,
    *,
    stock_code: str,
    market: str,
    action: str,
    confidence: int,
    scenario_match: dict[str, float] | None = None,
) -> str:
    return logger.log_decision(
        stock_code=stock_code,
        market=market,
        exchange_code="KRX" if market == "KR" else "NASDAQ",
        action=action,
        confidence=confidence,
        rationale="test",
        context_snapshot={"scenario_match": scenario_match or {}},
        input_data={"stock_code": stock_code},
    )


def test_generate_scorecard_market_scoped(
    db_conn: sqlite3.Connection,
    context_store: ContextStore,
) -> None:
    reviewer = DailyReviewer(db_conn, context_store)
    logger = DecisionLogger(db_conn)

    buy_id = _log_decision(
        logger,
        stock_code="005930",
        market="KR",
        action="BUY",
        confidence=90,
        scenario_match={"rsi": 29.0},
    )
    _log_decision(
        logger,
        stock_code="000660",
        market="KR",
        action="HOLD",
        confidence=60,
    )
    _log_decision(
        logger,
        stock_code="AAPL",
        market="US",
        action="SELL",
        confidence=80,
        scenario_match={"volume_ratio": 2.1},
    )

    log_trade(
        db_conn,
        "005930",
        "BUY",
        90,
        "buy",
        quantity=1,
        price=100.0,
        pnl=10.0,
        market="KR",
        exchange_code="KRX",
        decision_id=buy_id,
    )
    log_trade(
        db_conn,
        "000660",
        "HOLD",
        60,
        "hold",
        quantity=0,
        price=0.0,
        pnl=0.0,
        market="KR",
        exchange_code="KRX",
    )
    log_trade(
        db_conn,
        "AAPL",
        "SELL",
        80,
        "sell",
        quantity=1,
        price=200.0,
        pnl=-5.0,
        market="US",
        exchange_code="NASDAQ",
    )

    scorecard = reviewer.generate_scorecard(TODAY, "KR")

    assert scorecard.market == "KR"
    assert scorecard.total_decisions == 2
    assert scorecard.buys == 1
    assert scorecard.sells == 0
    assert scorecard.holds == 1
    assert scorecard.total_pnl == 10.0
    assert scorecard.win_rate == 100.0
    assert scorecard.avg_confidence == 75.0
    assert scorecard.scenario_match_rate == 50.0


def test_generate_scorecard_top_winners_and_losers(
    db_conn: sqlite3.Connection,
    context_store: ContextStore,
) -> None:
    reviewer = DailyReviewer(db_conn, context_store)
    logger = DecisionLogger(db_conn)

    for code, pnl in [("005930", 30.0), ("000660", 10.0), ("035420", -15.0), ("051910", -5.0)]:
        decision_id = _log_decision(
            logger,
            stock_code=code,
            market="KR",
            action="BUY" if pnl >= 0 else "SELL",
            confidence=80,
            scenario_match={"rsi": 30.0},
        )
        log_trade(
            db_conn,
            code,
            "BUY" if pnl >= 0 else "SELL",
            80,
            "test",
            quantity=1,
            price=100.0,
            pnl=pnl,
            market="KR",
            exchange_code="KRX",
            decision_id=decision_id,
        )

    scorecard = reviewer.generate_scorecard(TODAY, "KR")
    assert scorecard.top_winners == ["005930", "000660"]
    assert scorecard.top_losers == ["035420", "051910"]


def test_generate_scorecard_empty_day(
    db_conn: sqlite3.Connection,
    context_store: ContextStore,
) -> None:
    reviewer = DailyReviewer(db_conn, context_store)
    scorecard = reviewer.generate_scorecard(TODAY, "KR")

    assert scorecard.total_decisions == 0
    assert scorecard.total_pnl == 0.0
    assert scorecard.win_rate == 0.0
    assert scorecard.avg_confidence == 0.0
    assert scorecard.scenario_match_rate == 0.0
    assert scorecard.top_winners == []
    assert scorecard.top_losers == []


@pytest.mark.asyncio
async def test_generate_lessons_without_gemini_returns_empty(
    db_conn: sqlite3.Connection,
    context_store: ContextStore,
) -> None:
    reviewer = DailyReviewer(db_conn, context_store, gemini_client=None)
    lessons = await reviewer.generate_lessons(
        DailyScorecard(
            date="2026-02-14",
            market="KR",
            total_decisions=1,
            buys=1,
            sells=0,
            holds=0,
            total_pnl=5.0,
            win_rate=100.0,
            avg_confidence=90.0,
            scenario_match_rate=100.0,
        )
    )
    assert lessons == []


@pytest.mark.asyncio
async def test_generate_lessons_parses_json_array(
    db_conn: sqlite3.Connection,
    context_store: ContextStore,
) -> None:
    mock_gemini = MagicMock()
    mock_gemini.decide = AsyncMock(
        return_value=SimpleNamespace(rationale='["Cut losers earlier", "Reduce midday churn"]')
    )
    reviewer = DailyReviewer(db_conn, context_store, gemini_client=mock_gemini)

    lessons = await reviewer.generate_lessons(
        DailyScorecard(
            date="2026-02-14",
            market="KR",
            total_decisions=3,
            buys=1,
            sells=1,
            holds=1,
            total_pnl=-2.5,
            win_rate=50.0,
            avg_confidence=70.0,
            scenario_match_rate=66.7,
        )
    )
    assert lessons == ["Cut losers earlier", "Reduce midday churn"]


@pytest.mark.asyncio
async def test_generate_lessons_fallback_to_lines(
    db_conn: sqlite3.Connection,
    context_store: ContextStore,
) -> None:
    mock_gemini = MagicMock()
    mock_gemini.decide = AsyncMock(
        return_value=SimpleNamespace(rationale="- Keep risk tighter\n- Increase selectivity")
    )
    reviewer = DailyReviewer(db_conn, context_store, gemini_client=mock_gemini)

    lessons = await reviewer.generate_lessons(
        DailyScorecard(
            date="2026-02-14",
            market="US",
            total_decisions=2,
            buys=1,
            sells=1,
            holds=0,
            total_pnl=1.0,
            win_rate=50.0,
            avg_confidence=75.0,
            scenario_match_rate=100.0,
        )
    )
    assert lessons == ["Keep risk tighter", "Increase selectivity"]


@pytest.mark.asyncio
async def test_generate_lessons_handles_gemini_error(
    db_conn: sqlite3.Connection,
    context_store: ContextStore,
) -> None:
    mock_gemini = MagicMock()
    mock_gemini.decide = AsyncMock(side_effect=RuntimeError("boom"))
    reviewer = DailyReviewer(db_conn, context_store, gemini_client=mock_gemini)

    lessons = await reviewer.generate_lessons(
        DailyScorecard(
            date="2026-02-14",
            market="US",
            total_decisions=0,
            buys=0,
            sells=0,
            holds=0,
            total_pnl=0.0,
            win_rate=0.0,
            avg_confidence=0.0,
            scenario_match_rate=0.0,
        )
    )
    assert lessons == []


def test_store_scorecard_in_context(
    db_conn: sqlite3.Connection,
    context_store: ContextStore,
) -> None:
    reviewer = DailyReviewer(db_conn, context_store)
    scorecard = DailyScorecard(
        date="2026-02-14",
        market="KR",
        total_decisions=5,
        buys=2,
        sells=1,
        holds=2,
        total_pnl=15.0,
        win_rate=66.67,
        avg_confidence=82.0,
        scenario_match_rate=80.0,
        lessons=["Keep position sizing stable"],
        cross_market_note="US risk-off",
    )

    reviewer.store_scorecard_in_context(scorecard)

    stored = context_store.get_context(
        ContextLayer.L6_DAILY,
        "2026-02-14",
        "scorecard_KR",
    )
    assert stored is not None
    assert stored["market"] == "KR"
    assert stored["total_pnl"] == 15.0
    assert stored["lessons"] == ["Keep position sizing stable"]


def test_store_scorecard_key_is_market_scoped(
    db_conn: sqlite3.Connection,
    context_store: ContextStore,
) -> None:
    reviewer = DailyReviewer(db_conn, context_store)
    kr = DailyScorecard(
        date="2026-02-14",
        market="KR",
        total_decisions=1,
        buys=1,
        sells=0,
        holds=0,
        total_pnl=1.0,
        win_rate=100.0,
        avg_confidence=90.0,
        scenario_match_rate=100.0,
    )
    us = DailyScorecard(
        date="2026-02-14",
        market="US",
        total_decisions=1,
        buys=0,
        sells=1,
        holds=0,
        total_pnl=-1.0,
        win_rate=0.0,
        avg_confidence=70.0,
        scenario_match_rate=100.0,
    )

    reviewer.store_scorecard_in_context(kr)
    reviewer.store_scorecard_in_context(us)

    kr_ctx = context_store.get_context(ContextLayer.L6_DAILY, "2026-02-14", "scorecard_KR")
    us_ctx = context_store.get_context(ContextLayer.L6_DAILY, "2026-02-14", "scorecard_US")

    assert kr_ctx["market"] == "KR"
    assert us_ctx["market"] == "US"
    assert kr_ctx["total_pnl"] == 1.0
    assert us_ctx["total_pnl"] == -1.0


def test_generate_scorecard_handles_invalid_context_snapshot(
    db_conn: sqlite3.Connection,
    context_store: ContextStore,
) -> None:
    reviewer = DailyReviewer(db_conn, context_store)
    db_conn.execute(
        """
        INSERT INTO decision_logs (
            decision_id, timestamp, stock_code, market, exchange_code,
            action, confidence, rationale, context_snapshot, input_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "d1",
            "2026-02-14T09:00:00+00:00",
            "005930",
            "KR",
            "KRX",
            "HOLD",
            50,
            "test",
            "{invalid_json",
            json.dumps({}),
        ),
    )
    db_conn.commit()

    scorecard = reviewer.generate_scorecard("2026-02-14", "KR")
    assert scorecard.total_decisions == 1
    assert scorecard.scenario_match_rate == 0.0
