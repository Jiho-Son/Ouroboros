"""TDD tests for broker/kis_api.py — written BEFORE implementation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.broker.kis_api import KISBroker

# ---------------------------------------------------------------------------
# Token Management
# ---------------------------------------------------------------------------


class TestTokenManagement:
    """Access token must be auto-refreshed and cached."""

    @pytest.mark.asyncio
    async def test_fetches_token_on_first_call(self, settings):
        broker = KISBroker(settings)
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "access_token": "tok_abc123",
                "token_type": "Bearer",
                "expires_in": 86400,
            }
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            token = await broker._ensure_token()
            assert token == "tok_abc123"

        await broker.close()

    @pytest.mark.asyncio
    async def test_fetches_websocket_approval_key(self, settings):
        broker = KISBroker(settings)
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"approval_key": "ws_approval_123"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            approval = await broker.get_websocket_approval_key()

        assert approval == "ws_approval_123"
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["appkey"] == settings.KIS_APP_KEY
        assert kwargs["json"]["secretkey"] == settings.KIS_APP_SECRET

        await broker.close()

    @pytest.mark.asyncio
    async def test_reuses_cached_token(self, settings):
        broker = KISBroker(settings)
        now = asyncio.get_event_loop().time()
        broker._access_token = "cached_token"
        broker._token_expires_at = now + 3600
        broker._token_refresh_at = now + 1800

        token = await broker._ensure_token()
        assert token == "cached_token"

        await broker.close()

    @pytest.mark.asyncio
    async def test_records_refresh_deadline_when_token_is_issued(self, settings):
        broker = KISBroker(settings)
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "access_token": "tok_refresh_deadline",
                "token_type": "Bearer",
                "expires_in": 86400,
            }
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            token = await broker._ensure_token()

        assert token == "tok_refresh_deadline"
        assert broker._token_refresh_at < broker._token_expires_at
        assert broker._token_expires_at - broker._token_refresh_at == pytest.approx(1800, abs=1)

        await broker.close()

    @pytest.mark.asyncio
    async def test_refreshes_cached_token_after_refresh_deadline(self, settings):
        broker = KISBroker(settings)
        now = asyncio.get_event_loop().time()
        broker._access_token = "cached_token"
        broker._token_expires_at = now + 300
        broker._token_refresh_at = now - 1

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "access_token": "tok_proactive_refresh",
                "token_type": "Bearer",
                "expires_in": 86400,
            }
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
            token = await broker._ensure_token()

        assert token == "tok_proactive_refresh"
        assert mock_post.call_count == 1

        await broker.close()

    @pytest.mark.asyncio
    async def test_concurrent_token_refresh_calls_api_once(self, settings):
        """Multiple concurrent token requests should only call API once."""
        broker = KISBroker(settings)

        # Track how many times the mock API is called
        call_count = [0]

        def create_mock_resp():
            call_count[0] += 1
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(
                return_value={
                    "access_token": "tok_concurrent",
                    "token_type": "Bearer",
                    "expires_in": 86400,
                }
            )
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)
            return mock_resp

        with patch("aiohttp.ClientSession.post", return_value=create_mock_resp()):
            # Launch 5 concurrent token requests
            tokens = await asyncio.gather(
                broker._ensure_token(),
                broker._ensure_token(),
                broker._ensure_token(),
                broker._ensure_token(),
                broker._ensure_token(),
            )

            # All should get the same token
            assert all(t == "tok_concurrent" for t in tokens)
            # API should be called only once (due to lock)
            assert call_count[0] == 1

        await broker.close()

    @pytest.mark.asyncio
    async def test_concurrent_calls_after_refresh_deadline_calls_api_once(self, settings):
        """Concurrent callers should share one proactive refresh attempt."""
        broker = KISBroker(settings)
        now = asyncio.get_event_loop().time()
        broker._access_token = "cached_token"
        broker._token_expires_at = now + 300
        broker._token_refresh_at = now - 1

        call_count = [0]

        def create_mock_resp():
            call_count[0] += 1
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(
                return_value={
                    "access_token": "tok_refresh_once",
                    "token_type": "Bearer",
                    "expires_in": 86400,
                }
            )
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)
            return mock_resp

        with patch("aiohttp.ClientSession.post", return_value=create_mock_resp()):
            tokens = await asyncio.gather(
                broker._ensure_token(),
                broker._ensure_token(),
                broker._ensure_token(),
            )

        assert tokens == ["tok_refresh_once", "tok_refresh_once", "tok_refresh_once"]
        assert call_count[0] == 1

        await broker.close()

    @pytest.mark.asyncio
    async def test_token_refresh_cooldown_waits_then_retries(self, settings):
        """Token refresh should wait out cooldown then retry (issue #54)."""
        broker = KISBroker(settings)
        broker._refresh_cooldown = 0.1  # Short cooldown for testing

        # All attempts fail with 403 (EGW00133)
        mock_resp_403 = AsyncMock()
        mock_resp_403.status = 403
        mock_resp_403.text = AsyncMock(
            return_value=(
                '{"error_code":"EGW00133","error_description":'
                '"접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"}'
            )
        )
        mock_resp_403.__aenter__ = AsyncMock(return_value=mock_resp_403)
        mock_resp_403.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp_403):
            # First attempt should fail with 403
            with pytest.raises(ConnectionError, match="Token refresh failed"):
                await broker._ensure_token()

            # Second attempt within cooldown should wait then retry (and still get 403)
            with pytest.raises(ConnectionError, match="Token refresh failed"):
                await broker._ensure_token()

        await broker.close()

    @pytest.mark.asyncio
    async def test_token_refresh_allowed_after_cooldown(self, settings):
        """Token refresh should be allowed after cooldown period expires."""
        broker = KISBroker(settings)
        broker._refresh_cooldown = 0.1  # Very short cooldown for testing

        # First attempt fails
        mock_resp_403 = AsyncMock()
        mock_resp_403.status = 403
        mock_resp_403.text = AsyncMock(return_value='{"error_code":"EGW00133"}')
        mock_resp_403.__aenter__ = AsyncMock(return_value=mock_resp_403)
        mock_resp_403.__aexit__ = AsyncMock(return_value=False)

        # Second attempt succeeds
        mock_resp_200 = AsyncMock()
        mock_resp_200.status = 200
        mock_resp_200.json = AsyncMock(
            return_value={
                "access_token": "tok_after_cooldown",
                "expires_in": 86400,
            }
        )
        mock_resp_200.__aenter__ = AsyncMock(return_value=mock_resp_200)
        mock_resp_200.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp_403):
            with pytest.raises(ConnectionError, match="Token refresh failed"):
                await broker._ensure_token()

        # Wait for cooldown to expire
        await asyncio.sleep(0.15)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp_200):
            token = await broker._ensure_token()
            assert token == "tok_after_cooldown"

        await broker.close()


