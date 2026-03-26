from datetime import UTC, datetime

import pytest

from src.core.order_policy import OrderPolicyRejected, classify_session_id, validate_order_policy
from src.markets.schedule import MARKETS


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


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 3, 9, 13, 30, tzinfo=UTC), "US_REG"),
        (datetime(2026, 3, 9, 20, 0, tzinfo=UTC), "US_AFTER"),
        (datetime(2026, 11, 2, 14, 30, tzinfo=UTC), "US_REG"),
    ],
    ids=[
        "dst_start_regular_open",
        "dst_start_after_hours_open",
        "dst_end_regular_open",
    ],
)
def test_classify_us_dst_boundaries(now: datetime, expected: str) -> None:
    assert classify_session_id(MARKETS["US_NASDAQ"], now) == expected


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
