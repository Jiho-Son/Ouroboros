"""Tests for OverseasBroker — rankings, price, balance, order, and helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.broker.kis_api import KISBroker
from src.broker.overseas import _PRICE_EXCHANGE_MAP, _RANKING_EXCHANGE_MAP, OverseasBroker
from src.config import Settings


def _make_async_cm(mock_resp: AsyncMock) -> MagicMock:
    """Create an async context manager that returns mock_resp on __aenter__."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.fixture
def mock_settings() -> Settings:
    """Provide mock settings with correct default TR_IDs/paths."""
    return Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        MODE="paper",  # Explicitly set to avoid .env MODE=live override
    )


@pytest.fixture
def mock_broker(mock_settings: Settings) -> KISBroker:
    """Provide a mock KIS broker."""
    broker = KISBroker(mock_settings)
    broker.get_orderbook = AsyncMock()  # type: ignore[method-assign]
    return broker


@pytest.fixture
def overseas_broker(mock_broker: KISBroker) -> OverseasBroker:
    """Provide an OverseasBroker wrapping a mock KISBroker."""
    return OverseasBroker(mock_broker)


def _setup_broker_mocks(overseas_broker: OverseasBroker, mock_session: MagicMock) -> None:
    """Wire up common broker mocks."""
    overseas_broker._broker._rate_limiter.acquire = AsyncMock()
    overseas_broker._broker._get_session = MagicMock(return_value=mock_session)
    overseas_broker._broker._auth_headers = AsyncMock(return_value={})


class TestRankingExchangeMap:
    """Test exchange code mapping for ranking API."""

    def test_nasd_maps_to_nas(self) -> None:
        assert _RANKING_EXCHANGE_MAP["NASD"] == "NAS"

    def test_nyse_maps_to_nys(self) -> None:
        assert _RANKING_EXCHANGE_MAP["NYSE"] == "NYS"

    def test_amex_maps_to_ams(self) -> None:
        assert _RANKING_EXCHANGE_MAP["AMEX"] == "AMS"

    def test_sehk_maps_to_hks(self) -> None:
        assert _RANKING_EXCHANGE_MAP["SEHK"] == "HKS"

    def test_unmapped_exchange_passes_through(self) -> None:
        assert _RANKING_EXCHANGE_MAP.get("UNKNOWN", "UNKNOWN") == "UNKNOWN"

    def test_tse_unchanged(self) -> None:
        assert _RANKING_EXCHANGE_MAP["TSE"] == "TSE"


class TestConfigDefaults:
    """Test that config defaults match KIS official API specs."""

    def test_fluct_tr_id(self, mock_settings: Settings) -> None:
        assert mock_settings.OVERSEAS_RANKING_FLUCT_TR_ID == "HHDFS76290000"

    def test_volume_tr_id(self, mock_settings: Settings) -> None:
        assert mock_settings.OVERSEAS_RANKING_VOLUME_TR_ID == "HHDFS76270000"

    def test_fluct_path(self, mock_settings: Settings) -> None:
        assert (
            mock_settings.OVERSEAS_RANKING_FLUCT_PATH
            == "/uapi/overseas-stock/v1/ranking/updown-rate"
        )

    def test_volume_path(self, mock_settings: Settings) -> None:
        assert (
            mock_settings.OVERSEAS_RANKING_VOLUME_PATH
            == "/uapi/overseas-stock/v1/ranking/volume-surge"
        )


