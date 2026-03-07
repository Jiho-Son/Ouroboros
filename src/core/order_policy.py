"""Session-aware order policy guards.

Default policy:
- Low-liquidity sessions must reject market orders (price <= 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, tzinfo
from zoneinfo import ZoneInfo

from src.markets.schedule import MarketInfo

_LOW_LIQUIDITY_SESSIONS = {"NXT_AFTER", "US_PRE", "US_DAY", "US_AFTER"}


class OrderPolicyRejectedError(Exception):
    """Raised when an order violates session policy."""

    def __init__(self, message: str, *, session_id: str, market_code: str) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.market_code = market_code


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    is_low_liquidity: bool


def classify_session_id(market: MarketInfo, now: datetime | None = None) -> str:
    """Classify current session by KST schedule used in v3 docs."""
    now = now or datetime.now(UTC)
    market_timezone = market.timezone
    if not isinstance(market_timezone, tzinfo):
        if market.code == "KR":
            market_timezone = ZoneInfo("Asia/Seoul")
        elif market.code.startswith("US"):
            market_timezone = ZoneInfo("America/New_York")
        else:
            market_timezone = UTC
    local_now = now.astimezone(market_timezone)
    # v3 session tables are explicitly defined in KST perspective.
    kst_time = now.astimezone(ZoneInfo("Asia/Seoul")).timetz().replace(tzinfo=None)

    if market.code == "KR":
        if local_now.weekday() >= 5:
            return "KR_OFF"
        if time(8, 0) <= kst_time < time(8, 50):
            return "NXT_PRE"
        if time(9, 0) <= kst_time < time(15, 30):
            return "KRX_REG"
        if time(15, 30) <= kst_time < time(20, 0):
            return "NXT_AFTER"
        return "KR_OFF"

    if market.code.startswith("US"):
        if local_now.weekday() >= 5:
            return "US_OFF"
        if time(10, 0) <= kst_time < time(18, 0):
            return "US_DAY"
        if time(18, 0) <= kst_time < time(23, 30):
            return "US_PRE"
        if time(23, 30) <= kst_time or kst_time < time(6, 0):
            return "US_REG"
        if time(6, 0) <= kst_time < time(7, 0):
            return "US_AFTER"
        return "US_OFF"

    return "GENERIC_REG"


def get_session_info(market: MarketInfo, now: datetime | None = None) -> SessionInfo:
    session_id = classify_session_id(market, now)
    return SessionInfo(
        session_id=session_id, is_low_liquidity=session_id in _LOW_LIQUIDITY_SESSIONS
    )


def validate_order_policy(
    *,
    market: MarketInfo,
    order_type: str,
    price: float,
    now: datetime | None = None,
) -> SessionInfo:
    """Validate order against session policy and return resolved session info."""
    info = get_session_info(market, now)

    is_market_order = price <= 0
    if info.is_low_liquidity and is_market_order:
        raise OrderPolicyRejectedError(
            f"Market order is forbidden in low-liquidity session ({info.session_id})",
            session_id=info.session_id,
            market_code=market.code,
        )

    # Guard against accidental unsupported actions.
    if order_type not in {"BUY", "SELL"}:
        raise OrderPolicyRejectedError(
            f"Unsupported order_type={order_type}",
            session_id=info.session_id,
            market_code=market.code,
        )

    return info


# Backward compatibility alias
OrderPolicyRejected = OrderPolicyRejectedError
