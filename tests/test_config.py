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
        )