class TestFetchOverseasRankings:
    """Test fetch_overseas_rankings method."""

    @pytest.mark.asyncio
    async def test_fluctuation_uses_correct_params(self, overseas_broker: OverseasBroker) -> None:
        """Fluctuation ranking should use HHDFS76290000, updown-rate path, and correct params."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": [{"symb": "AAPL", "name": "Apple"}]})

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._auth_headers = AsyncMock(
            return_value={"authorization": "Bearer test"}
        )

        result = await overseas_broker.fetch_overseas_rankings("NASD", "fluctuation")

        assert len(result) == 1
        assert result[0]["symb"] == "AAPL"

        call_args = mock_session.get.call_args
        url = call_args[0][0]
        params = call_args[1]["params"]

        assert "/uapi/overseas-stock/v1/ranking/updown-rate" in url
        assert params["KEYB"] == ""  # Required by KIS API spec
        assert params["EXCD"] == "NAS"
        assert params["NDAY"] == "0"
        assert params["GUBN"] == "1"  # 1=상승율 — 변동성 스캐너는 급등 종목 우선
        assert params["VOL_RANG"] == "0"

        overseas_broker._broker._auth_headers.assert_called_with("HHDFS76290000")

    @pytest.mark.asyncio
    async def test_volume_uses_correct_params(self, overseas_broker: OverseasBroker) -> None:
        """Volume ranking should use HHDFS76270000, volume-surge path, and correct params."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": [{"symb": "TSLA", "name": "Tesla"}]})

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._auth_headers = AsyncMock(
            return_value={"authorization": "Bearer test"}
        )

        result = await overseas_broker.fetch_overseas_rankings("NYSE", "volume")

        assert len(result) == 1

        call_args = mock_session.get.call_args
        url = call_args[0][0]
        params = call_args[1]["params"]

        assert "/uapi/overseas-stock/v1/ranking/volume-surge" in url
        assert params["KEYB"] == ""  # Required by KIS API spec
        assert params["EXCD"] == "NYS"
        assert params["MIXN"] == "0"
        assert params["VOL_RANG"] == "0"
        assert "NDAY" not in params
        assert "GUBN" not in params

        overseas_broker._broker._auth_headers.assert_called_with("HHDFS76270000")

    @pytest.mark.asyncio
    async def test_404_returns_empty_list(self, overseas_broker: OverseasBroker) -> None:
        """HTTP 404 should return empty list (fallback) instead of raising."""
        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.text = AsyncMock(return_value="Not Found")

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)

        result = await overseas_broker.fetch_overseas_rankings("AMEX", "fluctuation")
        assert result == []

    @pytest.mark.asyncio
    async def test_non_404_error_raises(self, overseas_broker: OverseasBroker) -> None:
        """Non-404 HTTP errors should raise ConnectionError."""
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)

        with pytest.raises(ConnectionError, match="500"):
            await overseas_broker.fetch_overseas_rankings("NASD")

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty(self, overseas_broker: OverseasBroker) -> None:
        """Empty output in response should return empty list."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": []})

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)

        result = await overseas_broker.fetch_overseas_rankings("NASD")
        assert result == []


class TestGetDailyPrices:
    """Test overseas daily-price history fetch for ATR calculation."""

    @pytest.mark.asyncio
    async def test_get_daily_prices_uses_kis_dailyprice_api(
        self,
        overseas_broker: OverseasBroker,
    ) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "output2": [
                    {
                        "xymd": "20260307",
                        "open": "101.0",
                        "high": "105.0",
                        "low": "99.0",
                        "clos": "104.0",
                        "tvol": "12345",
                    },
                    {
                        "xymd": "20260306",
                        "open": "100.0",
                        "high": "104.0",
                        "low": "98.0",
                        "clos": "103.0",
                        "tvol": "12000",
                    },
                ]
            }
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._auth_headers = AsyncMock(
            return_value={"authorization": "Bearer test"}
        )

        rows = await overseas_broker.get_daily_prices("NASD", "AAPL", days=2)

        assert rows == [
            {
                "date": "20260306",
                "open": 100.0,
                "high": 104.0,
                "low": 98.0,
                "close": 103.0,
                "volume": 12000.0,
            },
            {
                "date": "20260307",
                "open": 101.0,
                "high": 105.0,
                "low": 99.0,
                "close": 104.0,
                "volume": 12345.0,
            },
        ]

        call_args = mock_session.get.call_args
        url = call_args[0][0]
        params = call_args[1]["params"]
        assert "/uapi/overseas-price/v1/quotations/dailyprice" in url
        assert params["AUTH"] == ""
        assert params["EXCD"] == "NAS"
        assert params["SYMB"] == "AAPL"
        assert params["GUBN"] == "0"
        assert params["MODP"] == "1"

        overseas_broker._broker._auth_headers.assert_called_with("HHDFS76240000")

    @pytest.mark.asyncio
    async def test_get_daily_prices_returns_most_recent_days_in_chronological_order(
        self,
        overseas_broker: OverseasBroker,
    ) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "output2": [
                    {
                        "xymd": "20260307",
                        "open": "107",
                        "high": "108",
                        "low": "106",
                        "clos": "107.5",
                        "tvol": "1007",
                    },
                    {
                        "xymd": "20260306",
                        "open": "106",
                        "high": "107",
                        "low": "105",
                        "clos": "106.5",
                        "tvol": "1006",
                    },
                    {
                        "xymd": "20260305",
                        "open": "105",
                        "high": "106",
                        "low": "104",
                        "clos": "105.5",
                        "tvol": "1005",
                    },
                    {
                        "xymd": "20260304",
                        "open": "104",
                        "high": "105",
                        "low": "103",
                        "clos": "104.5",
                        "tvol": "1004",
                    },
                ]
            }
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._auth_headers = AsyncMock(
            return_value={"authorization": "Bearer test"}
        )

        rows = await overseas_broker.get_daily_prices("NASD", "AAPL", days=2)

        assert [row["date"] for row in rows] == ["20260306", "20260307"]

    @pytest.mark.asyncio
    async def test_get_daily_prices_raises_on_non_200(
        self,
        overseas_broker: OverseasBroker,
    ) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)

        with pytest.raises(ConnectionError, match="500"):
            await overseas_broker.get_daily_prices("NASD", "AAPL")

    @pytest.mark.asyncio
    async def test_get_daily_prices_raises_on_network_error(
        self,
        overseas_broker: OverseasBroker,
    ) -> None:
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("timeout"))
        cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=cm)

        _setup_broker_mocks(overseas_broker, mock_session)

        with pytest.raises(ConnectionError, match="Network error"):
            await overseas_broker.get_daily_prices("NASD", "AAPL")

    @pytest.mark.asyncio
    async def test_ranking_disabled_returns_empty(self, overseas_broker: OverseasBroker) -> None:
        """When OVERSEAS_RANKING_ENABLED=False, should return empty immediately."""
        overseas_broker._broker._settings.OVERSEAS_RANKING_ENABLED = False
        result = await overseas_broker.fetch_overseas_rankings("NASD")
        assert result == []

    @pytest.mark.asyncio
    async def test_limit_truncates_results(self, overseas_broker: OverseasBroker) -> None:
        """Results should be truncated to the specified limit."""
        rows = [{"symb": f"SYM{i}"} for i in range(20)]
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": rows})

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)

        result = await overseas_broker.fetch_overseas_rankings("NASD", limit=5)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_network_error_raises(self, overseas_broker: OverseasBroker) -> None:
        """Network errors should raise ConnectionError."""
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("timeout"))
        cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=cm)

        _setup_broker_mocks(overseas_broker, mock_session)

        with pytest.raises(ConnectionError, match="Network error"):
            await overseas_broker.fetch_overseas_rankings("NASD")

    @pytest.mark.asyncio
    async def test_exchange_code_mapping_applied(self, overseas_broker: OverseasBroker) -> None:
        """All major exchanges should use mapped codes in API params."""
        for original, mapped in [("NASD", "NAS"), ("NYSE", "NYS"), ("AMEX", "AMS")]:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value={"output": [{"symb": "X"}]})

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

            _setup_broker_mocks(overseas_broker, mock_session)

            await overseas_broker.fetch_overseas_rankings(original)

            call_params = mock_session.get.call_args[1]["params"]
            assert call_params["EXCD"] == mapped, f"{original} should map to {mapped}"


class TestGetOverseasPrice:
    """Test get_overseas_price method."""

    @pytest.mark.asyncio
    async def test_success(self, overseas_broker: OverseasBroker) -> None:
        """Successful price fetch returns JSON data."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": {"last": "150.00"}})

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._auth_headers = AsyncMock(
            return_value={"authorization": "Bearer t"}
        )

        result = await overseas_broker.get_overseas_price("NASD", "AAPL")
        assert result["output"]["last"] == "150.00"

        call_args = mock_session.get.call_args
        params = call_args[1]["params"]
        assert params["EXCD"] == "NAS"  # NASD → NAS via _PRICE_EXCHANGE_MAP
        assert params["SYMB"] == "AAPL"

    @pytest.mark.asyncio
    async def test_http_error_raises(self, overseas_broker: OverseasBroker) -> None:
        """Non-200 response should raise ConnectionError."""
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value="Bad Request")

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)

        with pytest.raises(ConnectionError, match="get_overseas_price failed"):
            await overseas_broker.get_overseas_price("NASD", "AAPL")

    @pytest.mark.asyncio
    async def test_network_error_raises(self, overseas_broker: OverseasBroker) -> None:
        """Network error should raise ConnectionError."""
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("conn refused"))
        cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=cm)

        _setup_broker_mocks(overseas_broker, mock_session)

        with pytest.raises(ConnectionError, match="Network error"):
            await overseas_broker.get_overseas_price("NASD", "AAPL")


