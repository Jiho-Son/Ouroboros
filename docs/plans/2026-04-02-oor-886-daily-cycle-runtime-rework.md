# OOR-886 Daily Cycle Runtime Rework Implementation Plan

**Goal:** late-start regular-session daily mode에서 `phase 5/6/1` 이 다음 post-close iteration으로 밀리는 이유를 runtime phase 로그 자체에 남기고, 회귀 테스트와 문서로 고정한다.

**Architecture:** scheduler cadence 자체는 유지한다. `src/main.py` 의 기존 regular-session follow-up helper를 재사용해 현재 batch가 마지막 regular-session 기회인 시장 목록을 계산하고, 이를 `phase=4` schedule log에 추가한다. 테스트는 late-start `KR` runtime 경로와 기존 daily lifecycle coverage를 함께 묶어 실제 defer semantics를 검증한다.

**Tech Stack:** Python 3.12, pytest, logging, Markdown docs

---

### Task 1: late-start defer semantics를 failing test로 고정

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

추가할 테스트:
- `KR` 시장이 late-start regular session (`2026-03-23 09:31 KST`) 에서 한 번 열린 뒤 다음 iteration에 닫히는 daily `run()` 경로를 시뮬레이션한다.
- 첫 batch의 `daily_cycle phase=4` 로그에 `last_regular_batch_markets=KR` 가 남는지 확인한다.
- 이어지는 closed iteration에서 `phase=5`, `phase=6`, `phase=1` 이 실제로 남는지 확인한다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "late_start_daily_cycle" -v`

Expected: FAIL because current `phase=4` log does not yet emit `last_regular_batch_markets`.

### Task 2: minimal runtime observability change 추가

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Add helper for last regular batch markets**

- 현재 열린 시장 중 default/catch-up next batch 이후 추가 regular-session batch가 더 이상 없는 시장 목록을 계산하는 helper를 추가하거나 기존 helper를 재사용 가능한 형태로 묶는다.

**Step 2: Wire the helper into `phase=4` logging**

- `schedule_next_batch` phase log에 `last_regular_batch_markets=<csv>` 필드를 추가한다.
- 해당 목록이 비어 있으면 필드를 생략해 기존 로그를 유지한다.

**Step 3: Re-run targeted test**

Run: `pytest tests/test_main.py -k "late_start_daily_cycle" -v`

Expected: PASS

### Task 3: daily lifecycle 문서 정합화

**Files:**
- Modify: `docs/architecture.md`

**Step 1: Update daily-mode observability note**

- late-start regular-session에서는 `phase=4` 가 마지막 regular-session batch임을 표시할 수 있고, `phase=5/6/1` 은 다음 post-close iteration에서 나타난다는 점을 짧게 기록한다.

### Task 4: Verify and prepare ticket artifacts

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-04-02-oor-886-daily-cycle-runtime-rework.md`
- Modify: `src/main.py`
- Modify: `tests/test_main.py`
- Modify: `docs/architecture.md`

**Step 1: Run targeted daily-cycle checks**

Run: `pytest tests/test_main.py -k "late_start_daily_cycle or run_daily_mode_waits_for_next_market_open or run_daily_mode_handles_market_close_review_and_logs_phases or run_daily_session_logs_phase_prepare_and_process" -v`

Expected: PASS

**Step 2: Run scoped static/doc checks**

Run: `ruff check src/main.py tests/test_main.py docs/architecture.md docs/plans/2026-04-02-oor-886-daily-cycle-runtime-rework.md workflow/session-handover.md`

Expected: PASS

**Step 3: Run docs sync validation**

Run: `python3 scripts/validate_docs_sync.py`

Expected: PASS

**Step 4: Run repo verification gate**

Run: `pytest -v --cov=src --cov-report=term-missing`

Expected: PASS