# ---------------------------------------------------------------------------
# Network Error Handling
# ---------------------------------------------------------------------------


class TestNetworkErrorHandling:
    """Broker must handle network timeouts and HTTP errors gracefully."""

    @pytest.mark.asyncio
    async def test_timeout_raises_connection_error(self, settings):
        broker = KISBroker(settings)
        broker._access_token = "tok"
        broker._token_expires_at = asyncio.get_event_loop().time() + 3600

        with patch(
            "aiohttp.ClientSession.get",
            side_effect=TimeoutError(),
        ):
            with pytest.raises(ConnectionError):
                await broker.get_orderbook("005930")

        await broker.close()

    @pytest.mark.asyncio
    async def test_http_500_raises_connection_error(self, settings):
        broker = KISBroker(settings)
        broker._access_token = "tok"
        broker._token_expires_at = asyncio.get_event_loop().time() + 3600

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.get", return_value=mock_resp):
            with pytest.raises(ConnectionError):
                await broker.get_orderbook("005930")

        await broker.close()


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    """The leaky bucket rate limiter must throttle requests."""

    @pytest.mark.asyncio
    async def test_rate_limiter_does_not_block_under_limit(self, settings):
        broker = KISBroker(settings)
        # Should complete without blocking when under limit
        await broker._rate_limiter.acquire()
        await broker.close()

    @pytest.mark.asyncio
    async def test_send_order_acquires_rate_limiter_twice(self, settings):
        """send_order must acquire rate limiter for both hash key and order call."""
        broker = KISBroker(settings)
        broker._access_token = "tok"
        broker._token_expires_at = asyncio.get_event_loop().time() + 3600

        # Mock hash key response
        mock_hash_resp = AsyncMock()
        mock_hash_resp.status = 200
        mock_hash_resp.json = AsyncMock(return_value={"HASH": "abc123"})
        mock_hash_resp.__aenter__ = AsyncMock(return_value=mock_hash_resp)
        mock_hash_resp.__aexit__ = AsyncMock(return_value=False)

        # Mock order response
        mock_order_resp = AsyncMock()
        mock_order_resp.status = 200
        mock_order_resp.json = AsyncMock(return_value={"rt_cd": "0"})
        mock_order_resp.__aenter__ = AsyncMock(return_value=mock_order_resp)
        mock_order_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash_resp, mock_order_resp]):
            with patch.object(
                broker._rate_limiter, "acquire", new_callable=AsyncMock
            ) as mock_acquire:
                await broker.send_order("005930", "BUY", 1, 50000)
                assert mock_acquire.call_count == 2

        await broker.close()


# ---------------------------------------------------------------------------
# Hash Key Generation
# ---------------------------------------------------------------------------


