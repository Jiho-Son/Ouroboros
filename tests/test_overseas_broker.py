"""Tests for OverseasBroker.fetch_overseas_rankings with correct KIS API specs."""

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