class TestGetOverseasOrderbook:
    """Test overseas executable quote-book access."""

    @pytest.mark.asyncio
    async def test_success(self, overseas_broker: OverseasBroker) -> None:
        """Successful orderbook fetch should use the asking-price endpoint and mapped code."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"output2": {"pask1": "200.60", "pbid1": "200.40"}}
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._auth_headers = AsyncMock(
            return_value={"authorization": "Bearer t"}
        )

        result = await overseas_broker.get_overseas_orderbook("NASD", "AAPL")

        assert result["output2"]["pask1"] == "200.60"
        call_args = mock_session.get.call_args
        url = call_args[0][0]
        params = call_args[1]["params"]
        assert "/uapi/overseas-price/v1/quotations/inquire-asking-price" in url
        assert params["AUTH"] == ""
        assert params["EXCD"] == "NAS"
        assert params["SYMB"] == "AAPL"
        overseas_broker._broker._auth_headers.assert_called_with("HHDFS76200100")

    @pytest.mark.asyncio
    async def test_http_error_raises(self, overseas_broker: OverseasBroker) -> None:
        """Non-200 response should raise ConnectionError."""
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value="Bad Request")

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)

        with pytest.raises(ConnectionError, match="get_overseas_orderbook failed"):
            await overseas_broker.get_overseas_orderbook("NASD", "AAPL")

    def test_extract_orderbook_top_levels_reads_best_ask_and_bid(self) -> None:
        ask, bid = OverseasBroker._extract_orderbook_top_levels(
            {"output2": {"pask1": "200.60", "pbid1": "200.40"}}
        )

        assert ask == 200.60
        assert bid == 200.40


class TestGetOverseasBalance:
    """Test get_overseas_balance method."""

    @pytest.mark.asyncio
    async def test_success(self, overseas_broker: OverseasBroker) -> None:
        """Successful balance fetch returns JSON data."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output1": [{"pdno": "AAPL"}]})

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)

        result = await overseas_broker.get_overseas_balance("NASD")
        assert result["output1"][0]["pdno"] == "AAPL"

    @pytest.mark.asyncio
    async def test_http_error_raises(self, overseas_broker: OverseasBroker) -> None:
        """Non-200 should raise ConnectionError."""
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Server Error")

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)

        with pytest.raises(ConnectionError, match="get_overseas_balance failed"):
            await overseas_broker.get_overseas_balance("NASD")

    @pytest.mark.asyncio
    async def test_network_error_raises(self, overseas_broker: OverseasBroker) -> None:
        """Network error should raise ConnectionError."""
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=TimeoutError("timeout"))
        cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=cm)

        _setup_broker_mocks(overseas_broker, mock_session)

        with pytest.raises(ConnectionError, match="Network error"):
            await overseas_broker.get_overseas_balance("NYSE")


