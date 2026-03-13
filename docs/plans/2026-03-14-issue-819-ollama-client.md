# Ollama Client Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an Ollama-backed LLM client and a configuration-driven provider selector without regressing the current Gemini default path.

**Architecture:** Keep trade-decision parsing and prompting in the existing decision engine, but route raw prompt execution through a provider factory. Apply the same provider selection to direct LLM generation in the evolution optimizer so the repo has one configuration switch.

**Tech Stack:** Python, aiohttp, Pydantic settings, pytest, existing trading runtime orchestration

---

### Task 1: Lock provider-selection behavior in tests

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_brain.py`

**Step 1: Write the failing settings validation tests**

Add tests that prove:
- `LLM_PROVIDER="ollama"` does not require `GEMINI_API_KEY`.
- `LLM_PROVIDER="gemini"` still requires `GEMINI_API_KEY`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -k "llm_provider or ollama" -v`
Expected: FAIL because settings are Gemini-only today.

**Step 3: Write the failing provider delegation test**

Add a `GeminiClient` test that injects an Ollama-like provider stub and expects `decide()` to use that provider to produce a parsed decision.

**Step 4: Run test to verify it fails**

Run: `pytest tests/test_brain.py -k "ollama or provider" -v`
Expected: FAIL because the client still calls Google SDK directly.

### Task 2: Implement configuration and provider adapters

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`
- Create: `src/brain/llm_client.py`

**Step 1: Add provider-aware settings**

Introduce:
- `LLM_PROVIDER`
- `OLLAMA_BASE_URL`
- `OLLAMA_MODEL`
- `OLLAMA_REQUEST_TIMEOUT_SECONDS`

Add conditional validation so Gemini credentials are required only when Gemini is selected.

**Step 2: Add the provider interface and factory**

Create a small async interface for raw prompt generation plus a `build_llm_client(settings)` factory that returns the configured provider client.

**Step 3: Implement provider clients**

Add:
- a Gemini adapter that wraps the current `google.genai` call,
- an Ollama adapter that posts to the configured local server and returns the generated text.

### Task 3: Wire the decision engine and evolution path

**Files:**
- Modify: `src/brain/gemini_client.py`
- Modify: `src/evolution/optimizer.py`
- Modify: `tests/test_evolution.py`
- Modify: `tests/test_main.py`

**Step 1: Refactor `GeminiClient` to delegate raw generation**

Keep prompt building, response parsing, batch handling, and caching intact, but replace direct SDK calls with the configured provider client.

**Step 2: Refactor `EvolutionOptimizer` to use the same provider factory**

Replace its direct `google.genai.Client` construction with the shared provider abstraction.

**Step 3: Add runtime-level regression coverage**

Update `tests/test_main.py` so the runtime still instantiates the decision engine once and preserves the default Gemini path.

**Step 4: Run focused tests**

Run: `pytest tests/test_config.py tests/test_brain.py tests/test_evolution.py tests/test_main.py -k "llm or ollama or provider" -v`
Expected: PASS after the wiring is complete.

### Task 4: Document and verify the touched surface

**Files:**
- Modify: `docs/architecture.md`

**Step 1: Document provider selection**

Add a short architecture note describing the shared LLM provider factory and the new Ollama configuration path.

**Step 2: Run touched-surface lint**

Run: `ruff check src/config.py src/brain/gemini_client.py src/brain/llm_client.py src/evolution/optimizer.py tests/test_config.py tests/test_brain.py tests/test_evolution.py tests/test_main.py`
Expected: PASS.

**Step 3: Run touched-surface tests**

Run: `pytest tests/test_config.py tests/test_brain.py tests/test_evolution.py tests/test_main.py -v`
Expected: PASS.
