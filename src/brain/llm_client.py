"""Provider-selectable low-level LLM clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import aiohttp
from google import genai

from src.config import Settings


class LLMTextResponse(Protocol):
    """Minimal response shape expected by higher-level callers."""

    text: str


class LLMModelClient(Protocol):
    """Async model client that can generate text from a prompt."""

    async def generate_content(self, *, model: str, contents: str) -> LLMTextResponse:
        """Generate text for the given model and prompt."""


class LLMAsyncNamespace(Protocol):
    """Namespace exposing async model operations."""

    models: LLMModelClient


class LLMProvider(Protocol):
    """Low-level provider protocol used by the decision engine."""

    async def generate_text(self, *, model: str, prompt: str) -> str:
        """Generate raw text for the given model and prompt."""


# Backward-compatible alias for older call sites/tests.
LLMClient = LLMProvider


@dataclass(slots=True)
class _BasicTextResponse:
    text: str


class _OllamaAsyncModels:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def generate_content(self, *, model: str, contents: str) -> _BasicTextResponse:
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": contents,
                    "stream": False,
                },
            ) as response:
                response.raise_for_status()
                payload = await response.json()

        generated_text = payload.get("response")
        if not isinstance(generated_text, str):
            raise ValueError("Ollama response did not include a text `response` field")
        return _BasicTextResponse(text=generated_text)


class _OllamaAsyncNamespace:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.models = _OllamaAsyncModels(base_url=base_url, timeout_seconds=timeout_seconds)


class GeminiProvider:
    """Gemini-backed provider wrapper."""

    def __init__(self, *, api_key: str) -> None:
        self.aio = genai.Client(api_key=api_key).aio

    async def generate_text(self, *, model: str, prompt: str) -> str:
        response = await self.aio.models.generate_content(model=model, contents=prompt)
        return response.text


class OllamaProvider:
    """Ollama-backed provider wrapper."""

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self.aio = _OllamaAsyncNamespace(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )

    async def generate_text(self, *, model: str, prompt: str) -> str:
        response = await self.aio.models.generate_content(model=model, contents=prompt)
        return response.text


class OpenAICompatProvider:
    """OpenAI-compatible API provider (MLX, vLLM, llama.cpp, etc.)."""

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def generate_text(self, *, model: str, prompt: str) -> str:
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self._base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            ) as response:
                response.raise_for_status()
                payload = await response.json()

        choices = payload.get("choices")
        if not choices:
            raise ValueError("OpenAI-compatible response did not include `choices`")
        return choices[0]["message"]["content"]


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Return the configured low-level LLM provider."""
    if settings.LLM_PROVIDER == "ollama":
        return OllamaProvider(
            base_url=settings.OLLAMA_BASE_URL,
            timeout_seconds=settings.OLLAMA_REQUEST_TIMEOUT_SECONDS,
        )
    if settings.LLM_PROVIDER == "openai_compat":
        return OpenAICompatProvider(
            base_url=settings.OPENAI_COMPAT_BASE_URL,
            timeout_seconds=settings.OPENAI_COMPAT_REQUEST_TIMEOUT_SECONDS,
        )
    if not settings.GEMINI_API_KEY:
        raise ValueError("LLM_PROVIDER=gemini 이지만 GEMINI_API_KEY가 설정되지 않았습니다")
    return GeminiProvider(api_key=settings.GEMINI_API_KEY)


def build_llm_client(settings: Settings) -> LLMProvider:
    """Backward-compatible alias for older call sites."""
    return build_llm_provider(settings)