class TestSendOverseasOrder:
    """Test send_overseas_order method."""

    @pytest.mark.asyncio
    async def test_buy_market_order(self, overseas_broker: OverseasBroker) -> None:
        """Market buy order should use VTTT1002U and ORD_DVSN=01."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"rt_cd": "0"})

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._get_hash_key = AsyncMock(return_value="hashval")

        result = await overseas_broker.send_overseas_order("NASD", "AAPL", "BUY", 10)
        assert result["rt_cd"] == "0"

        # Verify BUY TR_ID
        overseas_broker._broker._auth_headers.assert_called_with("VTTT1002U")

        call_args = mock_session.post.call_args
        body = call_args[1]["json"]
        assert body["ORD_DVSN"] == "01"  # market order
        assert body["OVRS_ORD_UNPR"] == "0"

    @pytest.mark.asyncio
    async def test_sell_limit_order(self, overseas_broker: OverseasBroker) -> None:
        """Limit sell order should use VTTT1001U and ORD_DVSN=00."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"rt_cd": "0"})

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._get_hash_key = AsyncMock(return_value="hashval")

        result = await overseas_broker.send_overseas_order("NYSE", "MSFT", "SELL", 5, price=350.0)
        assert result["rt_cd"] == "0"

        overseas_broker._broker._auth_headers.assert_called_with("VTTT1001U")

        call_args = mock_session.post.call_args
        body = call_args[1]["json"]
        assert body["ORD_DVSN"] == "00"  # limit order
        assert body["OVRS_ORD_UNPR"] == "350.00"

    @pytest.mark.asyncio
    async def test_limit_order_keeps_four_decimals_below_one_dollar(
        self,
        overseas_broker: OverseasBroker,
    ) -> None:
        """Sub-$1 limit orders must preserve four decimals for KIS price rules."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"rt_cd": "0"})

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._get_hash_key = AsyncMock(return_value="hashval")

        await overseas_broker.send_overseas_order("AMEX", "ATRA", "SELL", 5, price=0.8765)

        call_args = mock_session.post.call_args
        body = call_args[1]["json"]
        assert body["ORD_DVSN"] == "00"
        assert body["OVRS_ORD_UNPR"] == "0.8765"

    @pytest.mark.asyncio
    async def test_order_http_error_raises(self, overseas_broker: OverseasBroker) -> None:
        """Non-200 should raise ConnectionError."""
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value="Bad Request")

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._get_hash_key = AsyncMock(return_value="hashval")

        with pytest.raises(ConnectionError, match="send_overseas_order failed"):
            await overseas_broker.send_overseas_order("NASD", "AAPL", "BUY", 1)

    @pytest.mark.asyncio
    async def test_order_network_error_raises(self, overseas_broker: OverseasBroker) -> None:
        """Network error should raise ConnectionError."""
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("conn reset"))
        cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=cm)

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._get_hash_key = AsyncMock(return_value="hashval")

        with pytest.raises(ConnectionError, match="Network error"):
            await overseas_broker.send_overseas_order("NASD", "TSLA", "SELL", 2)


class TestGetCurrencyCode:
    """Test _get_currency_code mapping."""

    def test_us_exchanges(self, overseas_broker: OverseasBroker) -> None:
        assert overseas_broker._get_currency_code("NASD") == "USD"
        assert overseas_broker._get_currency_code("NYSE") == "USD"
        assert overseas_broker._get_currency_code("AMEX") == "USD"

    def test_japan(self, overseas_broker: OverseasBroker) -> None:
        assert overseas_broker._get_currency_code("TSE") == "JPY"

    def test_hong_kong(self, overseas_broker: OverseasBroker) -> None:
        assert overseas_broker._get_currency_code("SEHK") == "HKD"

    def test_china(self, overseas_broker: OverseasBroker) -> None:
        assert overseas_broker._get_currency_code("SHAA") == "CNY"
        assert overseas_broker._get_currency_code("SZAA") == "CNY"

    def test_vietnam(self, overseas_broker: OverseasBroker) -> None:
        assert overseas_broker._get_currency_code("HNX") == "VND"
        assert overseas_broker._get_currency_code("HSX") == "VND"

    def test_unknown_defaults_usd(self, overseas_broker: OverseasBroker) -> None:
        assert overseas_broker._get_currency_code("UNKNOWN") == "USD"


class TestExtractRankingRows:
    """Test _extract_ranking_rows helper."""

    def test_output_key(self, overseas_broker: OverseasBroker) -> None:
        data = {"output": [{"a": 1}, {"b": 2}]}
        assert overseas_broker._extract_ranking_rows(data) == [{"a": 1}, {"b": 2}]

    def test_output1_key(self, overseas_broker: OverseasBroker) -> None:
        data = {"output1": [{"c": 3}]}
        assert overseas_broker._extract_ranking_rows(data) == [{"c": 3}]

    def test_output2_key(self, overseas_broker: OverseasBroker) -> None:
        data = {"output2": [{"d": 4}]}
        assert overseas_broker._extract_ranking_rows(data) == [{"d": 4}]

    def test_no_list_returns_empty(self, overseas_broker: OverseasBroker) -> None:
        data = {"output": "not a list"}
        assert overseas_broker._extract_ranking_rows(data) == []

    def test_empty_data(self, overseas_broker: OverseasBroker) -> None:
        assert overseas_broker._extract_ranking_rows({}) == []

    def test_filters_non_dict_rows(self, overseas_broker: OverseasBroker) -> None:
        data = {"output": [{"a": 1}, "invalid", {"b": 2}]}
        assert overseas_broker._extract_ranking_rows(data) == [{"a": 1}, {"b": 2}]


class TestPriceExchangeMap:
    """Test _PRICE_EXCHANGE_MAP is applied in get_overseas_price (issue #151)."""

    def test_price_map_equals_ranking_map(self) -> None:
        assert _PRICE_EXCHANGE_MAP is _RANKING_EXCHANGE_MAP

    @pytest.mark.parametrize(
        "original,expected",
        [
            ("NASD", "NAS"),
            ("NYSE", "NYS"),
            ("AMEX", "AMS"),
        ],
    )
    def test_us_exchange_code_mapping(self, original: str, expected: str) -> None:
        assert _PRICE_EXCHANGE_MAP[original] == expected

    @pytest.mark.asyncio
    async def test_get_overseas_price_sends_mapped_code(
        self, overseas_broker: OverseasBroker
    ) -> None:
        """NASD → NAS must be sent to HHDFS00000300."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": {"last": "200.00"}})

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))
        _setup_broker_mocks(overseas_broker, mock_session)

        await overseas_broker.get_overseas_price("NASD", "AAPL")

        params = mock_session.get.call_args[1]["params"]
        assert params["EXCD"] == "NAS"


class TestOrderRtCdCheck:
    """Test that send_overseas_order checks rt_cd and logs accordingly (issue #151)."""

    @pytest.fixture
    def overseas_broker(self, mock_settings: Settings) -> OverseasBroker:
        broker = MagicMock(spec=KISBroker)
        broker._settings = mock_settings
        broker._account_no = "12345678"
        broker._product_cd = "01"
        broker._base_url = "https://openapivts.koreainvestment.com:9443"
        broker._rate_limiter = AsyncMock()
        broker._rate_limiter.acquire = AsyncMock()
        broker._auth_headers = AsyncMock(return_value={"authorization": "Bearer t"})
        broker._get_hash_key = AsyncMock(return_value="hashval")
        return OverseasBroker(broker)

    @pytest.mark.asyncio
    async def test_success_rt_cd_returns_data(self, overseas_broker: OverseasBroker) -> None:
        """rt_cd='0' → order accepted, data returned."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"rt_cd": "0", "msg1": "완료"})

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_make_async_cm(mock_resp))
        overseas_broker._broker._get_session = MagicMock(return_value=mock_session)

        result = await overseas_broker.send_overseas_order("NASD", "AAPL", "BUY", 10, price=150.0)
        assert result["rt_cd"] == "0"

    @pytest.mark.asyncio
    async def test_error_rt_cd_returns_data_with_msg(self, overseas_broker: OverseasBroker) -> None:
        """rt_cd != '0' → order rejected, data still returned (caller checks rt_cd)."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"rt_cd": "1", "msg1": "주문가능금액이 부족합니다."}
        )

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_make_async_cm(mock_resp))
        overseas_broker._broker._get_session = MagicMock(return_value=mock_session)

        result = await overseas_broker.send_overseas_order("NASD", "AAPL", "BUY", 10, price=150.0)
        assert result["rt_cd"] == "1"
        assert "부족" in result["msg1"]


