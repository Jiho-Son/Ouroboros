from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExchangeResolution:
    exchange_code: str
    reason: str


class KRExchangeRouter:
    """Resolve domestic exchange routing for KR sessions."""

    def resolve_for_ranking(self, session_id: str) -> str:
        if session_id in {"NXT_PRE", "NXT_AFTER"}:
            return "NX"
        return "J"

    def resolve_for_order(
        self,
        *,
        stock_code: str,
        session_id: str,
        is_dual_listed: bool = False,
        spread_krx: float | None = None,
        spread_nxt: float | None = None,
        liquidity_krx: float | None = None,
        liquidity_nxt: float | None = None,
    ) -> ExchangeResolution:
        del stock_code
        default_exchange = "NXT" if session_id in {"NXT_PRE", "NXT_AFTER"} else "KRX"
        default_reason = "session_default"

        if not is_dual_listed:
            return ExchangeResolution(default_exchange, default_reason)

        if spread_krx is not None and spread_nxt is not None:
            if spread_nxt < spread_krx:
                return ExchangeResolution("NXT", "dual_listing_spread")
            return ExchangeResolution("KRX", "dual_listing_spread")

        if liquidity_krx is not None and liquidity_nxt is not None:
            if liquidity_nxt > liquidity_krx:
                return ExchangeResolution("NXT", "dual_listing_liquidity")
            return ExchangeResolution("KRX", "dual_listing_liquidity")

        return ExchangeResolution(default_exchange, "fallback_data_unavailable")
