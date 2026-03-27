"""Tests for market schedule management."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.markets.schedule import (
    MARKETS,
    expand_market_codes,
    get_next_market_open,
    get_open_markets,
    is_market_open,
)


class TestMarketInfo:
    """Test MarketInfo dataclass."""

    def test_market_info_immutable(self) -> None:
        """MarketInfo should be frozen."""
        market = MARKETS["KR"]
        with pytest.raises(AttributeError):
            market.code = "US"  # type: ignore[misc]

    def test_all_markets_defined(self) -> None:
        """All 10 markets should be defined."""
        expected_markets = {
            "KR",
            "US_NASDAQ",
            "US_NYSE",
            "US_AMEX",
            "JP",
            "HK",
            "CN_SHA",
            "CN_SZA",
            "VN_HAN",
            "VN_HCM",
        }
        assert set(MARKETS.keys()) == expected_markets


class TestIsMarketOpen:
    """Test is_market_open function."""

    def test_kr_market_open_weekday(self) -> None:
        """KR market should be open during trading hours on weekday."""
        # Monday 2026-02-02 10:00 KST
        test_time = datetime(2026, 2, 2, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        assert is_market_open(MARKETS["KR"], test_time)

    def test_kr_market_closed_before_open(self) -> None:
        """KR market should be closed before 9:00."""
        # Monday 2026-02-02 08:30 KST
        test_time = datetime(2026, 2, 2, 8, 30, tzinfo=ZoneInfo("Asia/Seoul"))
        assert not is_market_open(MARKETS["KR"], test_time)

    def test_kr_market_closed_after_close(self) -> None:
        """KR market should be closed after 15:30."""
        # Monday 2026-02-02 15:30 KST (exact close time)
        test_time = datetime(2026, 2, 2, 15, 30, tzinfo=ZoneInfo("Asia/Seoul"))
        assert not is_market_open(MARKETS["KR"], test_time)

    def test_kr_market_closed_weekend(self) -> None:
        """KR market should be closed on weekends."""
        # Saturday 2026-02-07 10:00 KST
        test_time = datetime(2026, 2, 7, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        assert not is_market_open(MARKETS["KR"], test_time)

        # Sunday 2026-02-08 10:00 KST
        test_time = datetime(2026, 2, 8, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        assert not is_market_open(MARKETS["KR"], test_time)

    def test_us_nasdaq_open_with_dst(self) -> None:
        """US markets should respect DST."""
        # Monday 2026-06-01 10:00 EDT (DST in effect)
        test_time = datetime(2026, 6, 1, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        assert is_market_open(MARKETS["US_NASDAQ"], test_time)

        # Monday 2026-12-07 10:00 EST (no DST)
        test_time = datetime(2026, 12, 7, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        assert is_market_open(MARKETS["US_NASDAQ"], test_time)

    def test_jp_market_lunch_break(self) -> None:
        """JP market should be closed during lunch break."""
        # Monday 2026-02-02 12:00 JST (lunch break)
        test_time = datetime(2026, 2, 2, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        assert not is_market_open(MARKETS["JP"], test_time)

        # Before lunch
        test_time = datetime(2026, 2, 2, 11, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        assert is_market_open(MARKETS["JP"], test_time)

        # After lunch
        test_time = datetime(2026, 2, 2, 13, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        assert is_market_open(MARKETS["JP"], test_time)

    def test_hk_market_lunch_break(self) -> None:
        """HK market should be closed during lunch break."""
        # Monday 2026-02-02 12:30 HKT (lunch break)
        test_time = datetime(2026, 2, 2, 12, 30, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        assert not is_market_open(MARKETS["HK"], test_time)

    def test_timezone_conversion(self) -> None:
        """Should correctly convert timezones."""
        # 2026-02-02 10:00 KST = 2026-02-02 01:00 UTC
        test_time = datetime(2026, 2, 2, 1, 0, tzinfo=ZoneInfo("UTC"))
        assert is_market_open(MARKETS["KR"], test_time)


class TestGetOpenMarkets:
    """Test get_open_markets function."""

    def test_get_open_markets_all_closed(self) -> None:
        """Should return empty list when all markets closed."""
        # Sunday 2026-02-08 12:00 UTC (all markets closed)
        test_time = datetime(2026, 2, 8, 12, 0, tzinfo=ZoneInfo("UTC"))
        assert get_open_markets(now=test_time) == []

    def test_get_open_markets_kr_only(self) -> None:
        """Should return only KR when filtering enabled markets."""
        # Monday 2026-02-02 10:00 KST = 01:00 UTC
        test_time = datetime(2026, 2, 2, 1, 0, tzinfo=ZoneInfo("UTC"))
        open_markets = get_open_markets(enabled_markets=["KR"], now=test_time)
        assert len(open_markets) == 1
        assert open_markets[0].code == "KR"

    def test_get_open_markets_multiple(self) -> None:
        """Should return multiple markets when open."""
        # Monday 2026-02-02 14:30 EST = 19:30 UTC
        # US markets: 9:30-16:00 EST → 14:30-21:00 UTC (open)
        test_time = datetime(2026, 2, 2, 19, 30, tzinfo=ZoneInfo("UTC"))
        open_markets = get_open_markets(
            enabled_markets=["US_NASDAQ", "US_NYSE", "US_AMEX"], now=test_time
        )
        assert len(open_markets) == 3
        codes = {m.code for m in open_markets}
        assert codes == {"US_NASDAQ", "US_NYSE", "US_AMEX"}

    def test_get_open_markets_sorted(self) -> None:
        """Should return markets sorted by code."""
        # Monday 2026-02-02 14:30 EST
        test_time = datetime(2026, 2, 2, 19, 30, tzinfo=ZoneInfo("UTC"))
        open_markets = get_open_markets(
            enabled_markets=["US_NYSE", "US_AMEX", "US_NASDAQ"], now=test_time
        )
        codes = [m.code for m in open_markets]
        assert codes == sorted(codes)

    def test_get_open_markets_us_pre_extended_session(self) -> None:
        """US premarket should be considered open when extended sessions enabled."""
        # Monday 2026-02-02 08:30 EST = 13:30 UTC (premarket window)
        test_time = datetime(2026, 2, 2, 13, 30, tzinfo=ZoneInfo("UTC"))

        regular = get_open_markets(
            enabled_markets=["US_NASDAQ", "US_NYSE", "US_AMEX"],
            now=test_time,
        )
        assert regular == []

        extended = get_open_markets(
            enabled_markets=["US_NASDAQ", "US_NYSE", "US_AMEX"],
            now=test_time,
            include_extended_sessions=True,
        )
        assert {m.code for m in extended} == {"US_NASDAQ", "US_NYSE", "US_AMEX"}

    def test_get_open_markets_excludes_us_day_when_extended_enabled(self) -> None:
        """US_DAY should be treated as non-tradable even in extended-session lookup."""
        # Monday 2026-02-02 10:30 KST = 01:30 UTC (US_DAY by session classification)
        test_time = datetime(2026, 2, 2, 1, 30, tzinfo=ZoneInfo("UTC"))
        extended = get_open_markets(
            enabled_markets=["US_NASDAQ", "US_NYSE", "US_AMEX"],
            now=test_time,
            include_extended_sessions=True,
        )
        assert extended == []

    def test_get_open_markets_excludes_kr_weekend_extended_session(self) -> None:
        """KR extended lookup must not treat Saturday NXT_PRE hours as tradable."""
        # 2026-03-06 23:00 UTC = 2026-03-07 08:00 KST (Saturday)
        test_time = datetime(2026, 3, 6, 23, 0, tzinfo=ZoneInfo("UTC"))
        extended = get_open_markets(
            enabled_markets=["KR"],
            now=test_time,
            include_extended_sessions=True,
        )
        assert extended == []

    def test_get_open_markets_excludes_us_weekend_extended_session(self) -> None:
        """US extended lookup must not treat weekend as active."""
        # 2026-03-07 15:30 UTC = 2026-03-08 00:30 KST, still Saturday in New York.
        test_time = datetime(2026, 3, 7, 15, 30, tzinfo=ZoneInfo("UTC"))
        extended = get_open_markets(
            enabled_markets=["US_NASDAQ"],
            now=test_time,
            include_extended_sessions=True,
        )
        assert extended == []

    def test_get_open_markets_excludes_us_post_close_gap_after_dst_start(self) -> None:
        """17:30 EDT should already be outside US extended trading."""
        # 2026-03-09 21:30 UTC = 2026-03-09 17:30 EDT
        test_time = datetime(2026, 3, 9, 21, 30, tzinfo=ZoneInfo("UTC"))
        regular = get_open_markets(
            enabled_markets=["US_NASDAQ"],
            now=test_time,
        )
        assert regular == []

        extended = get_open_markets(
            enabled_markets=["US_NASDAQ"],
            now=test_time,
            include_extended_sessions=True,
        )
        assert extended == []


class TestGetNextMarketOpen:
    """Test get_next_market_open function."""

    def test_get_next_market_open_weekend(self) -> None:
        """Should find next Monday opening when called on weekend."""
        # Saturday 2026-02-07 12:00 UTC
        test_time = datetime(2026, 2, 7, 12, 0, tzinfo=ZoneInfo("UTC"))
        market, open_time = get_next_market_open(enabled_markets=["KR"], now=test_time)
        assert market.code == "KR"
        # Monday 2026-02-09 09:00 KST
        expected = datetime(2026, 2, 9, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        assert open_time == expected.astimezone(ZoneInfo("UTC"))

    def test_get_next_market_open_after_close(self) -> None:
        """Should find next day opening when called after market close."""
        # Monday 2026-02-02 16:00 KST (after close)
        test_time = datetime(2026, 2, 2, 16, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        market, open_time = get_next_market_open(enabled_markets=["KR"], now=test_time)
        assert market.code == "KR"
        # Tuesday 2026-02-03 09:00 KST
        expected = datetime(2026, 2, 3, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        assert open_time == expected.astimezone(ZoneInfo("UTC"))

    def test_get_next_market_open_multiple_markets(self) -> None:
        """Should find earliest opening market among multiple."""
        # Saturday 2026-02-07 12:00 UTC
        test_time = datetime(2026, 2, 7, 12, 0, tzinfo=ZoneInfo("UTC"))
        market, open_time = get_next_market_open(enabled_markets=["KR", "US_NASDAQ"], now=test_time)
        # Monday 2026-02-09: KR opens at 09:00 KST = 00:00 UTC
        # Monday 2026-02-09: US opens at 09:30 EST = 14:30 UTC
        # KR opens first
        assert market.code == "KR"

    def test_get_next_market_open_no_markets(self) -> None:
        """Should raise ValueError when no markets enabled."""
        test_time = datetime(2026, 2, 7, 12, 0, tzinfo=ZoneInfo("UTC"))
        with pytest.raises(ValueError, match="No enabled markets"):
            get_next_market_open(enabled_markets=[], now=test_time)

    def test_get_next_market_open_invalid_market(self) -> None:
        """Should skip invalid market codes."""
        test_time = datetime(2026, 2, 7, 12, 0, tzinfo=ZoneInfo("UTC"))
        market, _ = get_next_market_open(enabled_markets=["INVALID", "KR"], now=test_time)
        assert market.code == "KR"

    def test_get_next_market_open_prefers_extended_session(self) -> None:
        """Extended lookup should return premarket open time before regular open."""
        # Monday 2026-02-02 07:00 EST = 12:00 UTC
        # US_DAY is treated as non-tradable in extended lookup, so after entering
        # US_DAY the next tradable OFF->ON transition is US_PRE at 09:00 UTC next day.
        test_time = datetime(2026, 2, 2, 12, 0, tzinfo=ZoneInfo("UTC"))
        market, next_open = get_next_market_open(
            enabled_markets=["US_NASDAQ"],
            now=test_time,
            include_extended_sessions=True,
        )
        assert market.code == "US_NASDAQ"
        assert next_open == datetime(2026, 2, 3, 9, 0, tzinfo=ZoneInfo("UTC"))

    def test_get_next_market_open_prefers_dst_adjusted_us_pre_after_post_close(self) -> None:
        """DST-start week should reopen premarket at 04:00 EDT, not 05:00 EDT."""
        # 2026-03-09 21:30 UTC = 2026-03-09 17:30 EDT (already closed)
        test_time = datetime(2026, 3, 9, 21, 30, tzinfo=ZoneInfo("UTC"))
        market, next_open = get_next_market_open(
            enabled_markets=["US_NASDAQ"],
            now=test_time,
            include_extended_sessions=True,
        )
        assert market.code == "US_NASDAQ"
        assert next_open == datetime(2026, 3, 10, 8, 0, tzinfo=ZoneInfo("UTC"))


class TestExpandMarketCodes:
    """Test shorthand market expansion."""

    def test_expand_us_shorthand(self) -> None:
        assert expand_market_codes(["US"]) == ["US_NASDAQ", "US_NYSE", "US_AMEX"]

    def test_expand_cn_shorthand(self) -> None:
        assert expand_market_codes(["CN"]) == ["CN_SHA", "CN_SZA"]

    def test_expand_vn_shorthand(self) -> None:
        assert expand_market_codes(["VN"]) == ["VN_HAN", "VN_HCM"]

    def test_expand_mixed_codes(self) -> None:
        assert expand_market_codes(["KR", "US", "JP"]) == [
            "KR",
            "US_NASDAQ",
            "US_NYSE",
            "US_AMEX",
            "JP",
        ]

    def test_expand_preserves_unknown_code(self) -> None:
        assert expand_market_codes(["KR", "UNKNOWN"]) == ["KR", "UNKNOWN"]
