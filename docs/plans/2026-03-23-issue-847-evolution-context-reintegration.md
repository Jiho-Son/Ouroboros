# OOR-847 Evolution Context Reintegration Plan

**Goal:** 진화 프롬프트에서 설계상 의도된 컨텍스트 레벨 입력 부재를 진단하고, 현재 `report` 기반 진화 구조에 맞는 재도입 경로를 문서로 고정한다.

**Architecture:** `PreMarketPlanner` 는 이미 `ContextSelector` 로 선택한 `L7/L6/L5` 데이터와 `scorecard_<market>` 를 프롬프트의 `Strategic Context` 블록에 주입한다. 이때 `ContextLayer` enum 자체는 `L1_LEGACY` 부터 `L7_REALTIME` 까지 장기→단기 순서로 정의돼 있지만, prompt selection 은 별도로 `L7 -> L6 -> L5` 우선순위로 조합된다. 반면 `EvolutionOptimizer` 는 현재 `Failure Patterns` 와 샘플 실패 거래만으로 recommendation JSON 을 생성하고 결과를 `L6_DAILY` 의 `evolution_<market>` 키에 저장한다. 재도입은 이 구조를 유지한 채, `scorecard_<market>`, 최근 `evolution_<market>` report, 시장 suffix 가 붙은 `L5/L4` 집계값, 대표적인 `decision_logger.context_snapshot` 샘플을 묶은 compact `evolution context bundle` 을 만들어 `generate_recommendation()` 에 주입하는 방식이 가장 안전하다. `ContextSelector.get_context_data()` 는 각 layer 에서 `get_latest_timeframe()` 로 최신 timeframe 하나를 고른 뒤 그 timeframe 의 key 전부를 읽기 때문에, 시장/일자 정렬이 필요한 진화 경로에는 그대로 재사용하기 어렵다.

**Tech Stack:** Python, sqlite-backed `ContextStore`, `DecisionLogger`, pytest, docs sync validator

---

## Diagnosis

- 재현 결과 플레이북 프롬프트는 `Strategic Context`, `L6_DAILY`, `L5_WEEKLY` 를 실제로 포함한다.
- 같은 재현에서 진화 프롬프트는 `Failure Patterns` 와 실패 거래 샘플만 포함하고 `Strategic Context` 나 `L6_DAILY` 는 포함하지 않는다.
- `git log -S"ContextSelector" -- src/evolution/optimizer.py` 결과가 비어 있어, 진화 경로는 최근 회귀보다 미구현 상태 지속으로 보는 편이 정확하다.
- 따라서 이번 선조치는 “플레이북도 안 쓰고 있다”가 아니라 “플레이북은 쓰고 있고, 진화만 설계 개념이 코드화되지 않았다”를 문서로 명확히 남기는 데 있다.

### Task 1: 진화 컨텍스트 부재를 테스트 계약으로 고정

**Files:**
- Modify: `tests/test_evolution.py`

**Step 1: Write the failing test**

- `generate_recommendation()` 가 시장/일자 정렬된 `evolution context bundle` 을 프롬프트에 포함해야 한다는 기대값을 추가한다.
- 최소 포함 항목은 `scorecard_<market>`, 최근 `evolution_<market>` 요약, 시장 suffix 가 붙은 `weekly_pnl_<market>` 또는 `avg_confidence_<market>`, 실패 decision 의 대표 `context_snapshot` 이다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_evolution.py -k 'evolution_context_bundle' -v`
Expected: FAIL because current prompt only contains `Failure Patterns` and sample failures.

**Step 3: Commit**

```bash
git add tests/test_evolution.py
git commit -m "test: define evolution context bundle contract"
```

### Task 2: 시장/일자 정렬된 evolution context bundle 추가

**Files:**
- Modify: `src/evolution/optimizer.py`
- Create: `src/evolution/context_bundle.py`
- Test: `tests/test_evolution.py`

**Step 1: Write the minimal implementation**

- `scorecard_<market>` 와 `evolution_<market>` 를 `L6_DAILY` 에서 명시적 timeframe 으로 조회한다.
- `L5/L4` 는 `ContextStore.get_all_contexts(layer, timeframe)` 로 특정 timeframe 만 읽고, `weekly_pnl_<market>`, `avg_confidence_<market>` 같은 시장 suffix 키만 필터링한다.
- 실패 decision 들의 `context_snapshot` 에서 반복적으로 등장하는 핵심 필드만 추려 compact summary 로 만든다.
- 이 번들을 prompt 본문에 `## Evolution Context` 섹션으로 주입하되, 최종 출력 계약은 지금처럼 JSON object 하나만 유지한다.

**Step 2: Run targeted tests**

Run: `pytest tests/test_evolution.py -k 'evolution_context_bundle or generate_recommendation' -v`
Expected: PASS

**Step 3: Commit**

```bash
git add src/evolution/context_bundle.py src/evolution/optimizer.py tests/test_evolution.py
git commit -m "feat(evolution): inject market-scoped context bundle"
```

### Task 3: 문서와 아키텍처 설명 동기화

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/context-tree.md`
- Modify: `docs/plans/2026-03-23-issue-847-evolution-context-reintegration.md`

**Step 1: Update docs**

- 플레이북은 이미 컨텍스트를 사용 중이고, 진화는 아직 미사용이라는 현재 사실을 명시한다.
- `ContextSelector` 가 최신 레이어 전체를 읽는 helper 이므로 시장/일자 정렬이 필요한 진화 경로에는 바로 쓰지 않는다는 원칙을 문서화한다.

**Step 2: Run docs validation**

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 3: Commit**

```bash
git add docs/architecture.md docs/context-tree.md docs/plans/2026-03-23-issue-847-evolution-context-reintegration.md
git commit -m "docs: diagnose evolution context reintegration path"
```

### Task 4: 최종 검증

**Files:**
- Verify only

**Step 1: Run focused regression suite**

Run: `pytest tests/test_pre_market_planner.py -k 'generate_playbook_uses_strategic_context_selector or prompt_contains_context_data' -v`
Expected: PASS

Run: `pytest tests/test_evolution.py -k 'evolution_context_bundle or generate_recommendation' -v`
Expected: PASS

**Step 2: Run docs sync**

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS
