"""Tests for OverseasBroker — rankings, price, balance, order, and helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker, _RANKING_EXCHANGE_MAP
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
        assert mock_settings.OVERSEAS_RANKING_FLUCT_PATH == "/uapi/overseas-stock/v1/ranking/updown-rate"

    def test_volume_path(self, mock_settings: Settings) -> None:
        assert mock_settings.OVERSEAS_RANKING_VOLUME_PATH == "/uapi/overseas-stock/v1/ranking/volume-surge"


class TestFetchOverseasRankings:
    """Test fetch_overseas_rankings method."""

    @pytest.mark.asyncio
    async def test_fluctuation_uses_correct_params(
        self, overseas_broker: OverseasBroker
    ) -> None:
        """Fluctuation ranking should use HHDFS76290000, updown-rate path, and correct params."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"output": [{"symb": "AAPL", "name": "Apple"}]}
        )

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
        assert params["EXCD"] == "NAS"
        assert params["NDAY"] == "0"
        assert params["GUBN"] == "1"
        assert params["VOL_RANG"] == "0"

        overseas_broker._broker._auth_headers.assert_called_with("HHDFS76290000")

    @pytest.mark.asyncio
    async def test_volume_uses_correct_params(
        self, overseas_broker: OverseasBroker
    ) -> None:
        """Volume ranking should use HHDFS76270000, volume-surge path, and correct params."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={"output": [{"symb": "TSLA", "name": "Tesla"}]}
        )

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
        assert params["EXCD"] == "NYS"
        assert params["MIXN"] == "0"
        assert params["VOL_RANG"] == "0"
        assert "NDAY" not in params
        assert "GUBN" not in params

        overseas_broker._broker._auth_headers.assert_called_with("HHDFS76270000")

    @pytest.mark.asyncio
    async def test_404_returns_empty_list(
        self, overseas_broker: OverseasBroker
    ) -> None:
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
    async def test_non_404_error_raises(
        self, overseas_broker: OverseasBroker
    ) -> None:
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
    async def test_empty_response_returns_empty(
        self, overseas_broker: OverseasBroker
    ) -> None:
        """Empty output in response should return empty list."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": []})

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)

        result = await overseas_broker.fetch_overseas_rankings("NASD")
        assert result == []

    @pytest.mark.asyncio
    async def test_ranking_disabled_returns_empty(
        self, overseas_broker: OverseasBroker
    ) -> None:
        """When OVERSEAS_RANKING_ENABLED=False, should return empty immediately."""
        overseas_broker._broker._settings.OVERSEAS_RANKING_ENABLED = False
        result = await overseas_broker.fetch_overseas_rankings("NASD")
        assert result == []

    @pytest.mark.asyncio
    async def test_limit_truncates_results(
        self, overseas_broker: OverseasBroker
    ) -> None:
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
    async def test_network_error_raises(
        self, overseas_broker: OverseasBroker
    ) -> None:
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
    async def test_exchange_code_mapping_applied(
        self, overseas_broker: OverseasBroker
    ) -> None:
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
        overseas_broker._broker._auth_headers = AsyncMock(return_value={"authorization": "Bearer t"})

        result = await overseas_broker.get_overseas_price("NASD", "AAPL")
        assert result["output"]["last"] == "150.00"

        call_args = mock_session.get.call_args
        params = call_args[1]["params"]
        assert params["EXCD"] == "NASD"
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
        """Limit sell order should use VTTT1006U and ORD_DVSN=00."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"rt_cd": "0"})

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_make_async_cm(mock_resp))

        _setup_broker_mocks(overseas_broker, mock_session)
        overseas_broker._broker._get_hash_key = AsyncMock(return_value="hashval")

        result = await overseas_broker.send_overseas_order("NYSE", "MSFT", "SELL", 5, price=350.0)
        assert result["rt_cd"] == "0"

        overseas_broker._broker._auth_headers.assert_called_with("VTTT1006U")

        call_args = mock_session.post.call_args
        body = call_args[1]["json"]
        assert body["ORD_DVSN"] == "00"  # limit order
        assert body["OVRS_ORD_UNPR"] == "350.0"

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
