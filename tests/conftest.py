"""Shared test fixtures for The Ouroboros test suite."""

from __future__ import annotations

import pytest

from src.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Return a Settings instance with safe test defaults."""
    return Settings(
        KIS_APP_KEY="test_app_key",
        KIS_APP_SECRET="test_app_secret",
        KIS_ACCOUNT_NO="12345678-01",
        KIS_BASE_URL="https://openapivts.koreainvestment.com:9443",
        GEMINI_API_KEY="test_gemini_key",
        CIRCUIT_BREAKER_PCT=-3.0,
        FAT_FINGER_PCT=30.0,
        CONFIDENCE_THRESHOLD=80,
        DB_PATH=":memory:",
        ENABLED_MARKETS="KR",
    )
