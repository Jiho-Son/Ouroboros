# OOR-859 US Session DST Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** US session classification 이 `America/New_York` 현지 시각 기준으로 동작해 DST 전환 전후에도 `US_PRE`/`US_REG`/`US_AFTER` 경계와 extended-session 판정이 일관되게 유지되도록 한다.

**Architecture:** `src/core/order_policy.py` 에서 US 세션 분류를 고정 KST 윈도우 비교에서 market-local 시각 비교로 바꾼다. regular session 경계는 `MarketInfo.open_time`/`close_time` 를 그대로 재사용하고, extended-session regression 은 `tests/test_order_policy.py` 와 `tests/test_market_schedule.py` 로 고정한다.

**Tech Stack:** Python 3.12, `zoneinfo`, `pytest`, `ruff`

---

### Task 1: DST 회귀 테스트를 먼저 추가한다

**Files:**
- Modify: `tests/test_order_policy.py`
- Modify: `tests/test_market_schedule.py`

**Step 1: Write the failing test**

추가할 테스트:
- `2026-03-09 13:30 UTC` (`09:30 EDT`) 에 `classify_session_id()` 가 `US_REG` 를 반환해야 한다.
- `2026-03-09 20:00 UTC` (`16:00 EDT`) 에 `classify_session_id()` 가 `US_AFTER` 를 반환해야 한다.
- `2026-11-02 14:30 UTC` (`09:30 EST`) 에 `classify_session_id()` 가 `US_REG` 를 유지해야 한다.
- `get_open_markets(include_extended_sessions=True)` 가 `2026-03-09 21:30 UTC` (`17:30 EDT`) 에 빈 리스트를 반환해야 한다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_order_policy.py tests/test_market_schedule.py -k "dst or extended_session" -v`
Expected: DST-start 관련 새 회귀 테스트가 FAIL 한다.

### Task 2: US classifier 를 local-market time 기준으로 수정한다

**Files:**
- Modify: `src/core/order_policy.py`
- Test: `tests/test_order_policy.py`
- Test: `tests/test_market_schedule.py`

**Step 1: Write minimal implementation**

구현 항목:
- KR 세션 분류는 현행 의미를 유지하되 `local_now.time()` 기준으로 정리한다.
- US 세션 분류는 `local_now` 와 `market.open_time`/`market.close_time` 를 사용한다.
- US local boundaries:
  - `US_DAY`: `20:00 <= local < 04:00`
  - `US_PRE`: `04:00 <= local < 09:30`
  - `US_REG`: `09:30 <= local < 16:00`
  - `US_AFTER`: `16:00 <= local < 17:00`
  - else `US_OFF`
- `schedule.py` 의 extended-session lookup 는 기존 lazy import 구조를 유지하고, classifier 변경만으로 같은 현지 시간축을 공유하게 만든다.

**Step 2: Run targeted tests**

Run: `pytest tests/test_order_policy.py tests/test_market_schedule.py -k "dst or extended_session" -v`
Expected: 추가한 회귀 테스트와 관련 기존 테스트가 PASS 한다.

### Task 3: 문서/검증/추적 산출물을 정리한다

**Files:**
- Modify: `docs/architecture.md`
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-27-oor-859-us-session-dst-design.md`
- Modify: `docs/plans/2026-03-27-oor-859-us-session-dst.md`

**Step 1: Update docs**

반영 항목:
- `OrderPolicy` 설명을 "KST clock" 에서 "market-local clock with DST-aware US timezone" 로 갱신한다.

**Step 2: Run scope verification**

Run: `pytest tests/test_order_policy.py tests/test_market_schedule.py -v`
Expected: PASS

Run: `ruff check src/ tests/`
Expected: PASS

**Step 3: Update tracking artifacts**

반영 항목:
- Linear workpad 에 reproduction, root cause, validation, pull evidence를 체크리스트와 함께 반영한다.
- PR 생성 시 관련 validation 결과와 docs update 이유를 본문에 반영한다.
