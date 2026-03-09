"""Realtime hard-stop monitor state for websocket-driven exits."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class TrackedHardStop:
    market_code: str
    stock_code: str
    entry_price: float
    quantity: int
    hard_stop_pct: float
    hard_stop_price: float
    decision_id: str
    position_timestamp: str
    in_flight: bool = False


@dataclass(frozen=True, slots=True)
class HardStopTrigger:
    market_code: str
    stock_code: str
    last_price: float
    hard_stop_price: float
    quantity: int
    decision_id: str
    position_timestamp: str


class RealtimeHardStopMonitor:
    """Track realtime hard-stop thresholds and deduplicate triggers."""

    def __init__(self) -> None:
        self._tracked: dict[tuple[str, str], TrackedHardStop] = {}

    def register(
        self,
        *,
        market_code: str,
        stock_code: str,
        entry_price: float,
        quantity: int,
        hard_stop_pct: float,
        decision_id: str,
        position_timestamp: str,
    ) -> TrackedHardStop:
        existing = self._tracked.get((market_code, stock_code))
        hard_stop_price = round(entry_price * (1.0 + (hard_stop_pct / 100.0)), 4)
        tracked = TrackedHardStop(
            market_code=market_code,
            stock_code=stock_code,
            entry_price=entry_price,
            quantity=quantity,
            hard_stop_pct=hard_stop_pct,
            hard_stop_price=hard_stop_price,
            decision_id=decision_id,
            position_timestamp=position_timestamp,
            in_flight=existing.in_flight if existing is not None else False,
        )
        self._tracked[(market_code, stock_code)] = tracked
        return tracked

    def get(self, market_code: str, stock_code: str) -> TrackedHardStop | None:
        return self._tracked.get((market_code, stock_code))

    def remove(self, market_code: str, stock_code: str) -> None:
        self._tracked.pop((market_code, stock_code), None)

    def tracked_symbols(self) -> set[str]:
        return {stock_code for (_, stock_code) in self._tracked}

    def release_in_flight(self, market_code: str, stock_code: str) -> None:
        key = (market_code, stock_code)
        tracked = self._tracked.get(key)
        if tracked is None or not tracked.in_flight:
            return
        self._tracked[key] = replace(tracked, in_flight=False)

    def evaluate_price(
        self,
        market_code: str,
        stock_code: str,
        last_price: float,
    ) -> HardStopTrigger | None:
        key = (market_code, stock_code)
        tracked = self._tracked.get(key)
        if tracked is None or tracked.in_flight:
            return None
        if last_price > tracked.hard_stop_price:
            return None

        self._tracked[key] = replace(tracked, in_flight=True)
        return HardStopTrigger(
            market_code=market_code,
            stock_code=stock_code,
            last_price=last_price,
            hard_stop_price=tracked.hard_stop_price,
            quantity=tracked.quantity,
            decision_id=tracked.decision_id,
            position_timestamp=tracked.position_timestamp,
        )
