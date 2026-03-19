from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.broker.orderbook_utils import extract_orderbook_top_levels
from src.broker.overseas import OverseasBroker
from src.broker.pending_orders import (
    _fetch_optional_orderbook_top_levels,
    _fetch_optional_quote_payload,
)


class TestFetchOptionalQuotePayload:
    @pytest.mark.asyncio
    async def test_returns_empty_when_attribute_exists_but_is_not_callable(self) -> None:
        broker = SimpleNamespace(get_orderbook_by_market={"unexpected": "mapping"})

        payload = await _fetch_optional_quote_payload(
            obj=broker,
            method_name="get_orderbook_by_market",
            kwargs={"stock_code": "005930", "market_div_code": "J"},
        )

        assert payload == {}

    @pytest.mark.asyncio
    async def test_requires_async_quote_method(self) -> None:
        broker = SimpleNamespace(
            get_orderbook_by_market=lambda **kwargs: {
                "output1": {"stck_askp1": "50300", "stck_bidp1": "49900"}
            }
        )

        with pytest.raises(TypeError):
            await _fetch_optional_quote_payload(
                obj=broker,
                method_name="get_orderbook_by_market",
                kwargs={"stock_code": "005930", "market_div_code": "J"},
            )


class TestFetchOptionalOrderbookTopLevels:
    @pytest.mark.asyncio
    async def test_returns_none_when_quote_fetch_raises(self) -> None:
        async def _raise(**kwargs: object) -> dict[str, object]:
            raise RuntimeError("network error")

        broker = SimpleNamespace(get_orderbook_by_market=_raise)

        ask, bid = await _fetch_optional_orderbook_top_levels(
            obj=broker,
            method_name="get_orderbook_by_market",
            kwargs={"stock_code": "005930", "market_div_code": "J"},
            log_context="test",
        )

        assert ask is None
        assert bid is None

    @pytest.mark.asyncio
    async def test_returns_none_when_quote_method_is_sync_only(self) -> None:
        broker = SimpleNamespace(
            get_orderbook_by_market=lambda **kwargs: {
                "output1": {"stck_askp1": "50300", "stck_bidp1": "49900"}
            }
        )

        ask, bid = await _fetch_optional_orderbook_top_levels(
            obj=broker,
            method_name="get_orderbook_by_market",
            kwargs={"stock_code": "005930", "market_div_code": "J"},
            log_context="test",
        )

        assert ask is None
        assert bid is None


class TestSharedTopLevelExtraction:
    def test_overseas_wrapper_accepts_domestic_and_overseas_aliases(self) -> None:
        domestic_payload = {
            "output1": {"stck_askp1": "50300", "stck_bidp1": "49900"},
        }
        overseas_payload = {
            "output2": {"pask1": "201.5", "pbid1": "200.8"},
        }

        assert OverseasBroker._extract_orderbook_top_levels(domestic_payload) == (
            50300.0,
            49900.0,
        )
        assert OverseasBroker._extract_orderbook_top_levels(overseas_payload) == (
            201.5,
            200.8,
        )

    def test_overseas_wrapper_uses_shared_helper_container_order(self) -> None:
        payload = {
            "output1": {"pask1": "999.9", "pbid1": "998.8"},
            "output2": {"pask1": "201.5", "pbid1": "200.8"},
        }

        assert OverseasBroker._extract_orderbook_top_levels(payload) == (
            999.9,
            998.8,
        )

    def test_shared_helper_accepts_output_container_shape(self) -> None:
        payload = {
            "output": {"pask1": "101.5", "pbid1": "101.1"},
        }

        assert extract_orderbook_top_levels(payload) == (
            101.5,
            101.1,
        )
