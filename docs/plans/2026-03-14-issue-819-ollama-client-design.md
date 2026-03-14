# OOR-819 Ollama Client Design

## Context

- Ticket `OOR-819` requires a new client for personal LLM serving, starting with Ollama.
- Current configuration is Gemini-only:
  - `src/config.py` requires `GEMINI_API_KEY` and exposes only `GEMINI_MODEL`.
  - `src/brain/gemini_client.py` creates `google.genai.Client` directly.
  - `src/evolution/optimizer.py` also creates `google.genai.Client` directly.
  - `src/main.py` hard-codes `GeminiClient(settings)` for the trading runtime.
- That means provider selection is not configurable today, and switching to a local Ollama server would require code edits.

## Approaches Considered

### 1. Replace `GeminiClient` with a brand-new generic decision engine

- Introduce a renamed top-level class for all decision-making and migrate every caller immediately.
- Pros: clean naming and a clearer long-term abstraction.
- Cons: broad churn across runtime, planner, review, and tests for limited ticket scope.

### 2. Add provider-specific low-level text clients and keep the existing decision engine surface

- Create a provider factory plus two low-level clients:
  - `GeminiLLMClient`
  - `OllamaLLMClient`
- Let the existing decision-focused classes delegate text generation through that factory.
- Pros: small review surface, preserves current trade-decision behavior, and covers both runtime and other direct LLM call sites.
- Cons: `GeminiClient` keeps its legacy name even when backed by Ollama.

### 3. Add an Ollama path only inside `src/main.py`

- Keep the rest of the codebase Gemini-only and special-case runtime wiring.
- Pros: smallest short-term patch.
- Cons: incomplete provider selection, duplicates logic, and leaves direct Gemini coupling elsewhere.

## Recommendation

Choose approach 2.

The ticket asks for configurable client selection, not a full decision-engine rewrite. A provider factory with provider-specific adapters is the narrowest way to add Ollama while keeping existing decision parsing, prompt building, caching, and planner/reviewer flows intact.

## Proposed Design

### Configuration

- Add `LLM_PROVIDER` with allowed values `gemini|ollama`, defaulting to `gemini`.
- Keep Gemini settings but make the API key conditional on `LLM_PROVIDER=gemini`.
- Add Ollama settings:
  - `OLLAMA_BASE_URL`
  - `OLLAMA_MODEL`
  - `OLLAMA_REQUEST_TIMEOUT_SECONDS`

### Provider Client Abstraction

- Add a small async provider interface for raw prompt-to-text generation.
- Implement:
  - `GeminiLLMClient` using the current Google SDK call path.
  - `OllamaLLMClient` using `aiohttp` against Ollama's generate API.
- Add a factory that returns the configured provider client from `Settings`.

### Runtime/Data Flow

- `GeminiClient` keeps its current public behavior (`decide`, `decide_batch`, parsing, token accounting) but delegates raw prompt execution to the provider client from the factory.
- `EvolutionOptimizer` uses the same provider factory for code-generation prompts so provider selection is consistent outside the runtime path.
- `src/main.py` continues to instantiate the decision client in one place, but provider selection happens through config rather than hard-coded Google SDK usage.

### Error Handling

- Provider selection errors should fail fast during settings validation.
- Ollama request failures should return the same safe HOLD fallback behavior already used for Gemini API failures.
- Ollama response parsing should accept the plain generated text body and ignore provider-specific metadata.

## Files Expected To Change

- `.env.example`
- `src/config.py`
- `src/brain/gemini_client.py`
- `src/evolution/optimizer.py`
- `tests/test_config.py`
- `tests/test_brain.py`
- `tests/test_evolution.py`
- `tests/test_main.py`
- `docs/architecture.md`

## Verification Plan

- Reproduce the current coupling with a grep-based signal showing direct Gemini-only configuration and instantiation.
- Add failing tests first for:
  - provider-specific settings validation,
  - provider factory selection,
  - Ollama-backed text generation inside the decision client,
  - runtime wiring that still uses the decision client through `src/main.py`.
- Run focused pytest coverage for config/brain/evolution/main touched surfaces.
- Run `ruff check` on touched source and test files.
