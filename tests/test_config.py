import pytest
from pydantic import ValidationError

from src.config import Settings


def test_us_min_price_cannot_be_zero():
    """US_MIN_PRICE=0은 허용하지 않는다 (ge=1.0)."""
    with pytest.raises(ValidationError):
        Settings(
            KIS_APP_KEY="test",
            KIS_APP_SECRET="test",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="test",
            US_MIN_PRICE=0.0,
        )


def test_us_min_price_cannot_be_below_one():
    """US_MIN_PRICE=0.5도 허용하지 않는다."""
    with pytest.raises(ValidationError):
        Settings(
            KIS_APP_KEY="test",
            KIS_APP_SECRET="test",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="test",
            US_MIN_PRICE=0.5,
        )


def test_us_min_price_default_is_five():
    """기본값은 5.0."""
    s = Settings(
        KIS_APP_KEY="test",
        KIS_APP_SECRET="test",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test",
    )
    assert s.US_MIN_PRICE == 5.0


def test_ollama_provider_does_not_require_gemini_api_key():
    """`LLM_PROVIDER=ollama`일 때는 Gemini 키 없이도 설정이 구성되어야 한다."""
    s = Settings(
        KIS_APP_KEY="test",
        KIS_APP_SECRET="test",
        KIS_ACCOUNT_NO="12345678-01",
        LLM_PROVIDER="ollama",
        OLLAMA_MODEL="llama3.2",
    )
    assert s.LLM_PROVIDER == "ollama"
    assert s.OLLAMA_MODEL == "llama3.2"


def test_gemini_provider_still_requires_gemini_api_key():
    """`LLM_PROVIDER=gemini`에서는 Gemini 키 요구사항이 유지되어야 한다."""
    with pytest.raises(ValidationError):
        Settings(
            KIS_APP_KEY="test",
            KIS_APP_SECRET="test",
            KIS_ACCOUNT_NO="12345678-01",
            LLM_PROVIDER="gemini",
            GEMINI_API_KEY=None,
        )


def test_executable_quote_gap_caps_by_market_parses_and_normalizes_keys():
    """시장별 gap cap JSON은 대문자 market key로 파싱되어야 한다."""
    s = Settings(
        KIS_APP_KEY="test",
        KIS_APP_SECRET="test",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test",
        EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON='{"kr": 0.5, "US_NASDAQ": 1.2}',
    )
    assert s.executable_quote_gap_caps_by_market == {"KR": 0.5, "US_NASDAQ": 1.2}


def test_executable_quote_gap_caps_by_market_requires_valid_json_object():
    """market gap cap 설정은 JSON object여야 한다."""
    with pytest.raises(ValidationError):
        Settings(
            KIS_APP_KEY="test",
            KIS_APP_SECRET="test",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="test",
            EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON='["KR", 1.0]',
        )


def test_executable_quote_gap_caps_by_market_requires_numeric_values():
    """market gap cap 값은 숫자 범위 [0,100]이어야 한다."""
    with pytest.raises(ValidationError):
        Settings(
            KIS_APP_KEY="test",
            KIS_APP_SECRET="test",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="test",
            EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON='{"KR": "wide"}',
        )

    with pytest.raises(ValidationError):
        Settings(
            KIS_APP_KEY="test",
            KIS_APP_SECRET="test",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="test",
            EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON='{"KR": 120}',
        )


def test_executable_quote_gap_caps_by_market_reuses_private_cache_per_instance():
    """`@property` should keep returning the validator-populated per-instance cache."""
    s = Settings(
        KIS_APP_KEY="test",
        KIS_APP_SECRET="test",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test",
        EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON='{"KR": 0.5}',
    )
    first = s.executable_quote_gap_caps_by_market
    second = s.executable_quote_gap_caps_by_market
    assert first == {"KR": 0.5}
    # The `@property` returns the same dict stored in the per-instance `PrivateAttr` cache.
    assert second is first


def test_executable_quote_gap_caps_validation_populates_private_cache_once_per_instance(
    monkeypatch: pytest.MonkeyPatch,
):
    """Validation should parse once, then property access should reuse the per-instance cache."""
    original = Settings._parse_executable_quote_gap_caps_by_market
    calls = 0

    def _spy(self: Settings) -> dict[str, float]:
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(Settings, "_parse_executable_quote_gap_caps_by_market", _spy)

    s = Settings(
        KIS_APP_KEY="test",
        KIS_APP_SECRET="test",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test",
        EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON='{"KR": 0.5}',
    )

    first = s.executable_quote_gap_caps_by_market
    second = s.executable_quote_gap_caps_by_market

    assert first == {"KR": 0.5}
    assert second is first
    assert calls == 1