class TestHashKey:
    """POST requests to KIS require a hash key."""

    @pytest.mark.asyncio
    async def test_generates_hash_key_for_post_body(self, settings):
        broker = KISBroker(settings)
        broker._access_token = "tok"
        broker._token_expires_at = asyncio.get_event_loop().time() + 3600

        body = {"CANO": "12345678", "ACNT_PRDT_CD": "01"}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"HASH": "abc123hash"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            hash_key = await broker._get_hash_key(body)
            assert isinstance(hash_key, str)
            assert len(hash_key) > 0

        await broker.close()

    @pytest.mark.asyncio
    async def test_hash_key_acquires_rate_limiter(self, settings):
        """_get_hash_key must go through the rate limiter to prevent burst."""
        broker = KISBroker(settings)
        broker._access_token = "tok"
        broker._token_expires_at = asyncio.get_event_loop().time() + 3600

        body = {"CANO": "12345678", "ACNT_PRDT_CD": "01"}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"HASH": "abc123hash"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp):
            with patch.object(
                broker._rate_limiter, "acquire", new_callable=AsyncMock
            ) as mock_acquire:
                await broker._get_hash_key(body)
                mock_acquire.assert_called_once()

        await broker.close()


# ---------------------------------------------------------------------------
# fetch_market_rankings — TR_ID, path, params (issue #155)
# ---------------------------------------------------------------------------


def _make_ranking_mock(items: list[dict]) -> AsyncMock:
    """Build a mock HTTP response returning ranking items."""
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"output": items})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


class TestFetchMarketRankings:
    """Verify correct TR_ID, API path, and params per ranking_type (issue #155)."""

    @pytest.fixture
    def broker(self, settings) -> KISBroker:
        b = KISBroker(settings)
        b._access_token = "tok"
        b._token_expires_at = float("inf")
        b._rate_limiter.acquire = AsyncMock()
        return b

    @pytest.mark.asyncio
    async def test_volume_uses_correct_tr_id_and_path(self, broker: KISBroker) -> None:
        mock_resp = _make_ranking_mock([])
        with patch("aiohttp.ClientSession.get", return_value=mock_resp) as mock_get:
            await broker.fetch_market_rankings(ranking_type="volume")

        call_kwargs = mock_get.call_args
        url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        headers = call_kwargs[1].get("headers", {})
        params = call_kwargs[1].get("params", {})

        assert "volume-rank" in url
        assert headers.get("tr_id") == "FHPST01710000"
        assert params.get("FID_COND_SCR_DIV_CODE") == "20171"
        assert params.get("FID_TRGT_EXLS_CLS_CODE") == "0000000000"

    @pytest.mark.asyncio
    async def test_fluctuation_uses_correct_tr_id_and_path(self, broker: KISBroker) -> None:
        mock_resp = _make_ranking_mock([])
        with patch("aiohttp.ClientSession.get", return_value=mock_resp) as mock_get:
            await broker.fetch_market_rankings(ranking_type="fluctuation")

        call_kwargs = mock_get.call_args
        url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        headers = call_kwargs[1].get("headers", {})
        params = call_kwargs[1].get("params", {})

        assert "ranking/fluctuation" in url
        assert headers.get("tr_id") == "FHPST01700000"
        assert params.get("fid_cond_scr_div_code") == "20170"
        # 실전 API는 4자리("0000") 거부 — 1자리("0")여야 한다 (#240)
        assert params.get("fid_rank_sort_cls_code") == "0"

    @pytest.mark.asyncio
    async def test_volume_returns_parsed_rows(self, broker: KISBroker) -> None:
        items = [
            {
                "mksc_shrn_iscd": "005930",
                "hts_kor_isnm": "삼성전자",
                "stck_prpr": "75000",
                "acml_vol": "10000000",
                "prdy_ctrt": "2.5",
                "vol_inrt": "150",
            }
        ]
        mock_resp = _make_ranking_mock(items)
        with patch("aiohttp.ClientSession.get", return_value=mock_resp):
            result = await broker.fetch_market_rankings(ranking_type="volume")

        assert len(result) == 1
        assert result[0]["stock_code"] == "005930"
        assert result[0]["price"] == 75000.0
        assert result[0]["change_rate"] == 2.5

    @pytest.mark.asyncio
    async def test_fluctuation_parses_stck_shrn_iscd(self, broker: KISBroker) -> None:
        """실전 API는 mksc_shrn_iscd 대신 stck_shrn_iscd를 반환한다 (#240)."""
        items = [
            {
                "stck_shrn_iscd": "015260",
                "hts_kor_isnm": "에이엔피",
                "stck_prpr": "794",
                "acml_vol": "4896196",
                "prdy_ctrt": "29.74",
                "vol_inrt": "0",
            }
        ]
        mock_resp = _make_ranking_mock(items)
        with patch("aiohttp.ClientSession.get", return_value=mock_resp):
            result = await broker.fetch_market_rankings(ranking_type="fluctuation")

        assert len(result) == 1
        assert result[0]["stock_code"] == "015260"
        assert result[0]["change_rate"] == 29.74

    @pytest.mark.asyncio
    async def test_volume_uses_nx_market_code_in_nxt_session(self, broker: KISBroker) -> None:
        mock_resp = _make_ranking_mock([])
        with patch("aiohttp.ClientSession.get", return_value=mock_resp) as mock_get:
            await broker.fetch_market_rankings(ranking_type="volume", session_id="NXT_PRE")

        params = mock_get.call_args[1].get("params", {})
        assert params.get("FID_COND_MRKT_DIV_CODE") == "NX"


