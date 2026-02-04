"""TDD tests for core/risk_manager.py — written BEFORE implementation."""

from __future__ import annotations

import pytest

from src.core.risk_manager import (
    CircuitBreakerTripped,
    FatFingerRejected,
    RiskManager,
)

# ---------------------------------------------------------------------------
# Circuit Breaker Tests
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """The circuit breaker must halt all trading when daily loss exceeds the threshold."""

    def test_allows_trading_when_pnl_is_positive(self, settings):
        rm = RiskManager(settings)
        # 2% gain — should be fine
        rm.check_circuit_breaker(current_pnl_pct=2.0)

    def test_allows_trading_at_zero_pnl(self, settings):
        rm = RiskManager(settings)
        rm.check_circuit_breaker(current_pnl_pct=0.0)

    def test_allows_trading_at_exactly_threshold(self, settings):
        rm = RiskManager(settings)
        # Exactly -3.0% is ON the boundary — still allowed
        rm.check_circuit_breaker(current_pnl_pct=-3.0)

    def test_trips_when_loss_exceeds_threshold(self, settings):
        rm = RiskManager(settings)
        with pytest.raises(CircuitBreakerTripped):
            rm.check_circuit_breaker(current_pnl_pct=-3.01)

    def test_trips_at_large_loss(self, settings):
        rm = RiskManager(settings)
        with pytest.raises(CircuitBreakerTripped):
            rm.check_circuit_breaker(current_pnl_pct=-10.0)

    def test_custom_threshold(self):
        """A stricter threshold (-1.5%) should trip earlier."""
        from src.config import Settings

        strict = Settings(
            KIS_APP_KEY="k",
            KIS_APP_SECRET="s",
            KIS_ACCOUNT_NO="00000000-00",
            KIS_BASE_URL="https://example.com",
            GEMINI_API_KEY="g",
            CIRCUIT_BREAKER_PCT=-1.5,
            FAT_FINGER_PCT=30.0,
            CONFIDENCE_THRESHOLD=80,
            DB_PATH=":memory:",
        )
        rm = RiskManager(strict)
        with pytest.raises(CircuitBreakerTripped):
            rm.check_circuit_breaker(current_pnl_pct=-1.51)


# ---------------------------------------------------------------------------
# Fat Finger Tests
# ---------------------------------------------------------------------------


class TestFatFingerCheck:
    """Orders exceeding 30% of total cash must be rejected."""

    def test_allows_small_order(self, settings):
        rm = RiskManager(settings)
        # 10% of 10_000_000 = 1_000_000
        rm.check_fat_finger(order_amount=1_000_000, total_cash=10_000_000)

    def test_allows_order_at_exactly_threshold(self, settings):
        rm = RiskManager(settings)
        # Exactly 30% — allowed
        rm.check_fat_finger(order_amount=3_000_000, total_cash=10_000_000)

    def test_rejects_order_exceeding_threshold(self, settings):
        rm = RiskManager(settings)
        with pytest.raises(FatFingerRejected):
            rm.check_fat_finger(order_amount=3_000_001, total_cash=10_000_000)

    def test_rejects_massive_order(self, settings):
        rm = RiskManager(settings)
        with pytest.raises(FatFingerRejected):
            rm.check_fat_finger(order_amount=9_000_000, total_cash=10_000_000)

    def test_zero_cash_rejects_any_order(self, settings):
        rm = RiskManager(settings)
        with pytest.raises(FatFingerRejected):
            rm.check_fat_finger(order_amount=1, total_cash=0)


# ---------------------------------------------------------------------------
# Pre-Order Validation (Integration of both checks)
# ---------------------------------------------------------------------------


class TestPreOrderValidation:
    """validate_order must run BOTH checks before approving."""

    def test_passes_when_both_checks_ok(self, settings):
        rm = RiskManager(settings)
        rm.validate_order(
            current_pnl_pct=0.5,
            order_amount=1_000_000,
            total_cash=10_000_000,
        )

    def test_fails_on_circuit_breaker(self, settings):
        rm = RiskManager(settings)
        with pytest.raises(CircuitBreakerTripped):
            rm.validate_order(
                current_pnl_pct=-5.0,
                order_amount=100,
                total_cash=10_000_000,
            )

    def test_fails_on_fat_finger(self, settings):
        rm = RiskManager(settings)
        with pytest.raises(FatFingerRejected):
            rm.validate_order(
                current_pnl_pct=1.0,
                order_amount=5_000_000,
                total_cash=10_000_000,
            )