class TestPaperOverseasCash:
    """Test PAPER_OVERSEAS_CASH config setting (issue #151)."""

    def test_default_value(self) -> None:
        settings = Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
        )
        assert settings.PAPER_OVERSEAS_CASH == 50000.0

    def test_env_override(self) -> None:
        import os

        os.environ["PAPER_OVERSEAS_CASH"] = "25000"
        settings = Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
        )
        assert settings.PAPER_OVERSEAS_CASH == 25000.0
        del os.environ["PAPER_OVERSEAS_CASH"]

    def test_zero_disables_fallback(self) -> None:
        import os

        os.environ["PAPER_OVERSEAS_CASH"] = "0"
        settings = Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="g",
        )
        assert settings.PAPER_OVERSEAS_CASH == 0.0


# ---------------------------------------------------------------------------
# TR_ID live/paper branching — overseas (issues #201, #203)
# ---------------------------------------------------------------------------


def _make_overseas_broker_with_mode(mode: str) -> OverseasBroker:
    s = Settings(
        KIS_APP_KEY="k",
        KIS_APP_SECRET="s",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="g",
        DB_PATH=":memory:",
        MODE=mode,
    )
    kis = KISBroker(s)
    kis._access_token = "tok"
    kis._token_expires_at = float("inf")
    kis._rate_limiter.acquire = AsyncMock()
    return OverseasBroker(kis)