# ---------------------------------------------------------------------------
# KRX tick unit / round-down helpers (issue #157)
# ---------------------------------------------------------------------------


from src.broker.kis_api import kr_round_down, kr_tick_unit  # noqa: E402


class TestKrTickUnit:
    """kr_tick_unit and kr_round_down must implement KRX price tick rules."""

    @pytest.mark.parametrize(
        "price, expected_tick",
        [
            (1999, 1),
            (2000, 5),
            (4999, 5),
            (5000, 10),
            (19999, 10),
            (20000, 50),
            (49999, 50),
            (50000, 100),
            (199999, 100),
            (200000, 500),
            (499999, 500),
            (500000, 1000),
            (1000000, 1000),
        ],
    )
    def test_tick_unit_boundaries(self, price: int, expected_tick: int) -> None:
        assert kr_tick_unit(price) == expected_tick

    @pytest.mark.parametrize(
        "price, expected_rounded",
        [
            (188150, 188100),  # 100원 단위, 50원 잔여 → 내림
            (188100, 188100),  # 이미 정렬됨
            (75050, 75000),  # 100원 단위, 50원 잔여 → 내림
            (49950, 49950),  # 50원 단위 정렬됨
            (49960, 49950),  # 50원 단위, 10원 잔여 → 내림
            (1999, 1999),  # 1원 단위 → 그대로
            (5003, 5000),  # 10원 단위, 3원 잔여 → 내림
        ],
    )
    def test_round_down_to_tick(self, price: int, expected_rounded: int) -> None:
        assert kr_round_down(price) == expected_rounded


# ---------------------------------------------------------------------------
# get_current_price (issue #157)
# ---------------------------------------------------------------------------


class TestGetCurrentPrice:
    """get_current_price must use inquire-price API and return (price, change, foreigner)."""

    @pytest.fixture
    def broker(self, settings) -> KISBroker:
        b = KISBroker(settings)
        b._access_token = "tok"
        b._token_expires_at = float("inf")
        b._rate_limiter.acquire = AsyncMock()
        return b

    @pytest.mark.asyncio
    async def test_returns_correct_fields(self, broker: KISBroker) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "rt_cd": "0",
                "output": {
                    "stck_prpr": "188600",
                    "prdy_ctrt": "3.97",
                    "frgn_ntby_qty": "12345",
                },
            }
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.get", return_value=mock_resp) as mock_get:
            price, change_pct, foreigner = await broker.get_current_price("005930")

        assert price == 188600.0
        assert change_pct == 3.97
        assert foreigner == 12345.0

        call_kwargs = mock_get.call_args
        url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        headers = call_kwargs[1].get("headers", {})
        params = call_kwargs[1].get("params", {})
        assert "inquire-price" in url
        assert headers.get("tr_id") == "FHKST01010100"
        assert params.get("FID_COND_MRKT_DIV_CODE") == "J"

    @pytest.mark.asyncio
    async def test_market_div_code_can_be_overridden(self, broker: KISBroker) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "rt_cd": "0",
                "output": {
                    "stck_prpr": "56500",
                    "prdy_ctrt": "0.00",
                    "frgn_ntby_qty": "0",
                },
            }
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.get", return_value=mock_resp) as mock_get:
            await broker.get_current_price("006800", market_div_code="NX")

        params = mock_get.call_args[1].get("params", {})
        assert params.get("FID_COND_MRKT_DIV_CODE") == "NX"

    @pytest.mark.asyncio
    async def test_get_current_price_with_output_returns_raw_quote_payload(
        self, broker: KISBroker
    ) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "rt_cd": "0",
                "output": {
                    "stck_prpr": "188600",
                    "prdy_ctrt": "3.97",
                    "frgn_ntby_qty": "12345",
                    "stck_hgpr": "189500",
                },
            }
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.get", return_value=mock_resp):
            price, change_pct, foreigner, output = await broker.get_current_price_with_output(
                "005930"
            )

        assert price == 188600.0
        assert change_pct == 3.97
        assert foreigner == 12345.0
        assert output["stck_hgpr"] == "189500"

    @pytest.mark.asyncio
    async def test_http_error_raises_connection_error(self, broker: KISBroker) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.get", return_value=mock_resp):
            with pytest.raises(ConnectionError, match="get_current_price failed"):
                await broker.get_current_price("005930")


# ---------------------------------------------------------------------------
# send_order tick rounding and ORD_DVSN (issue #157)
# ---------------------------------------------------------------------------


