# Dashboard Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 상태 중심 대시보드와 풍부한 결정 히스토리 필터를 제공하고, 각 결정에서 LLM request/response trace 를 조회할 수 있게 만든다.

**Architecture:** `TradeDecision` 에 prompt/raw response 를 담고 `DecisionLogger` 와 `decision_logs` 에 저장한다. `/api/decisions` 는 풍부한 필터와 metadata 를 제공하고, 대시보드는 결정 히스토리를 중심으로 재구성하며 raw diagnostics 패널은 접힌 `Diagnostics` 로 이동한다. 최신 `src/main.py` daily/live runtime 변경은 유지한 채 trace 전달만 최소 수정한다.

**Tech Stack:** Python, FastAPI, SQLite, vanilla HTML/CSS/JS, pytest

---

### Task 1: Trace Persistence RED

**Files:**
- Modify: `tests/test_decision_engine.py`
- Modify: `tests/test_decision_logger.py`
- Modify: `tests/test_main.py`
- Test: `tests/test_decision_engine.py`
- Test: `tests/test_decision_logger.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

Add tests that assert:
- `DecisionEngine.decide()` returns a `TradeDecision` carrying the exact prompt and raw response.
- `DecisionLogger.log_decision()` stores and retrieves `llm_prompt` / `llm_response`.
- main runtime decision logging forwards `decision.llm_prompt` / `decision.llm_response` to `DecisionLogger.log_decision()`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_decision_engine.py tests/test_decision_logger.py tests/test_main.py -k "llm or scenario_match" -v`
Expected: FAIL because trace fields do not yet exist or are not forwarded.

**Step 3: Write minimal implementation**

Modify:
- `src/brain/decision_engine.py`
- `src/decision_logging/decision_logger.py`
- `src/db.py`
- `src/main.py`

Implement:
- optional trace fields on `TradeDecision` / `DecisionLog`
- DB migration for `decision_logs.llm_prompt` / `decision_logs.llm_response`
- decision logging call sites that persist trace data without weakening realtime monitoring logic

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_decision_engine.py tests/test_decision_logger.py tests/test_main.py -k "llm or scenario_match" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/brain/decision_engine.py src/decision_logging/decision_logger.py src/db.py src/main.py tests/test_decision_engine.py tests/test_decision_logger.py tests/test_main.py
git commit -m "feat: persist dashboard llm traces"
```

### Task 2: Decision API Filter RED

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `src/dashboard/app.py`
- Test: `tests/test_dashboard.py`

**Step 1: Write the failing test**

Add tests that assert `/api/decisions` can:
- filter by `market=all`, `action`, `session_id`, `stock_code`, `min_confidence`
- filter by `from_date` / `to_date`
- filter by `matched_only`
- return `markets` / `sessions` metadata and trace fields

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard.py -k "decisions" -v`
Expected: FAIL because the endpoint only supports `market` and `limit`.

**Step 3: Write minimal implementation**

Modify: `src/dashboard/app.py`

Implement:
- richer `/api/decisions` query params
- SQL filter assembly with parameter binding
- response metadata and trace fields

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard.py -k "decisions" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/dashboard/app.py tests/test_dashboard.py
git commit -m "feat: add dashboard decision filters"
```

### Task 3: Dashboard UI RED

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `src/dashboard/static/index.html`
- Test: `tests/test_dashboard.py`

**Step 1: Write the failing test**

Add assertions that served HTML contains:
- decision filter controls
- `LLM request` / `LLM response` trace labels
- collapsed `Diagnostics` section instead of always-open raw panels

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard.py -k "index or html" -v`
Expected: FAIL because the current HTML still renders the legacy layout.

**Step 3: Write minimal implementation**

Modify: `src/dashboard/static/index.html`

Implement:
- filter bar + result summary + decision detail layout
- expandable trace details per decision
- collapsed diagnostics section for playbook / scorecard / scenarios / context

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard.py -k "index or html" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/dashboard/static/index.html tests/test_dashboard.py
git commit -m "feat: refocus dashboard on decision history"
```

### Task 4: Final Validation and Docs

**Files:**
- Modify: `docs/commands.md`
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-25-issue-853-dashboard-cleanup-design.md`
- Modify: `docs/plans/2026-03-25-issue-853-dashboard-cleanup.md`
- Test: `tests/test_decision_engine.py`
- Test: `tests/test_decision_logger.py`
- Test: `tests/test_dashboard.py`
- Test: `tests/test_main.py`

**Step 1: Run targeted tests**

Run: `pytest tests/test_decision_engine.py tests/test_decision_logger.py tests/test_dashboard.py tests/test_main.py -k "dashboard or llm or scenario_match or runtime_session_id" -v`
Expected: PASS

**Step 2: Run repo checks for touched surface**

Run: `ruff check src/ tests/`
Expected: PASS

**Step 3: Sync docs**

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 4: Commit**

```bash
git add docs/commands.md docs/plans/2026-03-25-issue-853-dashboard-cleanup-design.md docs/plans/2026-03-25-issue-853-dashboard-cleanup.md workflow/session-handover.md
git commit -m "docs: record dashboard cleanup plan"
```
