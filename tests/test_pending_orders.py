from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.broker.overseas import OverseasBroker
from src.broker.pending_orders import _fetch_optional_quote_payload


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