class TestSendOrderTickRounding:
    """send_order must apply KRX tick rounding and correct ORD_DVSN codes."""

    @pytest.fixture
    def broker(self, settings) -> KISBroker:
        b = KISBroker(settings)
        b._access_token = "tok"
        b._token_expires_at = float("inf")
        b._rate_limiter.acquire = AsyncMock()
        return b

    @pytest.mark.asyncio
    async def test_limit_order_rounds_down_to_tick(self, broker: KISBroker) -> None:
        """Price 188150 (not on 100-won tick) must be rounded to 188100."""
        mock_hash = AsyncMock()
        mock_hash.status = 200
        mock_hash.json = AsyncMock(return_value={"HASH": "h"})
        mock_hash.__aenter__ = AsyncMock(return_value=mock_hash)
        mock_hash.__aexit__ = AsyncMock(return_value=False)

        mock_order = AsyncMock()
        mock_order.status = 200
        mock_order.json = AsyncMock(return_value={"rt_cd": "0"})
        mock_order.__aenter__ = AsyncMock(return_value=mock_order)
        mock_order.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.send_order("005930", "BUY", 1, price=188150)

        order_call = mock_post.call_args_list[1]
        body = order_call[1].get("json", {})
        assert body["ORD_UNPR"] == "188100"  # rounded down
        assert body["ORD_DVSN"] == "00"  # 지정가

    @pytest.mark.asyncio
    async def test_limit_order_ord_dvsn_is_00(self, broker: KISBroker) -> None:
        """send_order with price>0 must use ORD_DVSN='00' (지정가)."""
        mock_hash = AsyncMock()
        mock_hash.status = 200
        mock_hash.json = AsyncMock(return_value={"HASH": "h"})
        mock_hash.__aenter__ = AsyncMock(return_value=mock_hash)
        mock_hash.__aexit__ = AsyncMock(return_value=False)

        mock_order = AsyncMock()
        mock_order.status = 200
        mock_order.json = AsyncMock(return_value={"rt_cd": "0"})
        mock_order.__aenter__ = AsyncMock(return_value=mock_order)
        mock_order.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.send_order("005930", "BUY", 1, price=50000)

        order_call = mock_post.call_args_list[1]
        body = order_call[1].get("json", {})
        assert body["ORD_DVSN"] == "00"

    @pytest.mark.asyncio
    async def test_market_order_ord_dvsn_is_01(self, broker: KISBroker) -> None:
        """send_order with price=0 must use ORD_DVSN='01' (시장가)."""
        mock_hash = AsyncMock()
        mock_hash.status = 200
        mock_hash.json = AsyncMock(return_value={"HASH": "h"})
        mock_hash.__aenter__ = AsyncMock(return_value=mock_hash)
        mock_hash.__aexit__ = AsyncMock(return_value=False)

        mock_order = AsyncMock()
        mock_order.status = 200
        mock_order.json = AsyncMock(return_value={"rt_cd": "0"})
        mock_order.__aenter__ = AsyncMock(return_value=mock_order)
        mock_order.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.send_order("005930", "SELL", 1, price=0)

        order_call = mock_post.call_args_list[1]
        body = order_call[1].get("json", {})
        assert body["ORD_DVSN"] == "01"

    @pytest.mark.asyncio
    async def test_send_order_sets_exchange_field_from_session(self, broker: KISBroker) -> None:
        mock_hash = AsyncMock()
        mock_hash.status = 200
        mock_hash.json = AsyncMock(return_value={"HASH": "h"})
        mock_hash.__aenter__ = AsyncMock(return_value=mock_hash)
        mock_hash.__aexit__ = AsyncMock(return_value=False)

        mock_order = AsyncMock()
        mock_order.status = 200
        mock_order.json = AsyncMock(return_value={"rt_cd": "0"})
        mock_order.__aenter__ = AsyncMock(return_value=mock_order)
        mock_order.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            with patch.object(
                broker,
                "_load_dual_listing_metrics",
                new=AsyncMock(return_value=(False, None, None, None, None)),
            ):
                await broker.send_order("005930", "BUY", 1, price=50000, session_id="NXT_PRE")

        order_call = mock_post.call_args_list[1]
        body = order_call[1].get("json", {})
        assert body["EXCG_ID_DVSN_CD"] == "NXT"

    @pytest.mark.asyncio
    async def test_send_order_prefers_nxt_when_dual_listing_spread_is_tighter(
        self, broker: KISBroker
    ) -> None:
        mock_hash = AsyncMock()
        mock_hash.status = 200
        mock_hash.json = AsyncMock(return_value={"HASH": "h"})
        mock_hash.__aenter__ = AsyncMock(return_value=mock_hash)
        mock_hash.__aexit__ = AsyncMock(return_value=False)

        mock_order = AsyncMock()
        mock_order.status = 200
        mock_order.json = AsyncMock(return_value={"rt_cd": "0"})
        mock_order.__aenter__ = AsyncMock(return_value=mock_order)
        mock_order.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            with patch.object(
                broker,
                "_load_dual_listing_metrics",
                new=AsyncMock(return_value=(True, 0.004, 0.002, 100000.0, 90000.0)),
            ):
                await broker.send_order("005930", "BUY", 1, price=50000, session_id="KRX_REG")

        order_call = mock_post.call_args_list[1]
        body = order_call[1].get("json", {})
        assert body["EXCG_ID_DVSN_CD"] == "NXT"


