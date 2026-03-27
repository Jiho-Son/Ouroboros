"""Tests for per-market session tracking store."""

from __future__ import annotations

from src.analysis.smart_scanner import ScanCandidate
from src.core.market_tracking import MarketTrackingStore


def _candidate(code: str) -> ScanCandidate:
    return ScanCandidate(
        stock_code=code,
        name=code,
        price=100.0,
        volume=1000.0,
        volume_ratio=2.0,
        rsi=45.0,
        signal="momentum",
        score=80.0,
    )


def test_ensure_market_session_reuses_same_session_state() -> None:
    store = MarketTrackingStore()
    store.record_scan_result(
        market_code="US_NASDAQ",
        session_id="US_PRE",
        candidates=[_candidate("AAPL"), _candidate("MSFT")],
        scanned_at=100.0,
    )

    store.ensure_market_session("US_NASDAQ", "US_PRE")
    snapshot = store.get_snapshot("US_NASDAQ", now_monotonic=112.0)

    assert snapshot is not None
    assert snapshot.session_id == "US_PRE"
    assert snapshot.active_stocks == ("AAPL", "MSFT")
    assert snapshot.candidate_codes == ("AAPL", "MSFT")
    assert snapshot.active_count == 2
    assert snapshot.candidate_count == 2
    assert snapshot.last_scan_age_seconds == 12.0


def test_ensure_market_session_rolls_over_new_session_and_clears_scan_state() -> None:
    store = MarketTrackingStore()
    store.record_scan_result(
        market_code="US_NASDAQ",
        session_id="US_PRE",
        candidates=[_candidate("AAPL")],
        scanned_at=50.0,
    )

    store.ensure_market_session("US_NASDAQ", "US_REG")
    snapshot = store.get_snapshot("US_NASDAQ", now_monotonic=75.0)

    assert snapshot is not None
    assert snapshot.session_id == "US_REG"
    assert snapshot.active_stocks == ()
    assert snapshot.candidate_codes == ()
    assert snapshot.active_count == 0
    assert snapshot.candidate_count == 0
    assert snapshot.last_scan_age_seconds is None


def test_clear_market_removes_only_target_market() -> None:
    store = MarketTrackingStore()
    store.record_scan_result(
        market_code="KR",
        session_id="KRX_REG",
        candidates=[_candidate("005930")],
        scanned_at=10.0,
    )
    store.record_scan_result(
        market_code="US_NASDAQ",
        session_id="US_REG",
        candidates=[_candidate("AAPL")],
        scanned_at=12.0,
    )

    store.clear_market("KR")

    assert store.get_snapshot("KR", now_monotonic=20.0) is None
    us_snapshot = store.get_snapshot("US_NASDAQ", now_monotonic=20.0)
    assert us_snapshot is not None
    assert us_snapshot.session_id == "US_REG"
    assert us_snapshot.active_stocks == ("AAPL",)


def test_runtime_fallback_stocks_blocks_session_mismatch() -> None:
    store = MarketTrackingStore()
    store.record_scan_result(
        market_code="US_NASDAQ",
        session_id="US_PRE",
        candidates=[_candidate("AAPL")],
        scanned_at=100.0,
    )

    assert store.runtime_fallback_stocks("US_NASDAQ", "US_PRE") == ["AAPL"]
    assert store.runtime_fallback_stocks("US_NASDAQ", "US_REG") == []

