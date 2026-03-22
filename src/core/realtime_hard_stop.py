"""Realtime hard-stop monitor state for websocket-driven exits."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

logger = logging.getLogger(__name__)


def _is_us_market_code(market_code: str) -> bool:
    return market_code.startswith("US_")


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
    stock_name: str = ""
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
    stock_name: str = ""


@dataclass(frozen=True, slots=True)
class HardStopEvaluation:
    reason: str
    trigger: HardStopTrigger | None = None


class RealtimeHardStopMonitor:
    """Track realtime hard-stop thresholds and deduplicate triggers."""

    def __init__(self) -> None:
        self._tracked: dict[tuple[str, str], TrackedHardStop] = {}

    def register(
        self,
        *,
        market_code: str,
        stock_code: str,
        stock_name: str = "",
        entry_price: float,
        quantity: int,
        hard_stop_pct: float,
        decision_id: str,
        position_timestamp: str,
    ) -> TrackedHardStop:
        existing = self._tracked.get((market_code, stock_code))
        hard_stop_price = round(entry_price * (1.0 + (hard_stop_pct / 100.0)), 4)
        normalized_name = stock_name.strip()
        if not normalized_name and existing is not None:
            normalized_name = existing.stock_name
        tracked = TrackedHardStop(
            market_code=market_code,
            stock_code=stock_code,
            entry_price=entry_price,
            quantity=quantity,
            hard_stop_pct=hard_stop_pct,
            hard_stop_price=hard_stop_price,
            decision_id=decision_id,
            position_timestamp=position_timestamp,
            stock_name=normalized_name,
            in_flight=existing.in_flight if existing is not None else False,
        )
        self._tracked[(market_code, stock_code)] = tracked
        logger.info(
            "Registered realtime hard-stop market=%s stock=%s quantity=%d hard_stop_price=%.4f",
            market_code,
            stock_code,
            quantity,
            hard_stop_price,
        )
        return tracked

    def get(self, market_code: str, stock_code: str) -> TrackedHardStop | None:
        return self._tracked.get((market_code, stock_code))

    def remove(self, market_code: str, stock_code: str) -> None:
        removed = self._tracked.pop((market_code, stock_code), None)
        if removed is not None:
            logger.info(
                "Removed realtime hard-stop market=%s stock=%s",
                market_code,
                stock_code,
            )

    def tracked_symbols(self) -> set[str]:
        return {stock_code for (_, stock_code) in self._tracked}

    def release_in_flight(self, market_code: str, stock_code: str) -> None:
        key = (market_code, stock_code)
        tracked = self._tracked.get(key)
        if tracked is None or not tracked.in_flight:
            return
        self._tracked[key] = replace(tracked, in_flight=False)
        logger.info(
            "Released realtime hard-stop in-flight state market=%s stock=%s",
            market_code,
            stock_code,
        )

    def evaluate_price_diagnostic(
        self,
        market_code: str,
        stock_code: str,
        last_price: float,
    ) -> HardStopEvaluation:
        key = (market_code, stock_code)
        tracked = self._tracked.get(key)
        is_us_market = _is_us_market_code(market_code)
        if is_us_market:
            if tracked is None:
                logger.info(
                    "Realtime hard-stop evaluate action=enter "
                    "market=%s stock=%s last_price=%.4f tracked=no",
                    market_code,
                    stock_code,
                    last_price,
                )
            else:
                logger.info(
                    "Realtime hard-stop evaluate action=enter "
                    "market=%s stock=%s last_price=%.4f hard_stop_price=%.4f "
                    "in_flight=%s",
                    market_code,
                    stock_code,
                    last_price,
                    tracked.hard_stop_price,
                    tracked.in_flight,
                )
        if tracked is None:
            if is_us_market:
                logger.info(
                    "Realtime hard-stop evaluate action=result "
                    "reason=untracked market=%s stock=%s last_price=%.4f",
                    market_code,
                    stock_code,
                    last_price,
                )
            else:
                logger.debug(
                    "Realtime hard-stop evaluate skipped market=%s stock=%s "
                    "reason=untracked last_price=%.4f",
                    market_code,
                    stock_code,
                    last_price,
                )
            return HardStopEvaluation(reason="untracked")
        if tracked.in_flight:
            if is_us_market:
                logger.info(
                    "Realtime hard-stop evaluate action=result "
                    "reason=in_flight market=%s stock=%s last_price=%.4f "
                    "hard_stop_price=%.4f in_flight=%s",
                    market_code,
                    stock_code,
                    last_price,
                    tracked.hard_stop_price,
                    tracked.in_flight,
                )
            else:
                logger.debug(
                    "Realtime hard-stop evaluate skipped market=%s stock=%s "
                    "reason=in_flight last_price=%.4f hard_stop_price=%.4f",
                    market_code,
                    stock_code,
                    last_price,
                    tracked.hard_stop_price,
                )
            return HardStopEvaluation(reason="in_flight")
        if last_price > tracked.hard_stop_price:
            if is_us_market:
                logger.info(
                    "Realtime hard-stop evaluate action=result "
                    "reason=above_stop market=%s stock=%s last_price=%.4f "
                    "hard_stop_price=%.4f in_flight=%s",
                    market_code,
                    stock_code,
                    last_price,
                    tracked.hard_stop_price,
                    tracked.in_flight,
                )
            else:
                logger.debug(
                    "Realtime hard-stop evaluate skipped market=%s stock=%s "
                    "reason=above_stop last_price=%.4f hard_stop_price=%.4f",
                    market_code,
                    stock_code,
                    last_price,
                    tracked.hard_stop_price,
                )
            return HardStopEvaluation(reason="above_stop")

        self._tracked[key] = replace(tracked, in_flight=True)
        trigger = HardStopTrigger(
            market_code=market_code,
            stock_code=stock_code,
            last_price=last_price,
            hard_stop_price=tracked.hard_stop_price,
            quantity=tracked.quantity,
            decision_id=tracked.decision_id,
            position_timestamp=tracked.position_timestamp,
            stock_name=tracked.stock_name,
        )
        if is_us_market:
            logger.info(
                "Realtime hard-stop evaluate action=result "
                "reason=triggered market=%s stock=%s last_price=%.4f "
                "hard_stop_price=%.4f in_flight=%s",
                market_code,
                stock_code,
                last_price,
                tracked.hard_stop_price,
                True,
            )
        else:
            logger.info(
                "Realtime hard-stop trigger fired market=%s stock=%s "
                "last_price=%.4f hard_stop_price=%.4f",
                market_code,
                stock_code,
                last_price,
                tracked.hard_stop_price,
            )
        return HardStopEvaluation(reason="triggered", trigger=trigger)

    def evaluate_price(
        self,
        market_code: str,
        stock_code: str,
        last_price: float,
    ) -> HardStopTrigger | None:
        return self.evaluate_price_diagnostic(market_code, stock_code, last_price).trigger
