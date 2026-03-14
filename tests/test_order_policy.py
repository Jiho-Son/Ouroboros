from datetime import UTC, datetime

import pytest

from src.config import Settings
from src.core.order_helpers import resolve_executable_quote
from src.core.order_policy import OrderPolicyRejected, classify_session_id, validate_order_policy
from src.markets.schedule import MARKETS


def _make_settings(**overrides: float) -> Settings:
    base = {
        "KIS_APP_KEY": "k",
        "KIS_APP_SECRET": "s",
        "KIS_ACCOUNT_NO": "12345678-01",
        "GEMINI_API_KEY": "g",
    }
    base.update(overrides)
    return Settings(**base)


def test_classify_kr_nxt_after() -> None:
    # 2026-02-26 16:00 KST == 07:00 UTC
    now = datetime(2026, 2, 26, 7, 0, tzinfo=UTC)
    assert classify_session_id(MARKETS["KR"], now) == "NXT_AFTER"


def test_classify_kr_weekend_is_off_even_during_extended_hours() -> None:
    # 2026-03-07 08:00 KST == 2026-03-06 23:00 UTC (Saturday)
    now = datetime(2026, 3, 6, 23, 0, tzinfo=UTC)
    assert classify_session_id(MARKETS["KR"], now) == "KR_OFF"


def test_classify_us_pre() -> None:
    # 2026-02-26 19:00 KST == 10:00 UTC
    now = datetime(2026, 2, 26, 10, 0, tzinfo=UTC)
    assert classify_session_id(MARKETS["US_NASDAQ"], now) == "US_PRE"


def test_classify_us_weekend_is_off_even_during_kst_session_window() -> None:
    # 2026-03-08 00:30 KST == 2026-03-07 15:30 UTC, still Saturday in New York.
    now = datetime(2026, 3, 7, 15, 30, tzinfo=UTC)
    assert classify_session_id(MARKETS["US_NASDAQ"], now) == "US_OFF"


def test_reject_market_order_in_low_liquidity_session() -> None:
    now = datetime(2026, 2, 26, 10, 0, tzinfo=UTC)  # 19:00 KST -> US_PRE
    with pytest.raises(OrderPolicyRejected):
        validate_order_policy(
            market=MARKETS["US_NASDAQ"],
            order_type="BUY",
            price=0.0,
            now=now,
        )


def test_allow_limit_order_in_low_liquidity_session() -> None:
    now = datetime(2026, 2, 26, 10, 0, tzinfo=UTC)  # 19:00 KST -> US_PRE
    info = validate_order_policy(
        market=MARKETS["US_NASDAQ"],
        order_type="BUY",
        price=100.0,
        now=now,
    )
    assert info.session_id == "US_PRE"

def test_resolve_executable_quote_prefers_best_ask_for_buy() -> None:
    quote = resolve_executable_quote(
        market=MARKETS["US_NASDAQ"],
        action="BUY",
        current_price=100.0,
        settings=_make_settings(),
        payload={"output": {"last": "100.0", "ask": "100.40", "bid": "99.90"}},
    )

    assert quote.price == pytest.approx(100.40)
    assert quote.buy_gap_rejected is False
    assert quote.source == "ask"


def test_resolve_executable_quote_rejects_buy_when_gap_is_too_wide() -> None:
    quote = resolve_executable_quote(
        market=MARKETS["US_NASDAQ"],
        action="BUY",
        current_price=100.0,
        settings=_make_settings(EXECUTABLE_QUOTE_MAX_GAP_PCT=0.5),
        payload={"output": {"last": "100.0", "ask": "101.00"}},
    )

    assert quote.price == pytest.approx(101.00)
    assert quote.gap_pct == pytest.approx(1.0)
    assert quote.buy_gap_rejected is True


def test_resolve_executable_quote_prefers_best_bid_for_sell() -> None:
    quote = resolve_executable_quote(
        market=MARKETS["US_NASDAQ"],
        action="SELL",
        current_price=100.0,
        settings=_make_settings(),
        payload={"output": {"last": "100.0", "ask": "100.40", "bid": "99.25"}},
    )

    assert quote.price == pytest.approx(99.25)
    assert quote.buy_gap_rejected is False
    assert quote.source == "bid"
