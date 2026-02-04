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
    async def test_reuses_cached_token(self, settings):
        broker = KISBroker(settings)
        broker._access_token = "cached_token"
        broker._token_expires_at = asyncio.get_event_loop().time() + 3600

        token = await broker._ensure_token()
        assert token == "cached_token"

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
    async def test_token_refresh_cooldown_prevents_rapid_retries(self, settings):
        """Token refresh should enforce cooldown after failure (issue #54)."""
        broker = KISBroker(settings)
        broker._refresh_cooldown = 2.0  # Short cooldown for testing

        # First refresh attempt fails with 403 (EGW00133)
        mock_resp_403 = AsyncMock()
        mock_resp_403.status = 403
        mock_resp_403.text = AsyncMock(
            return_value='{"error_code":"EGW00133","error_description":"접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"}'
        )
        mock_resp_403.__aenter__ = AsyncMock(return_value=mock_resp_403)
        mock_resp_403.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession.post", return_value=mock_resp_403):
            # First attempt should fail with 403
            with pytest.raises(ConnectionError, match="Token refresh failed"):
                await broker._ensure_token()

            # Second attempt within cooldown should fail with cooldown error
            with pytest.raises(ConnectionError, match="Token refresh on cooldown"):
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

        with patch(
            "aiohttp.ClientSession.post", side_effect=[mock_hash_resp, mock_order_resp]
        ):
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