class TestOverseasTRIDBranching:
    """get_overseas_balance and send_overseas_order must use correct TR_ID."""

    @pytest.mark.asyncio
    async def test_get_overseas_balance_paper_uses_vtts3012r(self) -> None:
        broker = _make_overseas_broker_with_mode("paper")
        captured: list[str] = []

        async def mock_auth_headers(tr_id: str) -> dict:
            captured.append(tr_id)
            return {"tr_id": tr_id, "authorization": "Bearer tok"}

        broker._broker._auth_headers = mock_auth_headers  # type: ignore[method-assign]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output1": [], "output2": []})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        broker._broker._get_session = MagicMock(return_value=mock_session)

        await broker.get_overseas_balance("NASD")
        assert "VTTS3012R" in captured

    @pytest.mark.asyncio
    async def test_get_overseas_balance_live_uses_ttts3012r(self) -> None:
        broker = _make_overseas_broker_with_mode("live")
        captured: list[str] = []

        async def mock_auth_headers(tr_id: str) -> dict:
            captured.append(tr_id)
            return {"tr_id": tr_id, "authorization": "Bearer tok"}

        broker._broker._auth_headers = mock_auth_headers  # type: ignore[method-assign]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output1": [], "output2": []})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        broker._broker._get_session = MagicMock(return_value=mock_session)

        await broker.get_overseas_balance("NASD")
        assert "TTTS3012R" in captured

    @pytest.mark.asyncio
    async def test_send_overseas_order_buy_paper_uses_vttt1002u(self) -> None:
        broker = _make_overseas_broker_with_mode("paper")
        captured: list[str] = []

        async def mock_auth_headers(tr_id: str) -> dict:
            captured.append(tr_id)
            return {"tr_id": tr_id, "authorization": "Bearer tok"}

        broker._broker._auth_headers = mock_auth_headers  # type: ignore[method-assign]
        broker._broker._get_hash_key = AsyncMock(return_value="h")  # type: ignore[method-assign]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        broker._broker._get_session = MagicMock(return_value=mock_session)

        await broker.send_overseas_order("NASD", "AAPL", "BUY", 1)
        assert "VTTT1002U" in captured

    @pytest.mark.asyncio
    async def test_send_overseas_order_buy_live_uses_tttt1002u(self) -> None:
        broker = _make_overseas_broker_with_mode("live")
        captured: list[str] = []

        async def mock_auth_headers(tr_id: str) -> dict:
            captured.append(tr_id)
            return {"tr_id": tr_id, "authorization": "Bearer tok"}

        broker._broker._auth_headers = mock_auth_headers  # type: ignore[method-assign]
        broker._broker._get_hash_key = AsyncMock(return_value="h")  # type: ignore[method-assign]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        broker._broker._get_session = MagicMock(return_value=mock_session)

        await broker.send_overseas_order("NASD", "AAPL", "BUY", 1)
        assert "TTTT1002U" in captured

    @pytest.mark.asyncio
    async def test_send_overseas_order_sell_paper_uses_vttt1001u(self) -> None:
        broker = _make_overseas_broker_with_mode("paper")
        captured: list[str] = []

        async def mock_auth_headers(tr_id: str) -> dict:
            captured.append(tr_id)
            return {"tr_id": tr_id, "authorization": "Bearer tok"}

        broker._broker._auth_headers = mock_auth_headers  # type: ignore[method-assign]
        broker._broker._get_hash_key = AsyncMock(return_value="h")  # type: ignore[method-assign]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        broker._broker._get_session = MagicMock(return_value=mock_session)

        await broker.send_overseas_order("NASD", "AAPL", "SELL", 1)
        assert "VTTT1001U" in captured

    @pytest.mark.asyncio
    async def test_send_overseas_order_sell_live_uses_tttt1006u(self) -> None:
        broker = _make_overseas_broker_with_mode("live")
        captured: list[str] = []

        async def mock_auth_headers(tr_id: str) -> dict:
            captured.append(tr_id)
            return {"tr_id": tr_id, "authorization": "Bearer tok"}

        broker._broker._auth_headers = mock_auth_headers  # type: ignore[method-assign]
        broker._broker._get_hash_key = AsyncMock(return_value="h")  # type: ignore[method-assign]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"rt_cd": "0", "msg1": "OK"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        broker._broker._get_session = MagicMock(return_value=mock_session)

        await broker.send_overseas_order("NASD", "AAPL", "SELL", 1)
        assert "TTTT1006U" in captured


