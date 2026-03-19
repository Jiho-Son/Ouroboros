# OOR-820 Provider Abstraction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the Gemini-named runtime decision entrypoint with a provider-agnostic decision engine and move Gemini/Ollama behavior behind a shared provider abstraction/factory.

**Architecture:** Introduce `DecisionEngine` as the provider-agnostic owner of prompts, parsing, caching, and safe fallbacks. Move provider-specific raw text generation into explicit `GeminiProvider` and `OllamaProvider` implementations returned by one `build_llm_provider(settings)` factory.

**Tech Stack:** Python, pytest, aiohttp, google-genai, Pydantic settings

---

### Task 1: Lock the new abstraction in tests

**Files:**
- Modify: `tests/test_brain.py`
- Modify: `tests/test_evolution.py`
- Modify: `tests/test_main.py`
- Modify: `tests/test_data_integration.py`

**Step 1: Write the failing tests**

- Import `DecisionEngine` from `src.brain.decision_engine`.
- Add provider factory assertions for `GeminiProvider` / `OllamaProvider`.
- Switch runtime patch targets from `src.main.GeminiClient` to `src.main.DecisionEngine`.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_brain.py tests/test_evolution.py tests/test_main.py tests/test_data_integration.py -k 'decision or provider or ollama or gemini' -v`

Expected: FAIL because `DecisionEngine` / provider classes do not exist yet and runtime still imports `GeminiClient`.

**Step 3: Commit**

```bash
git add tests/test_brain.py tests/test_evolution.py tests/test_main.py tests/test_data_integration.py
git commit -m "test: lock provider-agnostic decision engine surface"
```

### Task 2: Implement the generic decision engine and provider factory

**Files:**
- Create: `src/brain/decision_engine.py`
- Modify: `src/brain/llm_client.py`
- Modify: `src/brain/gemini_client.py`
- Modify: `src/main.py`
- Modify: `src/strategy/pre_market_planner.py`
- Modify: `src/evolution/daily_review.py`
- Modify: `src/evolution/optimizer.py`

**Step 1: Write minimal implementation**

- Move decision-engine logic into `DecisionEngine`.
- Replace the provider protocol with `generate_text(model, prompt) -> str`.
- Add explicit `GeminiProvider` and `OllamaProvider` classes.
- Update runtime consumers to import `DecisionEngine`.
- Leave `src/brain/gemini_client.py` as a compatibility shim only.

**Step 2: Run the targeted tests**

Run: `pytest tests/test_brain.py tests/test_evolution.py tests/test_main.py tests/test_data_integration.py -k 'decision or provider or ollama or gemini' -v`

Expected: PASS

**Step 3: Commit**

```bash
git add src/brain/decision_engine.py src/brain/llm_client.py src/brain/gemini_client.py src/main.py src/strategy/pre_market_planner.py src/evolution/daily_review.py src/evolution/optimizer.py
git commit -m "refactor: add provider-agnostic decision engine"
```

### Task 3: Update docs and run final verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/context-tree.md`
- Modify: `src/data/README.md`
- Modify: `workflow/session-handover.md`

**Step 1: Align docs with shipped runtime wiring**

- Replace runtime-facing `GeminiClient` references with `DecisionEngine`.
- Document the shared provider abstraction/factory and compatibility shim.

**Step 2: Run verification**

Run: `pytest tests/test_brain.py tests/test_evolution.py tests/test_main.py tests/test_data_integration.py -k 'decision or provider or ollama or gemini' -v`
Expected: PASS

Run: `ruff check src/ tests/`
Expected: PASS

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 3: Commit**

```bash
git add docs/architecture.md docs/context-tree.md src/data/README.md workflow/session-handover.md
git commit -m "docs: align decision engine provider abstraction"
```
