"""Tests for DailyScorecard model."""

from __future__ import annotations

from src.evolution.scorecard import DailyScorecard


def test_scorecard_initialization() -> None:
    scorecard = DailyScorecard(
        date="2026-02-08",
        market="KR",
        total_decisions=10,
        buys=3,
        sells=2,
        holds=5,
        total_pnl=1234.5,
        win_rate=60.0,
        avg_confidence=78.5,
        scenario_match_rate=70.0,
        top_winners=["005930", "000660"],
        top_losers=["035420"],
        lessons=["Avoid chasing breakouts"],
        cross_market_note="US volatility spillover",
    )

    assert scorecard.market == "KR"
    assert scorecard.total_decisions == 10
    assert scorecard.total_pnl == 1234.5
    assert scorecard.top_winners == ["005930", "000660"]
    assert scorecard.lessons == ["Avoid chasing breakouts"]
    assert scorecard.cross_market_note == "US volatility spillover"


def test_scorecard_defaults() -> None:
    scorecard = DailyScorecard(
        date="2026-02-08",
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

    assert scorecard.top_winners == []
    assert scorecard.top_losers == []
    assert scorecard.lessons == []
    assert scorecard.cross_market_note == ""


def test_scorecard_list_isolation() -> None:
    a = DailyScorecard(
        date="2026-02-08",
        market="KR",
        total_decisions=1,
        buys=1,
        sells=0,
        holds=0,
        total_pnl=10.0,
        win_rate=100.0,
        avg_confidence=90.0,
        scenario_match_rate=100.0,
    )
    b = DailyScorecard(
        date="2026-02-08",
        market="US",
        total_decisions=1,
        buys=0,
        sells=1,
        holds=0,
        total_pnl=-5.0,
        win_rate=0.0,
        avg_confidence=60.0,
        scenario_match_rate=50.0,
    )

    a.top_winners.append("005930")
    assert b.top_winners == []
