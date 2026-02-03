"""TDD tests for broker/kis_api.py — written BEFORE implementation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
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
            side_effect=asyncio.TimeoutError(),
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
