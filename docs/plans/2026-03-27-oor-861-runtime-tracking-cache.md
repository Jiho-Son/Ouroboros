# OOR-861 Runtime Tracking Cache Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** session transition 이후 stale runtime tracking cache가 다음 scanner universe에 누수되지 않도록 막는다.

**Architecture:** realtime loop에 playbook session transition 처리와 동일한 수준의 tracking cache reset helper를 추가한다. market close cleanup은 유지하고, session transition 경로에서만 `active_stocks`, `scan_candidates`, `last_scan_time`을 비운 뒤 다음 rescan이 DB/holdings 기반 fallback만 사용하도록 만든다.

**Tech Stack:** Python, asyncio, pytest, unittest.mock

---

### Task 1: Red 테스트로 session transition 누수를 고정

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Write the failing test**

`run()` 회귀 테스트를 추가해 `US_PRE -> US_REG` 전환 시 첫 번째 cycle이 남긴 `active_stocks`
값이 두 번째 cycle의 `build_overseas_symbol_universe()`에 전달되지 않아야 한다는 기대를 적는다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "session_transition_clears_tracking_cache" -v`
Expected: FAIL because the second builder call still sees the previous session's `active_stocks`.

### Task 2: session transition tracking cache reset 구현

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Add helper-level red test**

session transition 여부에 따라 tracking cache reset 필요 여부와 실제 dict cleanup을 검증하는
helper 테스트를 추가한다.

**Step 2: Implement minimal helper**

`active_stocks`, `scan_candidates`, `last_scan_time`에 대해 session transition reset helper를
추가하고 realtime loop의 `session_changed` 처리 지점에서 호출한다.

**Step 3: Re-run targeted tests**

Run: `pytest tests/test_main.py -k "tracking_cache or session_transition_clears_tracking_cache" -v`
Expected: PASS

### Task 3: Broader validation and handoff

**Files:**
- Modify: `workflow/session-handover.md`

**Step 1: Run regression slice**

Run: `pytest tests/test_main.py -k "realtime_market_closures or session_transition_clears_tracking_cache or refresh_cached_playbook" -v`
Expected: PASS

**Step 2: Run lint on touched files**

Run: `ruff check src/main.py tests/test_main.py workflow/session-handover.md`
Expected: PASS

**Step 3: Update workpad and summarize evidence**

Record reproduction, fix scope, and validation commands in the existing Linear workpad comment.