# ---------------------------------------------------------------------------
# TR_ID live/paper branching (issues #201, #202, #203)
# ---------------------------------------------------------------------------


class TestTRIDBranchingDomestic:
    """get_balance and send_order must use correct TR_ID for live vs paper mode."""

    def _make_broker(self, settings, mode: str) -> KISBroker:
        from src.config import Settings

        s = Settings(
            KIS_APP_KEY=settings.KIS_APP_KEY,
            KIS_APP_SECRET=settings.KIS_APP_SECRET,
            KIS_ACCOUNT_NO=settings.KIS_ACCOUNT_NO,
            GEMINI_API_KEY=settings.GEMINI_API_KEY,
            DB_PATH=":memory:",
            ENABLED_MARKETS="KR",
            MODE=mode,
        )
        b = KISBroker(s)
        b._access_token = "tok"
        b._token_expires_at = float("inf")
        b._rate_limiter.acquire = AsyncMock()
        return b

    @pytest.mark.asyncio
    async def test_get_balance_paper_uses_vttc8434r(self, settings) -> None:
        broker = self._make_broker(settings, "paper")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output1": [], "output2": {}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.get", return_value=mock_resp) as mock_get:
            await broker.get_balance()

        headers = mock_get.call_args[1].get("headers", {})
        assert headers["tr_id"] == "VTTC8434R"

    @pytest.mark.asyncio
    async def test_get_balance_live_uses_tttc8434r(self, settings) -> None:
        broker = self._make_broker(settings, "live")
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output1": [], "output2": {}})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.get", return_value=mock_resp) as mock_get:
            await broker.get_balance()

        headers = mock_get.call_args[1].get("headers", {})
        assert headers["tr_id"] == "TTTC8434R"

    @pytest.mark.asyncio
    async def test_send_order_buy_paper_uses_vttc0012u(self, settings) -> None:
        broker = self._make_broker(settings, "paper")
        mock_hash = AsyncMock()
        mock_hash.status = 200
        mock_hash.json = AsyncMock(return_value={"HASH": "h"})
        mock_hash.__aenter__ = AsyncMock(return_value=mock_hash)
        mock_hash.__aexit__ = AsyncMock(return_value=False)

        mock_order = AsyncMock()
        mock_order.status = 200
        mock_order.json = AsyncMock(return_value={"rt_cd": "0"})
        mock_order.__aenter__ = AsyncMock(return_value=mock_order)
        mock_order.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.send_order("005930", "BUY", 1)

        order_headers = mock_post.call_args_list[1][1].get("headers", {})
        assert order_headers["tr_id"] == "VTTC0012U"

    @pytest.mark.asyncio
    async def test_send_order_buy_live_uses_tttc0012u(self, settings) -> None:
        broker = self._make_broker(settings, "live")
        mock_hash = AsyncMock()
        mock_hash.status = 200
        mock_hash.json = AsyncMock(return_value={"HASH": "h"})
        mock_hash.__aenter__ = AsyncMock(return_value=mock_hash)
        mock_hash.__aexit__ = AsyncMock(return_value=False)

        mock_order = AsyncMock()
        mock_order.status = 200
        mock_order.json = AsyncMock(return_value={"rt_cd": "0"})
        mock_order.__aenter__ = AsyncMock(return_value=mock_order)
        mock_order.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.send_order("005930", "BUY", 1)

        order_headers = mock_post.call_args_list[1][1].get("headers", {})
        assert order_headers["tr_id"] == "TTTC0012U"

    @pytest.mark.asyncio
    async def test_send_order_sell_paper_uses_vttc0011u(self, settings) -> None:
        broker = self._make_broker(settings, "paper")
        mock_hash = AsyncMock()
        mock_hash.status = 200
        mock_hash.json = AsyncMock(return_value={"HASH": "h"})
        mock_hash.__aenter__ = AsyncMock(return_value=mock_hash)
        mock_hash.__aexit__ = AsyncMock(return_value=False)

        mock_order = AsyncMock()
        mock_order.status = 200
        mock_order.json = AsyncMock(return_value={"rt_cd": "0"})
        mock_order.__aenter__ = AsyncMock(return_value=mock_order)
        mock_order.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.send_order("005930", "SELL", 1)

        order_headers = mock_post.call_args_list[1][1].get("headers", {})
        assert order_headers["tr_id"] == "VTTC0011U"

    @pytest.mark.asyncio
    async def test_send_order_sell_live_uses_tttc0011u(self, settings) -> None:
        broker = self._make_broker(settings, "live")
        mock_hash = AsyncMock()
        mock_hash.status = 200
        mock_hash.json = AsyncMock(return_value={"HASH": "h"})
        mock_hash.__aenter__ = AsyncMock(return_value=mock_hash)
        mock_hash.__aexit__ = AsyncMock(return_value=False)

        mock_order = AsyncMock()
        mock_order.status = 200
        mock_order.json = AsyncMock(return_value={"rt_cd": "0"})
        mock_order.__aenter__ = AsyncMock(return_value=mock_order)
        mock_order.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.send_order("005930", "SELL", 1)

        order_headers = mock_post.call_args_list[1][1].get("headers", {})
        assert order_headers["tr_id"] == "TTTC0011U"


