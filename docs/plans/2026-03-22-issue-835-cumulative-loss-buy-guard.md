# Cumulative Loss BUY Guard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 최근 N일 self-market scorecard 기반 누적 손실/저승률 구간에서 planner playbook 의 신규 BUY 를 deterministic 하게 차단한다.

**Architecture:** `src/strategy/pre_market_planner.py` 에 최근 self-market scorecard window loader + guard evaluator + playbook post-processor 를 추가한다. `src/config.py` 에 guard threshold/action 설정을 추가하고, prompt 에 recent guard section 을 노출해 LLM 과 최종 playbook 이 같은 guard 상태를 공유하도록 만든다.

**Tech Stack:** Python, pydantic-settings, pytest, unittest.mock, Ruff

---

### Task 1: failing test 로 현재 계약을 고정

**Files:**
- Modify: `tests/test_pre_market_planner.py`
- Modify: `tests/test_config.py`

**Step 1: Write the failing test**

- 최근 3일 연속 손실 scorecard 입력에서 `generate_playbook()` 결과 BUY scenario 가 제거되어야 한다는 테스트를 추가한다.
- `SCORECARD_BUY_GUARD_ACTION=\"defensive\"` 일 때 `market_outlook` 과 global rule 이 defensive 쪽으로 보강되는 테스트를 추가한다.
- prompt 에 recent guard section/reason 이 들어가는 테스트를 추가한다.
- 설정 validation/default 테스트를 추가한다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_pre_market_planner.py -k 'scorecard_buy_guard or recent_guard' -v`

Expected: guard helper/설정 미구현으로 FAIL

Run: `pytest tests/test_config.py -k 'scorecard_buy_guard' -v`

Expected: 새 설정 필드 부재 또는 validation 기대 불일치로 FAIL

### Task 2: 최소 구현으로 guard 추가

**Files:**
- Modify: `src/config.py`
- Modify: `src/strategy/pre_market_planner.py`
- Modify: `tests/test_pre_market_planner.py`
- Modify: `tests/test_config.py`

**Step 1: Write minimal implementation**

- `src/config.py` 에 scorecard BUY guard 설정 필드를 추가한다.
- `PreMarketPlanner` 에 최근 N일 self-market scorecard 조회 helper 를 추가한다.
- 누적 손실/평균 승률/연속 손실 기준 guard evaluator 를 추가한다.
- `_build_prompt()` 에 recent guard section 과 BUY 금지 지시를 추가한다.
- parse 이후 playbook post-processing 으로 BUY 제거 또는 defensive downgrade 를 강제한다.

**Step 2: Run targeted tests**

Run: `pytest tests/test_pre_market_planner.py -k 'scorecard_buy_guard or recent_guard' -v`

Expected: PASS

Run: `pytest tests/test_config.py -k 'scorecard_buy_guard' -v`

Expected: PASS

### Task 3: 관련 회귀와 문서 검증

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-22-issue-835-cumulative-loss-buy-guard-design.md`
- Modify: `docs/plans/2026-03-22-issue-835-cumulative-loss-buy-guard.md`
- Modify: `src/config.py`
- Modify: `src/strategy/pre_market_planner.py`
- Modify: `tests/test_pre_market_planner.py`
- Modify: `tests/test_config.py`

**Step 1: Run broader validation**

Run: `pytest tests/test_pre_market_planner.py -v`

Expected: PASS

Run: `pytest tests/test_config.py -v`

Expected: PASS

Run: `ruff check src/config.py src/strategy/pre_market_planner.py tests/test_config.py tests/test_pre_market_planner.py workflow/session-handover.md docs/plans/2026-03-22-issue-835-cumulative-loss-buy-guard-design.md docs/plans/2026-03-22-issue-835-cumulative-loss-buy-guard.md`

Expected: PASS

Run: `python3 scripts/validate_docs_sync.py`

Expected: PASS

**Step 2: Record evidence**

- Linear workpad `Validation` 에 실제 실행 커맨드와 결과를 체크한다.
- workpad `Notes` 에 재현 스니펫, guard 적용 방식, pull/검증 결과를 남긴다.
