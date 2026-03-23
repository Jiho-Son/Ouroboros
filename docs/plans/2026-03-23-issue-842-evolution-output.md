# OOR-842 Evolution Output Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 진화 루프가 `.py` 파일을 만들지 않고, 실패 분석 기반 recommendation report 를 context 에 저장하도록 바꾼다.

**Architecture:** `EvolutionOptimizer` 는 Python code generation 대신 JSON recommendation generation 을 수행하고, report 를 `ContextStore` 를 통해 `L6_DAILY` 에 기록한다. `src/main.py` 는 저장된 report 의 `context_key` 를 Telegram 알림에 노출하고, 문서/테스트는 이 새 계약을 기준으로 맞춘다.

**Tech Stack:** Python, pytest, sqlite-backed `ContextStore`, AsyncMock, Linear workpad workflow

---

### Task 1: Failing tests 로 새 계약 고정

**Files:**
- Modify: `tests/test_evolution.py`
- Modify: `tests/test_main.py`

**Step 1: Write the failing tests**

- `generate_strategy()` 가 `.py` 파일 대신 context-backed report 를 반환하도록 기대값을 바꾼다.
- `_run_evolution_loop()` 알림이 `branch` 대신 `context_key` 를 사용하도록 기대값을 추가한다.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_evolution.py tests/test_main.py -k "evolution or run_evolution_loop" -v`
Expected: 기존 `.py` 파일/`branch` 계약 때문에 FAIL

**Step 3: Commit**

Run:

```bash
git add tests/test_evolution.py tests/test_main.py
git commit -m "test: redefine evolution output contract"
```

### Task 2: Evolution optimizer 를 report 저장 방식으로 전환

**Files:**
- Modify: `src/evolution/optimizer.py`
- Modify: `src/context/store.py` (only if helper is truly needed)

**Step 1: Write the minimal implementation**

- Python strategy template/file write 경로를 제거한다.
- recommendation JSON prompt / parser / context storage helper 를 추가한다.
- `evolve()` 가 `market_code`, `market_date` optional input 을 받아 `context_key` 포함 report 를 반환하게 바꾼다.

**Step 2: Run targeted tests**

Run: `pytest tests/test_evolution.py -k "evolution" -v`
Expected: PASS

**Step 3: Commit**

Run:

```bash
git add src/evolution/optimizer.py tests/test_evolution.py
git commit -m "refactor: store evolution recommendations in context"
```

### Task 3: Market-close 알림 경로 동기화

**Files:**
- Modify: `src/main.py`
- Modify: `tests/test_main.py`

**Step 1: Write the minimal implementation**

- `_run_evolution_loop()` 가 optimizer 에 `market_code`, `market_date` 를 넘기고, Telegram 메시지에 `Context Key` 를 표시하도록 바꾼다.

**Step 2: Run targeted tests**

Run: `pytest tests/test_main.py -k "handle_market_close or run_evolution_loop" -v`
Expected: PASS

**Step 3: Commit**

Run:

```bash
git add src/main.py tests/test_main.py
git commit -m "refactor: notify stored evolution report context"
```

### Task 4: 문서 동기화

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/skills.md`
- Modify: `docs/plans/2026-03-23-issue-842-evolution-output-design.md` (if wording needs alignment)

**Step 1: Update docs**

- evolution 설명을 “strategy file 생성”에서 “recommendation report 저장”으로 바꾼다.

**Step 2: Run docs validation**

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 3: Commit**

Run:

```bash
git add docs/architecture.md docs/skills.md docs/plans/2026-03-23-issue-842-evolution-output-design.md docs/plans/2026-03-23-issue-842-evolution-output.md
git commit -m "docs: realign evolution output documentation"
```

### Task 5: Final verification

**Files:**
- Verify only

**Step 1: Run focused regression suite**

Run: `pytest tests/test_evolution.py tests/test_main.py -k "evolution or handle_market_close or run_evolution_loop" -v`
Expected: PASS

**Step 2: Run lint on touched code**

Run: `ruff check src/evolution/optimizer.py src/main.py tests/test_evolution.py tests/test_main.py docs/architecture.md docs/skills.md`
Expected: PASS

**Step 3: Run docs sync**

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS
