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
