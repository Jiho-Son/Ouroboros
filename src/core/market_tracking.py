"""Session-scoped runtime tracking store for market scanner state."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from src.analysis.smart_scanner import ScanCandidate


@dataclass
class MarketTrackingSessionState:
    """Mutable runtime tracking state for one market/session pair."""

    market_code: str
    session_id: str
    active_stocks: list[str] = field(default_factory=list)
    scan_candidates: dict[str, ScanCandidate] = field(default_factory=dict)
    last_scan_monotonic: float | None = None


@dataclass(frozen=True)
class MarketTrackingSnapshot:
    """Immutable diagnostics snapshot for one market."""

    market_code: str
    session_id: str
    active_stocks: tuple[str, ...]
    candidate_codes: tuple[str, ...]
    active_count: int
    candidate_count: int
    last_scan_monotonic: float | None
    last_scan_age_seconds: float | None

    def to_dashboard_dict(self) -> dict[str, Any]:
        """Return a JSON-safe diagnostics payload."""
        return {
            "session_id": self.session_id,
            "active_count": self.active_count,
            "active_stocks": list(self.active_stocks),
            "candidate_count": self.candidate_count,
            "candidate_codes": list(self.candidate_codes),
            "last_scan_age_seconds": self.last_scan_age_seconds,
        }


@dataclass(frozen=True)
class MarketSessionEnsureResult:
    """Describe how ensure/rollover handled the current request."""

    market_code: str
    session_id: str
    previous_session_id: str | None
    action: str


class MarketTrackingStore:
    """Thread-safe per-market store for current-session scanner state."""

    def __init__(self) -> None:
        self._states: dict[str, MarketTrackingSessionState] = {}
        self._lock = threading.Lock()

    def ensure_market_session(self, market_code: str, session_id: str) -> MarketSessionEnsureResult:
        """Create or roll over session-scoped state for a market."""
        with self._lock:
            state = self._states.get(market_code)
            if state is None:
                self._states[market_code] = MarketTrackingSessionState(
                    market_code=market_code,
                    session_id=session_id,
                )
                return MarketSessionEnsureResult(
                    market_code=market_code,
                    session_id=session_id,
                    previous_session_id=None,
                    action="created",
                )
            if state.session_id == session_id:
                return MarketSessionEnsureResult(
                    market_code=market_code,
                    session_id=session_id,
                    previous_session_id=state.session_id,
                    action="reused",
                )

            previous_session_id = state.session_id
            self._states[market_code] = MarketTrackingSessionState(
                market_code=market_code,
                session_id=session_id,
            )
            return MarketSessionEnsureResult(
                market_code=market_code,
                session_id=session_id,
                previous_session_id=previous_session_id,
                action="rolled_over",
            )

    def clear_market(self, market_code: str) -> MarketTrackingSnapshot | None:
        """Drop all runtime tracking state for a closed market."""
        with self._lock:
            state = self._states.pop(market_code, None)
            if state is None:
                return None
            return self._snapshot_from_state(state, now_monotonic=time.monotonic())

    def record_scan_result(
        self,
        *,
        market_code: str,
        session_id: str,
        candidates: list[ScanCandidate],
        scanned_at: float,
    ) -> MarketTrackingSnapshot:
        """Atomically store scanner candidates and the active universe."""
        with self._lock:
            state = self._ensure_state_locked(market_code, session_id)
            state.active_stocks = [candidate.stock_code for candidate in candidates]
            state.scan_candidates = {candidate.stock_code: candidate for candidate in candidates}
            state.last_scan_monotonic = scanned_at
            return self._snapshot_from_state(state, now_monotonic=scanned_at)

    def record_empty_scan(
        self,
        *,
        market_code: str,
        session_id: str,
        scanned_at: float,
    ) -> MarketTrackingSnapshot:
        """Store an empty same-session scan result."""
        with self._lock:
            state = self._ensure_state_locked(market_code, session_id)
            state.active_stocks = []
            state.scan_candidates = {}
            state.last_scan_monotonic = scanned_at
            return self._snapshot_from_state(state, now_monotonic=scanned_at)

    def runtime_fallback_stocks(self, market_code: str, session_id: str) -> list[str]:
        """Return same-session runtime universe only when session identity matches."""
        with self._lock:
            state = self._states.get(market_code)
            if state is None or state.session_id != session_id:
                return []
            return list(state.active_stocks)

    def last_scan_monotonic(self, market_code: str, session_id: str) -> float | None:
        """Return same-session last scan timestamp."""
        with self._lock:
            state = self._states.get(market_code)
            if state is None or state.session_id != session_id:
                return None
            return state.last_scan_monotonic

    def scan_candidates_snapshot(self) -> dict[str, dict[str, ScanCandidate]]:
        """Return a read-only copy compatible with existing trading-cycle helpers."""
        with self._lock:
            return {
                market_code: dict(state.scan_candidates)
                for market_code, state in self._states.items()
            }

    def get_snapshot(
        self,
        market_code: str,
        *,
        now_monotonic: float | None = None,
    ) -> MarketTrackingSnapshot | None:
        """Return immutable diagnostics for one market."""
        with self._lock:
            state = self._states.get(market_code)
            if state is None:
                return None
            return self._snapshot_from_state(
                state,
                now_monotonic=time.monotonic() if now_monotonic is None else now_monotonic,
            )

    def dashboard_status_payload(self) -> dict[str, dict[str, Any]]:
        """Return JSON-safe diagnostics grouped by market."""
        with self._lock:
            now_monotonic = time.monotonic()
            return {
                market_code: self._snapshot_from_state(
                    state,
                    now_monotonic=now_monotonic,
                ).to_dashboard_dict()
                for market_code, state in self._states.items()
            }

    def _ensure_state_locked(
        self,
        market_code: str,
        session_id: str,
    ) -> MarketTrackingSessionState:
        state = self._states.get(market_code)
        if state is None or state.session_id != session_id:
            state = MarketTrackingSessionState(
                market_code=market_code,
                session_id=session_id,
            )
            self._states[market_code] = state
        return state

    @staticmethod
    def _snapshot_from_state(
        state: MarketTrackingSessionState,
        *,
        now_monotonic: float | None,
    ) -> MarketTrackingSnapshot:
        last_scan_age_seconds: float | None = None
        if state.last_scan_monotonic is not None and now_monotonic is not None:
            last_scan_age_seconds = max(now_monotonic - state.last_scan_monotonic, 0.0)
        candidate_codes = tuple(state.scan_candidates)
        return MarketTrackingSnapshot(
            market_code=state.market_code,
            session_id=state.session_id,
            active_stocks=tuple(state.active_stocks),
            candidate_codes=candidate_codes,
            active_count=len(state.active_stocks),
            candidate_count=len(state.scan_candidates),
            last_scan_monotonic=state.last_scan_monotonic,
            last_scan_age_seconds=last_scan_age_seconds,
        )
