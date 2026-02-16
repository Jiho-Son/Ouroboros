"""Tests for database helper functions."""

from src.db import get_open_position, init_db, log_trade


def test_get_open_position_returns_latest_buy() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=2,
        price=70000.0,
        market="KR",
        exchange_code="KRX",
        decision_id="d-buy-1",
    )

    position = get_open_position(conn, "005930", "KR")
    assert position is not None
    assert position["decision_id"] == "d-buy-1"
    assert position["price"] == 70000.0
    assert position["quantity"] == 2


def test_get_open_position_returns_none_when_latest_is_sell() -> None:
    conn = init_db(":memory:")
    log_trade(
        conn=conn,
        stock_code="005930",
        action="BUY",
        confidence=90,
        rationale="entry",
        quantity=1,
        price=70000.0,
        market="KR",
        exchange_code="KRX",
        decision_id="d-buy-1",
    )
    log_trade(
        conn=conn,
        stock_code="005930",
        action="SELL",
        confidence=95,
        rationale="exit",
        quantity=1,
        price=71000.0,
        market="KR",
        exchange_code="KRX",
        decision_id="d-sell-1",
    )

    assert get_open_position(conn, "005930", "KR") is None


def test_get_open_position_returns_none_when_no_trades() -> None:
    conn = init_db(":memory:")
    assert get_open_position(conn, "AAPL", "US_NASDAQ") is None
