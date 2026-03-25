"""Regression tests for the provider-agnostic decision engine surface."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brain.decision_engine import DecisionEngine
from src.brain.llm_client import GeminiProvider, OllamaProvider, build_llm_provider


class _StubLLMProvider:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[dict[str, str]] = []
        self.generate_text = AsyncMock(side_effect=self._generate_text)

    async def _generate_text(self, *, model: str, prompt: str) -> str:
        self.calls.append({"model": model, "prompt": prompt})
        return self._response_text


class _LegacyStubLLMClient:
    def __init__(self, response_text: str) -> None:
        self.aio = MagicMock()
        self.aio.models = MagicMock()
        self.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text=response_text)
        )


def test_build_llm_provider_returns_gemini_provider(settings) -> None:
    provider = build_llm_provider(settings)

    assert isinstance(provider, GeminiProvider)


def test_build_llm_provider_returns_ollama_provider(settings) -> None:
    ollama_settings = settings.model_copy(update={"LLM_PROVIDER": "ollama"})

    provider = build_llm_provider(ollama_settings)

    assert isinstance(provider, OllamaProvider)


def test_build_llm_provider_requires_gemini_api_key(settings) -> None:
    missing_key_settings = settings.model_copy(update={"GEMINI_API_KEY": ""})

    with pytest.raises(
        ValueError, match="LLM_PROVIDER=gemini .* GEMINI_API_KEY가 설정되지 않았습니다"
    ):
        build_llm_provider(missing_key_settings)


@pytest.mark.asyncio
async def test_decision_engine_uses_injected_llm_provider(settings) -> None:
    llm_provider = _StubLLMProvider(
        '{"action": "BUY", "confidence": 92, "rationale": "Local model approved"}'
    )
    engine = DecisionEngine(
        settings,
        llm_provider=llm_provider,
        enable_cache=False,
        enable_optimization=False,
    )
    market_data = {
        "stock_code": "005930",
        "current_price": 72000,
        "orderbook": {"asks": [], "bids": []},
    }

    decision = await engine.decide(market_data)

    assert decision.action == "BUY"
    assert decision.confidence == 92
    assert decision.llm_prompt == engine.build_prompt_sync(market_data)
    assert (
        decision.llm_response
        == '{"action": "BUY", "confidence": 92, "rationale": "Local model approved"}'
    )
    assert llm_provider.calls == [
        {
            "model": settings.GEMINI_MODEL,
            "prompt": engine.build_prompt_sync(market_data),
        }
    ]


def test_decision_engine_warns_when_llm_client_is_used(settings) -> None:
    llm_provider = _StubLLMProvider('{"action": "HOLD", "confidence": 80, "rationale": "ok"}')

    with pytest.warns(DeprecationWarning, match="llm_client is deprecated"):
        DecisionEngine(settings, llm_client=llm_provider)


@pytest.mark.asyncio
async def test_decision_engine_warns_on_legacy_generate_content_fallback(settings) -> None:
    legacy_client = _LegacyStubLLMClient(
        '{"action": "HOLD", "confidence": 80, "rationale": "legacy"}'
    )
    engine = DecisionEngine(
        settings,
        llm_provider=legacy_client,
        enable_cache=False,
        enable_optimization=False,
    )

    with pytest.warns(DeprecationWarning, match="legacy LLM client fallback"):
        result = await engine._generate_text("prompt")

    assert result == '{"action": "HOLD", "confidence": 80, "rationale": "legacy"}'
