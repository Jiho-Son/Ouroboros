# Recent SELL Guard Helper Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** realtime BUY 경로와 daily BUY 경로가 동일 recent SELL guard helper를 사용하도록 리팩터링한다.

**Architecture:** `src/main.py` 에 공용 recent SELL guard helper를 추가하고, helper가 HOLD 전환에 필요한 rationale/log payload를 한 번에 계산한다. 기존 pure helper인 `_resolve_recent_sell_guard_window_seconds()` 와 `_should_block_buy_above_recent_sell()` 는 그대로 재사용하고, 두 BUY 경로는 새 helper만 호출하도록 정리한다.

**Tech Stack:** Python, pytest, unittest.mock, Ruff

---

### Task 1: 현재 recent SELL guard 계약을 failing test로 고정

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

- `src.main._apply_recent_sell_guard()` 가 block/non-block 결과를 일관되게 반환한다고 가정한 테스트를 추가한다.
- realtime/daily 경로 테스트에서 rationale 문자열을 더 엄격하게 확인한다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k 'apply_recent_sell_guard or suppresses_buy_above_recent_sell_price' -q`

Expected: helper 미구현으로 인한 import/attribute failure 또는 새 기대 계약 불일치 FAIL

### Task 2: 공용 helper 최소 구현

**Files:**
- Modify: `src/main.py`
- Modify: `tests/test_main.py`

**Step 1: Write minimal implementation**

- recent SELL guard 공용 helper를 추가한다.
- helper 이름은 `_apply_recent_sell_guard()` 로 둔다.
- helper가 최신 SELL 조회, 윈도우 계산, pure guard 판정, rationale/log payload 생성을 담당하게 한다.
- `_evaluate_trading_cycle_decision` 과 `_process_daily_session_stock` 에서 중복 블록을 제거하고 helper 호출로 대체한다.

**Step 2: Run targeted tests**

Run: `pytest tests/test_main.py -k 'apply_recent_sell_guard or suppresses_buy_above_recent_sell_price' -q`

Expected: PASS

### Task 3: 회귀 검증과 문서 정리

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-20-issue-831-recent-sell-guard-helper-design.md`
- Modify: `docs/plans/2026-03-20-issue-831-recent-sell-guard-helper.md`
- Modify: `src/main.py`
- Modify: `tests/test_main.py`

**Step 1: Run broader validation**

Run: `pytest tests/test_main.py -k 'suppresses_buy_above_recent_sell_price or recent_sell_guard_ or apply_recent_sell_guard' -q`

Expected: PASS

Run: `ruff check src/main.py tests/test_main.py workflow/session-handover.md docs/plans/2026-03-20-issue-831-recent-sell-guard-helper-design.md docs/plans/2026-03-20-issue-831-recent-sell-guard-helper.md`

Expected: PASS

**Step 2: Record evidence**

- workpad `Validation` 에 실제 실행 커맨드와 결과를 체크한다.
- workpad `Notes` 에 helper 추출과 문자열 일관성 확인 결과를 남긴다.
