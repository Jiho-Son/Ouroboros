"""Market schedule management with timezone support."""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
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

MARKET_SHORTHAND: dict[str, list[str]] = {
    "US": ["US_NASDAQ", "US_NYSE", "US_AMEX"],
    "CN": ["CN_SHA", "CN_SZA"],
    "VN": ["VN_HAN", "VN_HCM"],
}


def expand_market_codes(codes: list[str]) -> list[str]:
    """Expand shorthand market codes into concrete exchange market codes."""
    expanded: list[str] = []
    for code in codes:
        if code in MARKET_SHORTHAND:
            expanded.extend(MARKET_SHORTHAND[code])
        else:
            expanded.append(code)
    return expanded


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
    enabled_markets: list[str] | None = None,
    now: datetime | None = None,
    *,
    include_extended_sessions: bool = False,
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

    def is_available(market: MarketInfo) -> bool:
        if not include_extended_sessions:
            return is_market_open(market, now)
        if market.code == "KR" or market.code.startswith("US"):
            # Import lazily to avoid module cycle at import-time.
            from src.core.order_policy import classify_session_id

            session_id = classify_session_id(market, now)
            return session_id not in {"KR_OFF", "US_OFF", "US_DAY"}
        return is_market_open(market, now)

    open_markets = [
        MARKETS[code] for code in enabled_markets if code in MARKETS and is_available(MARKETS[code])
    ]

    return sorted(open_markets, key=lambda m: m.code)


def get_next_market_open(
    enabled_markets: list[str] | None = None,
    now: datetime | None = None,
    *,
    include_extended_sessions: bool = False,
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

    def first_extended_open_after(market: MarketInfo, start_utc: datetime) -> datetime | None:
        # Search minute-by-minute for KR/US session transition into active window.
        # Bounded to 7 days to match existing behavior.
        from src.core.order_policy import classify_session_id

        ts = start_utc.astimezone(ZoneInfo("UTC")).replace(second=0, microsecond=0)
        prev_active = classify_session_id(market, ts) not in {"KR_OFF", "US_OFF", "US_DAY"}
        for _ in range(7 * 24 * 60):
            ts = ts + timedelta(minutes=1)
            active = classify_session_id(market, ts) not in {"KR_OFF", "US_OFF", "US_DAY"}
            if active and not prev_active:
                return ts
            prev_active = active
        return None

    for code in enabled_markets:
        if code not in MARKETS:
            continue

        market = MARKETS[code]
        market_now = now.astimezone(market.timezone)

        if include_extended_sessions and (market.code == "KR" or market.code.startswith("US")):
            ext_open = first_extended_open_after(market, now.astimezone(UTC))
            if ext_open and (next_open_time is None or ext_open < next_open_time):
                next_open_time = ext_open
                next_market = market
            continue

        # Calculate next open time for this market
        for days_ahead in range(7):  # Check next 7 days
            check_date = market_now.date() + timedelta(days=days_ahead)
            check_datetime = datetime.combine(check_date, market.open_time, tzinfo=market.timezone)

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
