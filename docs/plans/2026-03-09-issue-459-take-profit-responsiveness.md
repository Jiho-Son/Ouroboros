# Issue #459 Favorable Exit Responsiveness Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve favorable-exit responsiveness by letting websocket highs update staged-exit peak tracking without moving exit decisions out of the existing trading-cycle path.

**Architecture:** Keep `src/strategy/exit_manager.py` as the single staged-exit authority. Add a narrow realtime peak update helper, wire it into the KR websocket/hard-stop path as data input only, and verify via regression tests that trailing-stop behavior reacts to the cached peak earlier without changing hard-stop ownership.

**Tech Stack:** Python 3.12, pytest, asyncio, existing realtime trading loop and staged-exit helpers

---

### Task 1: Add failing tests for runtime peak updates

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Write the failing test**

```python
def test_update_runtime_peak_only_raises_cached_peak() -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "runtime_peak" -v`
Expected: FAIL because the exported helper does not exist yet.

**Step 3: Write minimal implementation**

```python
def update_runtime_exit_peak(...):
    ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "runtime_peak" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_main.py src/strategy/exit_manager.py
git commit -m "test: add websocket runtime peak regression TASK-CODE-002"
```

### Task 2: Add realtime integration for favorable-exit peak hints

**Files:**
- Modify: `src/main.py`
- Modify: `src/strategy/exit_manager.py`
- Modify: `tests/test_main.py`

**Step 1: Write the failing test**

```python
async def test_kr_websocket_peak_hint_updates_runtime_exit_peak() -> None:
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "websocket_peak_hint" -v`
Expected: FAIL because the realtime path does not publish favorable-exit highs yet.

**Step 3: Write minimal implementation**

```python
update_runtime_exit_peak(...)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "websocket_peak_hint" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/main.py src/strategy/exit_manager.py tests/test_main.py
git commit -m "feat: wire websocket peak hints into staged exit TASK-CODE-002"
```

### Task 3: Document the favorable-exit peak hint behavior and verify regression suite

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/plans/2026-03-09-issue-459-take-profit-responsiveness-design.md`
- Modify: `docs/plans/2026-03-09-issue-459-take-profit-responsiveness.md`

**Step 1: Update the docs**

```md
- Websocket highs may seed staged-exit peak tracking, but exit decisions remain in trading_cycle.
```

**Step 2: Run focused verification**

Run: `pytest tests/test_main.py -k "runtime_peak or websocket_peak_hint" -v`
Expected: PASS

**Step 3: Run project verification**

Run: `pytest -q`
Expected: PASS

**Step 4: Run docs verification**

Run: `python3 scripts/validate_ouroboros_docs.py`
Expected: PASS

**Step 5: Commit**

```bash
git add docs/architecture.md docs/plans/2026-03-09-issue-459-take-profit-responsiveness-design.md docs/plans/2026-03-09-issue-459-take-profit-responsiveness.md
git commit -m "docs: record issue 459 favorable exit design TASK-CODE-002"
```
