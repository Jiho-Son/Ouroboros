# Market Close Composite Cache Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** realtime market close 뒤에 닫힌 마켓의 composite-key runtime cache stale 엔트리가 남지 않도록 close helper 를 보강한다.

**Architecture:** `src/main.py` 의 realtime market-close cleanup helper 를 확장해 `buy_cooldown` 과 `sell_resubmit_counts` prefix pruning 을 같이 수행한다. metadata 누락 시에는 `buy_cooldown` 만 정리하고, `sell_resubmit_counts` 는 exchange metadata 부재로 의도적으로 유지한다는 사실을 log/comment/test 로 고정한다.

**Tech Stack:** Python, asyncio, pytest, unittest.mock, Markdown workpad/docs

---

### Task 1: Close-path composite-key 누락을 failing test 로 재현

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Write the failing tests**

- `_handle_realtime_market_closures()` 정상 close test 에 `buy_cooldown` 과
  `sell_resubmit_counts` fixture 를 추가한다.
- metadata 누락 test 에 `buy_cooldown` cleanup 과
  `sell_resubmit_counts` intentional retain expectation 을 추가한다.
- close failure test 에 exception 뒤에도 composite-key cleanup 결과가 유지되는지
  검증한다.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main.py -k "handle_realtime_market_closures and (cache or unknown_market or close_failure)" -v`

Expected: FAIL because current close helper does not touch composite-key dicts.

### Task 2: composite-key cleanup helper 를 최소 구현

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Implement market close pruning helper**

- `buy_cooldown` 에서 `{market_code}:` prefix 를 제거한다.
- `sell_resubmit_counts` 에서 `{exchange_code}:` 와 `BUY:{exchange_code}:`
  prefix 를 제거한다.
- metadata 가 없으면 `sell_resubmit_counts` 를 유지하고 이유를 warning/comment 로
  남긴다.

**Step 2: Wire helper into realtime close cleanup**

- `_clear_realtime_market_runtime_state()` 또는 동등 helper 가 composite-key dict 를
  함께 정리하도록 연결한다.
- 정상 close, metadata 누락, `_handle_market_close()` 예외 뒤 모두 같은 helper 를
  지나가도록 유지한다.

**Step 3: Re-run targeted tests**

Run: `pytest tests/test_main.py -k "handle_realtime_market_closures" -v`

Expected: PASS

### Task 3: Validation and workpad refresh

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: Linear workpad comment

**Step 1: Run touched-surface checks**

Run: `pytest tests/test_main.py -k "handle_realtime_market_closures or closes_removed_market_while_other_market_stays_open" -v`

Expected: PASS

**Step 2: Run lint**

Run: `ruff check src/main.py tests/test_main.py workflow/session-handover.md docs/plans/2026-03-28-oor-871-market-close-composite-cache-design.md docs/plans/2026-03-28-oor-871-market-close-composite-cache.md`

Expected: PASS

**Step 3: Refresh Linear workpad**

- reproduction command/result
- pull skill evidence
- final validation commands
- metadata-missing intentional retain rationale
