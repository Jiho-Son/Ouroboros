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


class LLMClient(Protocol):
    """Low-level provider client protocol used by the decision engine."""

    aio: LLMAsyncNamespace


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


class OllamaLLMClient:
    """Low-level client wrapper that mimics the Google SDK async surface."""

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self.aio = _OllamaAsyncNamespace(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )


def build_llm_client(settings: Settings) -> LLMClient:
    """Return the configured low-level LLM client."""
    if settings.LLM_PROVIDER == "ollama":
        return OllamaLLMClient(
            base_url=settings.OLLAMA_BASE_URL,
            timeout_seconds=settings.OLLAMA_REQUEST_TIMEOUT_SECONDS,
        )
    return genai.Client(api_key=settings.GEMINI_API_KEY)
