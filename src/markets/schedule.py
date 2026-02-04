"""Market schedule management with timezone support."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class MarketInfo:
    """Information about a trading market."""

    code: str  # Market code for internal use (e.g., "KR", "US_NASDAQ")
    exchange_code: str  # KIS API exchange code (e.g., "NASD", "NYSE")
    name: str  # Human-readable name
    timezone: ZoneInfo  # Market timezone
    open_time: time  # Market open time in local timezone
    close_time: time  # Market close time in local timezone
    is_domestic: bool  # True for Korean market, False for overseas
    lunch_break: tuple[time, time] | None = None  # (start, end) or None


# 10 global markets with their schedules
MARKETS: dict[str, MarketInfo] = {
    "KR": MarketInfo(
        code="KR",
        exchange_code="KRX",
        name="Korea Exchange",
        timezone=ZoneInfo("Asia/Seoul"),
        open_time=time(9, 0),
        close_time=time(15, 30),
        is_domestic=True,
        lunch_break=None,  # KRX removed lunch break
    ),
    "US_NASDAQ": MarketInfo(
        code="US_NASDAQ",
        exchange_code="NASD",
        name="NASDAQ",
        timezone=ZoneInfo("America/New_York"),
        open_time=time(9, 30),
        close_time=time(16, 0),
        is_domestic=False,
        lunch_break=None,
    ),
    "US_NYSE": MarketInfo(
        code="US_NYSE",
        exchange_code="NYSE",
        name="New York Stock Exchange",
        timezone=ZoneInfo("America/New_York"),
        open_time=time(9, 30),
        close_time=time(16, 0),
        is_domestic=False,
        lunch_break=None,
    ),
    "US_AMEX": MarketInfo(
        code="US_AMEX",
        exchange_code="AMEX",
        name="NYSE American",
        timezone=ZoneInfo("America/New_York"),
        open_time=time(9, 30),
        close_time=time(16, 0),
        is_domestic=False,
        lunch_break=None,
    ),
    "JP": MarketInfo(
        code="JP",
        exchange_code="TSE",
        name="Tokyo Stock Exchange",
        timezone=ZoneInfo("Asia/Tokyo"),
        open_time=time(9, 0),
        close_time=time(15, 0),
        is_domestic=False,
        lunch_break=(time(11, 30), time(12, 30)),
    ),
    "HK": MarketInfo(
        code="HK",
        exchange_code="SEHK",
        name="Hong Kong Stock Exchange",
        timezone=ZoneInfo("Asia/Hong_Kong"),
        open_time=time(9, 30),
        close_time=time(16, 0),
        is_domestic=False,
        lunch_break=(time(12, 0), time(13, 0)),
    ),
    "CN_SHA": MarketInfo(
        code="CN_SHA",
        exchange_code="SHAA",
        name="Shanghai Stock Exchange",
        timezone=ZoneInfo("Asia/Shanghai"),
        open_time=time(9, 30),
        close_time=time(15, 0),
        is_domestic=False,
        lunch_break=(time(11, 30), time(13, 0)),
    ),
    "CN_SZA": MarketInfo(
        code="CN_SZA",
        exchange_code="SZAA",
        name="Shenzhen Stock Exchange",
        timezone=ZoneInfo("Asia/Shanghai"),
        open_time=time(9, 30),
        close_time=time(15, 0),
        is_domestic=False,
        lunch_break=(time(11, 30), time(13, 0)),
    ),
    "VN_HAN": MarketInfo(
        code="VN_HAN",
        exchange_code="HNX",
        name="Hanoi Stock Exchange",
        timezone=ZoneInfo("Asia/Ho_Chi_Minh"),
        open_time=time(9, 0),
        close_time=time(15, 0),
        is_domestic=False,
        lunch_break=(time(11, 30), time(13, 0)),
    ),
    "VN_HCM": MarketInfo(
        code="VN_HCM",
        exchange_code="HSX",
        name="Ho Chi Minh Stock Exchange",
        timezone=ZoneInfo("Asia/Ho_Chi_Minh"),
        open_time=time(9, 0),
        close_time=time(15, 0),
        is_domestic=False,
        lunch_break=(time(11, 30), time(13, 0)),
    ),
}


def is_market_open(market: MarketInfo, now: datetime | None = None) -> bool:
    """
    Check if a market is currently open for trading.

    Args:
        market: Market information
        now: Current time (defaults to datetime.now(UTC) for testing)

    Returns:
        True if market is open, False otherwise

    Note:
        Does not account for holidays (KIS API will reject orders on holidays)
    """
    if now is None:
        now = datetime.now(ZoneInfo("UTC"))

    # Convert to market's local timezone
    local_now = now.astimezone(market.timezone)

    # Check if it's a weekend
    if local_now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    current_time = local_now.time()

    # Check if within trading hours
    if current_time < market.open_time or current_time >= market.close_time:
        return False

    # Check lunch break
    if market.lunch_break:
        lunch_start, lunch_end = market.lunch_break
        if lunch_start <= current_time < lunch_end:
            return False

    return True


def get_open_markets(
    enabled_markets: list[str] | None = None, now: datetime | None = None
) -> list[MarketInfo]:
    """
    Get list of currently open markets.

    Args:
        enabled_markets: List of market codes to check (defaults to all markets)
        now: Current time (defaults to datetime.now(UTC) for testing)

    Returns:
        List of open markets, sorted by market code
    """
    if enabled_markets is None:
        enabled_markets = list(MARKETS.keys())

    open_markets = [
        MARKETS[code]
        for code in enabled_markets
        if code in MARKETS and is_market_open(MARKETS[code], now)
    ]

    return sorted(open_markets, key=lambda m: m.code)


def get_next_market_open(
    enabled_markets: list[str] | None = None, now: datetime | None = None
) -> tuple[MarketInfo, datetime]:
    """
    Find the next market that will open and when.

    Args:
        enabled_markets: List of market codes to check (defaults to all markets)
        now: Current time (defaults to datetime.now(UTC) for testing)

    Returns:
        Tuple of (market, open_datetime) for the next market to open

    Raises:
        ValueError: If no enabled markets are configured
    """
    if now is None:
        now = datetime.now(ZoneInfo("UTC"))

    if enabled_markets is None:
        enabled_markets = list(MARKETS.keys())

    if not enabled_markets:
        raise ValueError("No enabled markets configured")

    next_open_time: datetime | None = None
    next_market: MarketInfo | None = None

    for code in enabled_markets:
        if code not in MARKETS:
            continue

        market = MARKETS[code]
        market_now = now.astimezone(market.timezone)

        # Calculate next open time for this market
        for days_ahead in range(7):  # Check next 7 days
            check_date = market_now.date() + timedelta(days=days_ahead)
            check_datetime = datetime.combine(
                check_date, market.open_time, tzinfo=market.timezone
            )

            # Skip weekends
            if check_datetime.weekday() >= 5:
                continue

            # Skip if this open time already passed today
            if check_datetime <= market_now:
                continue

            # Convert to UTC for comparison
            check_datetime_utc = check_datetime.astimezone(ZoneInfo("UTC"))

            if next_open_time is None or check_datetime_utc < next_open_time:
                next_open_time = check_datetime_utc
                next_market = market
                break

    if next_market is None or next_open_time is None:
        raise ValueError("Could not find next market open time")

    return next_market, next_open_time