class TestGetOverseasPendingOrders:
    """Tests for get_overseas_pending_orders method."""

    @pytest.mark.asyncio
    async def test_paper_mode_returns_empty(self, overseas_broker: OverseasBroker) -> None:
        """Paper mode should immediately return [] without any API call."""
        # Default mock_settings has MODE="paper"
        overseas_broker._broker._settings = overseas_broker._broker._settings.model_copy(
            update={"MODE": "paper"}
        )
        mock_session = MagicMock()
        _setup_broker_mocks(overseas_broker, mock_session)

        result = await overseas_broker.get_overseas_pending_orders("NASD")

        assert result == []
        mock_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_live_mode_calls_ttts3018r_with_correct_params(
        self, overseas_broker: OverseasBroker
    ) -> None:
        """Live mode should call TTTS3018R with OVRS_EXCG_CD and return output list."""
        overseas_broker._broker._settings = overseas_broker._broker._settings.model_copy(
            update={"MODE": "live"}
        )
        captured_tr_id: list[str] = []
        captured_params: list[dict] = []

        async def mock_auth_headers(tr_id: str) -> dict:
            captured_tr_id.append(tr_id)
            return {}

        overseas_broker._broker._auth_headers = mock_auth_headers  # type: ignore[method-assign]

        pending_orders = [{"odno": "001", "pdno": "AAPL", "sll_buy_dvsn_cd": "02", "nccs_qty": "5"}]
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": pending_orders})

        mock_session = MagicMock()

        def _capture_get(url: str, **kwargs: object) -> MagicMock:
            captured_params.append(kwargs.get("params", {}))
            return _make_async_cm(mock_resp)

        mock_session.get = MagicMock(side_effect=_capture_get)
        overseas_broker._broker._rate_limiter.acquire = AsyncMock()
        overseas_broker._broker._get_session = MagicMock(return_value=mock_session)

        result = await overseas_broker.get_overseas_pending_orders("NASD")

        assert result == pending_orders
        assert captured_tr_id == ["TTTS3018R"]
        assert captured_params[0]["OVRS_EXCG_CD"] == "NASD"

    @pytest.mark.asyncio
    async def test_live_mode_connection_error(self, overseas_broker: OverseasBroker) -> None:
        """Network error in live mode should raise ConnectionError."""
        overseas_broker._broker._settings = overseas_broker._broker._settings.model_copy(
            update={"MODE": "live"}
        )
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("timeout"))
        cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=cm)
        _setup_broker_mocks(overseas_broker, mock_session)

        with pytest.raises(ConnectionError, match="Network error fetching pending orders"):
            await overseas_broker.get_overseas_pending_orders("NASD")