# ---------------------------------------------------------------------------
# Domestic Pending Orders (get_domestic_pending_orders)
# ---------------------------------------------------------------------------


class TestGetDomesticPendingOrders:
    """get_domestic_pending_orders must return [] in paper mode and call TTTC0084R in live."""

    def _make_broker(self, settings, mode: str) -> KISBroker:
        from src.config import Settings

        s = Settings(
            KIS_APP_KEY=settings.KIS_APP_KEY,
            KIS_APP_SECRET=settings.KIS_APP_SECRET,
            KIS_ACCOUNT_NO=settings.KIS_ACCOUNT_NO,
            GEMINI_API_KEY=settings.GEMINI_API_KEY,
            DB_PATH=":memory:",
            ENABLED_MARKETS="KR",
            MODE=mode,
        )
        b = KISBroker(s)
        b._access_token = "tok"
        b._token_expires_at = float("inf")
        b._rate_limiter.acquire = AsyncMock()
        return b

    @pytest.mark.asyncio
    async def test_paper_mode_returns_empty(self, settings) -> None:
        """Paper mode must return [] immediately without any API call."""
        broker = self._make_broker(settings, "paper")

        with patch("aiohttp.ClientSession.get") as mock_get:
            result = await broker.get_domestic_pending_orders()

        assert result == []
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_live_mode_calls_tttc0084r_with_correct_params(self, settings) -> None:
        """Live mode must call TTTC0084R with INQR_DVSN_1/2 and paging params."""
        broker = self._make_broker(settings, "live")
        pending = [{"odno": "001", "pdno": "005930", "psbl_qty": "10"}]
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": pending})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.get", return_value=mock_resp) as mock_get:
            result = await broker.get_domestic_pending_orders()

        assert len(result) == 1
        assert result[0]["odno"] == "001"
        assert result[0]["order_exchange"] == "KRX"
        headers = mock_get.call_args[1].get("headers", {})
        assert headers["tr_id"] == "TTTC0084R"
        params = mock_get.call_args[1].get("params", {})
        assert params["INQR_DVSN_1"] == "0"
        assert params["INQR_DVSN_2"] == "0"

    @pytest.mark.asyncio
    async def test_live_mode_normalizes_pending_exchange_code(self, settings) -> None:
        """Pending orders should include normalized order_exchange (KRX/NXT)."""
        broker = self._make_broker(settings, "live")
        pending = [
            {"odno": "001", "pdno": "005930", "psbl_qty": "10", "excg_dvsn_cd": "NXT"},
            {"odno": "002", "pdno": "000660", "psbl_qty": "3"},
        ]
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": pending})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.get", return_value=mock_resp):
            result = await broker.get_domestic_pending_orders()

        assert result[0]["order_exchange"] == "NXT"
        assert result[1]["order_exchange"] == "KRX"

    @pytest.mark.asyncio
    async def test_live_mode_logs_warning_for_unknown_pending_exchange_code(self, settings) -> None:
        """Unknown pending exchange codes should warn and default to KRX."""
        broker = self._make_broker(settings, "live")
        pending = [{"odno": "001", "pdno": "005930", "psbl_qty": "10", "excg_dvsn_cd": "ZZ"}]
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": pending})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("aiohttp.ClientSession.get", return_value=mock_resp),
            patch("src.broker.kis_api.logger.warning") as mock_warning,
        ):
            result = await broker.get_domestic_pending_orders()

        assert result[0]["order_exchange"] == "KRX"
        mock_warning.assert_called_once()
        assert "Unknown domestic exchange code" in mock_warning.call_args[0][0]

    @pytest.mark.asyncio
    async def test_live_mode_connection_error(self, settings) -> None:
        """Network error must raise ConnectionError."""
        import aiohttp as _aiohttp

        broker = self._make_broker(settings, "live")

        with patch(
            "aiohttp.ClientSession.get",
            side_effect=_aiohttp.ClientError("timeout"),
        ):
            with pytest.raises(ConnectionError):
                await broker.get_domestic_pending_orders()


# ---------------------------------------------------------------------------
# Domestic Order Cancellation (cancel_domestic_order)
# ---------------------------------------------------------------------------


