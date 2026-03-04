from __future__ import annotations

from src.broker.kr_exchange_router import KRExchangeRouter


def test_ranking_market_code_by_session() -> None:
    router = KRExchangeRouter()
    assert router.resolve_for_ranking("KRX_REG") == "J"
    assert router.resolve_for_ranking("NXT_PRE") == "NX"
    assert router.resolve_for_ranking("NXT_AFTER") == "NX"


def test_order_exchange_falls_back_to_session_default_on_missing_data() -> None:
    router = KRExchangeRouter()
    resolved = router.resolve_for_order(
        stock_code="0001A0",
        session_id="NXT_PRE",
        is_dual_listed=True,
        spread_krx=None,
        spread_nxt=None,
        liquidity_krx=None,
        liquidity_nxt=None,
    )
    assert resolved.exchange_code == "NXT"
    assert resolved.reason == "fallback_data_unavailable"


def test_order_exchange_uses_spread_preference_for_dual_listing() -> None:
    router = KRExchangeRouter()
    resolved = router.resolve_for_order(
        stock_code="0001A0",
        session_id="KRX_REG",
        is_dual_listed=True,
        spread_krx=0.005,
        spread_nxt=0.003,
        liquidity_krx=100000.0,
        liquidity_nxt=90000.0,
    )
    assert resolved.exchange_code == "NXT"
    assert resolved.reason == "dual_listing_spread"