class TestCancelOverseasOrder:
    """Tests for cancel_overseas_order method."""

    def _setup_cancel_mocks(
        self, overseas_broker: OverseasBroker, response: dict
    ) -> tuple[list[str], MagicMock]:
        """Wire up mocks for a successful cancel call; return captured TR_IDs and session."""
        captured_tr_ids: list[str] = []

        async def mock_auth_headers(tr_id: str) -> dict:
            captured_tr_ids.append(tr_id)
            return {}

        overseas_broker._broker._auth_headers = mock_auth_headers  # type: ignore[method-assign]
        overseas_broker._broker._get_hash_key = AsyncMock(return_value="hash_val")  # type: ignore[method-assign]
        overseas_broker._broker._rate_limiter.acquire = AsyncMock()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=response)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_make_async_cm(mock_resp))
        overseas_broker._broker._get_session = MagicMock(return_value=mock_session)

        return captured_tr_ids, mock_session

    @pytest.mark.asyncio
    async def test_us_live_uses_tttt1004u(self, overseas_broker: OverseasBroker) -> None:
        """US exchange in live mode should use TTTT1004U."""
        overseas_broker._broker._settings = overseas_broker._broker._settings.model_copy(
            update={"MODE": "live"}
        )
        captured, _ = self._setup_cancel_mocks(overseas_broker, {"rt_cd": "0", "msg1": "OK"})

        await overseas_broker.cancel_overseas_order("NASD", "AAPL", "ORD001", 5)

        assert "TTTT1004U" in captured

    @pytest.mark.asyncio
    async def test_us_paper_uses_vttt1004u(self, overseas_broker: OverseasBroker) -> None:
        """US exchange in paper mode should use VTTT1004U."""
        # Default mock_settings has MODE="paper"
        captured, _ = self._setup_cancel_mocks(overseas_broker, {"rt_cd": "0", "msg1": "OK"})

        await overseas_broker.cancel_overseas_order("NASD", "AAPL", "ORD001", 5)

        assert "VTTT1004U" in captured

    @pytest.mark.asyncio
    async def test_hk_live_uses_ttts1003u(self, overseas_broker: OverseasBroker) -> None:
        """SEHK exchange in live mode should use TTTS1003U."""
        overseas_broker._broker._settings = overseas_broker._broker._settings.model_copy(
            update={"MODE": "live"}
        )
        captured, _ = self._setup_cancel_mocks(overseas_broker, {"rt_cd": "0", "msg1": "OK"})

        await overseas_broker.cancel_overseas_order("SEHK", "0700", "ORD002", 10)

        assert "TTTS1003U" in captured

    @pytest.mark.asyncio
    async def test_cancel_sets_rvse_cncl_dvsn_cd_02(self, overseas_broker: OverseasBroker) -> None:
        """Cancel body must include RVSE_CNCL_DVSN_CD='02' and OVRS_ORD_UNPR='0'."""
        captured_body: list[dict] = []

        async def mock_auth_headers(tr_id: str) -> dict:
            return {}

        overseas_broker._broker._auth_headers = mock_auth_headers  # type: ignore[method-assign]
        overseas_broker._broker._get_hash_key = AsyncMock(return_value="h")  # type: ignore[method-assign]
        overseas_broker._broker._rate_limiter.acquire = AsyncMock()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"rt_cd": "0"})

        mock_session = MagicMock()

        def _capture_post(url: str, **kwargs: object) -> MagicMock:
            captured_body.append(kwargs.get("json", {}))
            return _make_async_cm(mock_resp)

        mock_session.post = MagicMock(side_effect=_capture_post)
        overseas_broker._broker._get_session = MagicMock(return_value=mock_session)

        await overseas_broker.cancel_overseas_order("NASD", "AAPL", "ORD003", 3)

        assert captured_body[0]["RVSE_CNCL_DVSN_CD"] == "02"
        assert captured_body[0]["OVRS_ORD_UNPR"] == "0"
        assert captured_body[0]["ORGN_ODNO"] == "ORD003"

    @pytest.mark.asyncio
    async def test_cancel_sets_hashkey_header(self, overseas_broker: OverseasBroker) -> None:
        """hashkey must be set in the request headers."""
        captured_headers: list[dict] = []
        overseas_broker._broker._get_hash_key = AsyncMock(return_value="test_hash")  # type: ignore[method-assign]
        overseas_broker._broker._rate_limiter.acquire = AsyncMock()

        async def mock_auth_headers(tr_id: str) -> dict:
            return {"tr_id": tr_id}

        overseas_broker._broker._auth_headers = mock_auth_headers  # type: ignore[method-assign]

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"rt_cd": "0"})

        mock_session = MagicMock()

        def _capture_post(url: str, **kwargs: object) -> MagicMock:
            captured_headers.append(dict(kwargs.get("headers", {})))
            return _make_async_cm(mock_resp)

        mock_session.post = MagicMock(side_effect=_capture_post)
        overseas_broker._broker._get_session = MagicMock(return_value=mock_session)

        await overseas_broker.cancel_overseas_order("NASD", "AAPL", "ORD004", 2)

        assert captured_headers[0].get("hashkey") == "test_hash"
