# Dashboard Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 상태 중심 대시보드와 결정 히스토리 필터를 제공하고, 각 결정에서 LLM request/response trace 를 조회할 수 있게 만든다.

**Architecture:** `TradeDecision` 에서 생성한 LLM trace 를 `decision_logs` 에 저장한 뒤, `/api/decisions` 가 풍부한 필터와 metadata 를 붙여 전달한다. 프런트엔드는 결정 히스토리를 중심 레이아웃으로 재구성하고, raw 진단 패널은 `Diagnostics` 아래로 접어 노출 강도를 낮춘다.

**Tech Stack:** Python, FastAPI, SQLite, vanilla HTML/CSS/JS, pytest

---

### Task 1: LLM Trace Persistence Tests

**Files:**
- Modify: `tests/test_decision_engine.py`
- Modify: `tests/test_decision_logger.py`
- Test: `tests/test_decision_engine.py`
- Test: `tests/test_decision_logger.py`

**Step 1: Write the failing test**

Add tests that assert:
- `DecisionEngine.decide()` returns a `TradeDecision` carrying the exact prompt sent to the provider.
- `DecisionLogger.log_decision()` stores and retrieves `llm_prompt` and `llm_response`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_decision_engine.py tests/test_decision_logger.py -v`
Expected: FAIL because `TradeDecision` / `DecisionLog` / `decision_logs` do not yet support `llm_prompt` and `llm_response`.

**Step 3: Write minimal implementation**

Modify:
- `src/brain/decision_engine.py`
- `src/decision_logging/decision_logger.py`
- `src/db.py`
- `src/main.py`

Implement:
- optional trace fields on `TradeDecision` / `DecisionLog`
- DB migration for `decision_logs.llm_prompt` / `decision_logs.llm_response`
- runtime logging path that persists trace data

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_decision_engine.py tests/test_decision_logger.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/brain/decision_engine.py src/decision_logging/decision_logger.py src/db.py src/main.py tests/test_decision_engine.py tests/test_decision_logger.py
git commit -m "feat: persist dashboard llm traces"
```

### Task 2: Decision API Filter Tests

**Files:**
- Modify: `tests/test_dashboard.py`
- Test: `tests/test_dashboard.py`

**Step 1: Write the failing test**

Add tests that assert `/api/decisions` can:
- filter by `market=all`, `action`, `session_id`, `stock_code`, `min_confidence`
- filter by `from_date` / `to_date`
- filter by `matched_only`
- return `markets` / `sessions` metadata and trace fields

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard.py -k \"decisions or dashboard\" -v`
Expected: FAIL because the endpoint only supports `market` and `limit`.

**Step 3: Write minimal implementation**

Modify: `src/dashboard/app.py`

Implement:
- richer `/api/decisions` query params
- SQL filter assembly with parameter binding
- metadata in the response body

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard.py -k \"decisions or dashboard\" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/dashboard/app.py tests/test_dashboard.py
git commit -m "feat: add dashboard decision filters"
```

### Task 3: Dashboard UI Restructure Tests

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `src/dashboard/static/index.html`
- Test: `tests/test_dashboard.py`

**Step 1: Write the failing test**

Add assertions that the served HTML contains:
- decision filter controls
- an `LLM request/response` trace label
- a collapsed `Diagnostics` section instead of always-open raw panels

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard.py -k \"index or html\" -v`
Expected: FAIL because the current HTML still renders the legacy layout.

**Step 3: Write minimal implementation**

Modify: `src/dashboard/static/index.html`

Implement:
- filter bar + decision timeline layout
- expandable trace details per decision
- diagnostics section for playbook/scorecard/scenario/context panels

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard.py -k \"index or html\" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/dashboard/static/index.html tests/test_dashboard.py
git commit -m "feat: refocus dashboard on decision history"
```

### Task 4: Final Validation

**Files:**
- Modify: `docs/commands.md`
- Modify: `workflow/session-handover.md`
- Test: `tests/test_decision_engine.py`
- Test: `tests/test_decision_logger.py`
- Test: `tests/test_dashboard.py`

**Step 1: Run targeted tests**

Run: `pytest tests/test_decision_engine.py tests/test_decision_logger.py tests/test_dashboard.py -v`
Expected: PASS

**Step 2: Run repo checks for touched surface**

Run: `ruff check src/ tests/`
Expected: PASS

**Step 3: Sync docs if dashboard endpoint documentation changed**

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS after docs updates

**Step 4: Commit**

```bash
git add docs/commands.md workflow/session-handover.md
git commit -m "docs: record dashboard cleanup verification"
```
