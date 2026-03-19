# OOR-820 Provider-Agnostic Decision Engine Design

## Context

- `OOR-819` added `LLM_PROVIDER=gemini|ollama`, but the runtime decision path still instantiates `GeminiClient`.
- Reproduction signal on `2026-03-19`: `GeminiClient(Settings(..., LLM_PROVIDER="ollama"))` still reports class name `GeminiClient` while using the Ollama model.
- `src/brain/llm_client.py` already hides some low-level provider selection, but the decision engine still depends on a Google-style async surface (`aio.models.generate_content`).

## Options Considered

### 1. Rename call sites only

- Replace `GeminiClient` imports/usages with a new alias while leaving the existing module/class internals unchanged.
- Pros: smallest diff.
- Cons: provider-specific SDK behavior still leaks into the decision engine through the fake Google async surface.

### 2. Introduce a generic decision engine plus generic provider protocol

- Move prompt building, parsing, caching, and external-data handling into `DecisionEngine`.
- Replace the Google-shaped `LLMClient` protocol with a provider-agnostic `LLMProvider.generate_text(...)`.
- Implement `GeminiProvider` and `OllamaProvider` behind `build_llm_provider(settings)`.
- Keep `src/brain/gemini_client.py` as a compatibility shim only.
- Pros: provider naming and provider behavior are both separated cleanly with a narrow, testable boundary.
- Cons: wider rename across runtime/tests/docs.

### 3. Full provider module split for every consumer now

- Add separate engine/provider factories for runtime, planner, reviewer, and evolution, and remove all compatibility aliases immediately.
- Pros: maximum cleanup.
- Cons: larger blast radius than the ticket requires.

## Recommendation

Choose option 2.

It fixes the actual leak called out by `OOR-820` without expanding into a full architecture rewrite. The decision engine remains the owner of prompts, response parsing, caching, and domain-safe fallbacks. Gemini/Ollama ownership is limited to raw prompt execution inside provider implementations.

## Proposed Design

### Decision Engine Surface

- Create `src/brain/decision_engine.py`.
- Move `TradeDecision` and the current `GeminiClient` logic into a new `DecisionEngine` class.
- `DecisionEngine` owns:
  - prompt construction,
  - prompt overrides,
  - response parsing,
  - batch parsing,
  - caching,
  - external data integration,
  - safe HOLD fallback behavior.

### Provider Abstraction

- Replace the current Google-shaped client protocol with:
  - `LLMProvider` protocol: `async def generate_text(*, model: str, prompt: str) -> str`
- Provide explicit implementations:
  - `GeminiProvider`
  - `OllamaProvider`
- `build_llm_provider(settings)` selects the concrete provider.
- `EvolutionOptimizer` uses the same provider abstraction so runtime and strategy generation share one boundary.

### Compatibility

- Keep `src/brain/gemini_client.py` as a thin compatibility module that re-exports:
  - `DecisionEngine as GeminiClient`
  - `TradeDecision`
- Internal runtime/docs/tests move to the new provider-agnostic names.
- This preserves old imports for any untouched code while ensuring shipped runtime wiring no longer flows through Gemini-specific names.

## Testing Strategy

- Add failing tests first for:
  - provider factory selection returning explicit `GeminiProvider` / `OllamaProvider`,
  - runtime wiring using `DecisionEngine` instead of `GeminiClient`,
  - provider-backed decision generation through the generic interface.
- Update integration tests/docs to use `DecisionEngine`.
- Run targeted regression tests for `brain`, `evolution`, `main`, and external-data integration, then lint and docs sync.
