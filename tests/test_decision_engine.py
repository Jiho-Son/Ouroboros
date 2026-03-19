"""Regression tests for the provider-agnostic decision engine surface."""

from __future__ import annotations

from unittest.mock import AsyncMock

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


def test_build_llm_provider_returns_gemini_provider(settings) -> None:
    provider = build_llm_provider(settings)

    assert isinstance(provider, GeminiProvider)


def test_build_llm_provider_returns_ollama_provider(settings) -> None:
    ollama_settings = settings.model_copy(update={"LLM_PROVIDER": "ollama"})

    provider = build_llm_provider(ollama_settings)

    assert isinstance(provider, OllamaProvider)


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
    assert llm_provider.calls == [
        {
            "model": settings.GEMINI_MODEL,
            "prompt": engine.build_prompt_sync(market_data),
        }
    ]
