# OOR-848 Evolution Context Bundle Reinjection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `EvolutionOptimizer.generate_recommendation()` 프롬프트에 market-scoped `Evolution Context` 를 재주입하면서 기존 recommendation JSON 계약과 저장 경로를 유지한다.

**Architecture:** `generate_recommendation()` 직전에 실패 거래에서 market/date 를 추출해 `L6_DAILY` 의 `scorecard_<market>` 및 최신 `evolution_<market>` report, `L5_WEEKLY` 의 market-scoped 집계값, 실패 `context_snapshot` compact summary 를 하나의 serializable bundle 로 만든다. 프롬프트에는 `Failure Patterns` 앞에 별도 `Evolution Context` 섹션을 추가하고, 기존 응답 파싱/검증 로직은 그대로 유지한다.

**Tech Stack:** Python, sqlite-backed `ContextStore`, `DecisionLogger`, pytest

---

### Task 1: 재주입 계약 red 테스트 추가

**Files:**
- Modify: `tests/test_evolution.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_generate_recommendation_injects_evolution_context_bundle(...):
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_evolution.py -k 'evolution_context_bundle' -v`
Expected: FAIL because current prompt has no `Evolution Context` section.

### Task 2: market-scoped bundle 생성기 구현

**Files:**
- Create: `src/evolution/context_bundle.py`
- Modify: `src/evolution/optimizer.py`
- Test: `tests/test_evolution.py`

**Step 1: Write minimal implementation**

- 실패 거래에서 market/date 를 정규화한다.
- `L6_DAILY` 에서 `scorecard_<market>` 와 최신 `evolution_<market>` report 를 읽는다.
- `L5_WEEKLY` 에서 `_<market>` suffix key 만 남긴다.
- `context_snapshot` 을 flatten 해서 repeated/representative clue summary 를 만든다.
- prompt 에 `## Evolution Context` 섹션을 추가한다.

**Step 2: Run focused tests**

Run: `pytest tests/test_evolution.py -k 'evolution_context_bundle or generate_recommendation' -v`
Expected: PASS

### Task 3: planner 회귀와 문서 영향 확인

**Files:**
- Verify only

**Step 1: Run planner regression**

Run: `pytest tests/test_pre_market_planner.py -k 'generate_playbook_uses_strategic_context_selector or prompt_contains_context_data' -v`
Expected: PASS

**Step 2: Run docs sync if touched**

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS when docs changed
