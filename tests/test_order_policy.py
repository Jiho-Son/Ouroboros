from datetime import UTC, datetime

import pytest

from src.core.order_policy import OrderPolicyRejected, classify_session_id, validate_order_policy
from src.markets.schedule import MARKETS


def test_classify_kr_nxt_after() -> None:
    # 2026-02-26 16:00 KST == 07:00 UTC
    now = datetime(2026, 2, 26, 7, 0, tzinfo=UTC)
    assert classify_session_id(MARKETS["KR"], now) == "NXT_AFTER"


def test_classify_us_pre() -> None:
    # 2026-02-26 19:00 KST == 10:00 UTC
    now = datetime(2026, 2, 26, 10, 0, tzinfo=UTC)
    assert classify_session_id(MARKETS["US_NASDAQ"], now) == "US_PRE"


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