class TestCancelDomesticOrder:
    """cancel_domestic_order must use correct TR_ID and build body correctly."""

    def _make_broker(self, settings, mode: str) -> KISBroker:
        from src.config import Settings

        s = Settings(
            KIS_APP_KEY=settings.KIS_APP_KEY,
            KIS_APP_SECRET=settings.KIS_APP_SECRET,
            KIS_ACCOUNT_NO=settings.KIS_ACCOUNT_NO,
            GEMINI_API_KEY=settings.GEMINI_API_KEY,
            DB_PATH=":memory:",
            ENABLED_MARKETS="KR",
            MODE=mode,
        )
        b = KISBroker(s)
        b._access_token = "tok"
        b._token_expires_at = float("inf")
        b._rate_limiter.acquire = AsyncMock()
        return b

    def _make_post_mocks(self, order_payload: dict) -> tuple:
        mock_hash = AsyncMock()
        mock_hash.status = 200
        mock_hash.json = AsyncMock(return_value={"HASH": "h"})
        mock_hash.__aenter__ = AsyncMock(return_value=mock_hash)
        mock_hash.__aexit__ = AsyncMock(return_value=False)

        mock_order = AsyncMock()
        mock_order.status = 200
        mock_order.json = AsyncMock(return_value=order_payload)
        mock_order.__aenter__ = AsyncMock(return_value=mock_order)
        mock_order.__aexit__ = AsyncMock(return_value=False)

        return mock_hash, mock_order

    @pytest.mark.asyncio
    async def test_live_uses_tttc0013u(self, settings) -> None:
        """Live mode must use TR_ID TTTC0013U."""
        broker = self._make_broker(settings, "live")
        mock_hash, mock_order = self._make_post_mocks({"rt_cd": "0"})

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.cancel_domestic_order("005930", "ORD001", "BRNO01", 5)

        order_headers = mock_post.call_args_list[1][1].get("headers", {})
        assert order_headers["tr_id"] == "TTTC0013U"

    @pytest.mark.asyncio
    async def test_paper_uses_vttc0013u(self, settings) -> None:
        """Paper mode must use TR_ID VTTC0013U."""
        broker = self._make_broker(settings, "paper")
        mock_hash, mock_order = self._make_post_mocks({"rt_cd": "0"})

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.cancel_domestic_order("005930", "ORD001", "BRNO01", 5)

        order_headers = mock_post.call_args_list[1][1].get("headers", {})
        assert order_headers["tr_id"] == "VTTC0013U"

    @pytest.mark.asyncio
    async def test_cancel_sets_rvse_cncl_dvsn_cd_02(self, settings) -> None:
        """Body must have RVSE_CNCL_DVSN_CD='02' (취소) and QTY_ALL_ORD_YN='Y'."""
        broker = self._make_broker(settings, "live")
        mock_hash, mock_order = self._make_post_mocks({"rt_cd": "0"})

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.cancel_domestic_order("005930", "ORD001", "BRNO01", 5)

        body = mock_post.call_args_list[1][1].get("json", {})
        assert body["RVSE_CNCL_DVSN_CD"] == "02"
        assert body["QTY_ALL_ORD_YN"] == "Y"
        assert body["ORD_UNPR"] == "0"

    @pytest.mark.asyncio
    async def test_cancel_sets_krx_fwdg_ord_orgno_in_body(self, settings) -> None:
        """Body must include KRX_FWDG_ORD_ORGNO and ORGN_ODNO from arguments."""
        broker = self._make_broker(settings, "live")
        mock_hash, mock_order = self._make_post_mocks({"rt_cd": "0"})

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.cancel_domestic_order("005930", "ORD123", "BRN456", 3)

        body = mock_post.call_args_list[1][1].get("json", {})
        assert body["KRX_FWDG_ORD_ORGNO"] == "BRN456"
        assert body["ORGN_ODNO"] == "ORD123"
        assert body["ORD_QTY"] == "3"

    @pytest.mark.asyncio
    async def test_cancel_sets_exchange_code_in_body(self, settings) -> None:
        """Cancel body must include explicit EXCG_ID_DVSN_CD."""
        broker = self._make_broker(settings, "live")
        mock_hash, mock_order = self._make_post_mocks({"rt_cd": "0"})

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.cancel_domestic_order(
                "005930",
                "ORD123",
                "BRN456",
                3,
                order_exchange="NXT",
            )

        body = mock_post.call_args_list[1][1].get("json", {})
        assert body["EXCG_ID_DVSN_CD"] == "NXT"

    @pytest.mark.asyncio
    async def test_cancel_sets_hashkey_header(self, settings) -> None:
        """Request must include hashkey header (same pattern as send_order)."""
        broker = self._make_broker(settings, "live")
        mock_hash, mock_order = self._make_post_mocks({"rt_cd": "0"})

        with patch("aiohttp.ClientSession.post", side_effect=[mock_hash, mock_order]) as mock_post:
            await broker.cancel_domestic_order("005930", "ORD001", "BRNO01", 2)

        order_headers = mock_post.call_args_list[1][1].get("headers", {})
        assert "hashkey" in order_headers
        assert order_headers["hashkey"] == "h"
