# OOR-864 Session Tracking Store Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** per-market runtime tracking 을 session-scoped store 로 재구성해 scanner universe / scan state reset policy 와 diagnostics contract 를 명시한다.

**Architecture:** `src/core/market_tracking.py` 에 thread-safe `MarketTrackingStore` 를 추가하고, `src/main.py` realtime loop 는 raw dict 대신 store 를 single source of truth 로 사용한다. `src/dashboard/app.py` 는 optional runtime diagnostics provider 를 받아 `/api/status` 에 per-market tracking summary 를 포함한다.

**Tech Stack:** Python 3.12, asyncio, FastAPI, pytest, ruff

---

### Task 1: Tracking Store contract 를 failing test 로 고정

**Files:**
- Create: `src/core/market_tracking.py`
- Create: `tests/test_market_tracking.py`

**Step 1: Write the failing tests**

Add tests that prove:
- same-session `ensure_market_session()` 는 기존 state 를 유지한다
- new-session rollover 는 `active_stocks`, `scan_candidates`, `last_scan_monotonic` 를 비운다
- `clear_market()` 는 target market state 만 제거한다
- `runtime_fallback_stocks()` 는 session mismatch 에서 빈 리스트를 반환한다

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_market_tracking.py -v`
Expected: FAIL because the tracking store module does not exist yet.

**Step 3: Write minimal implementation**

- add state/snapshot/store dataclasses
- keep mutations thread-safe
- implement summary payload generation for dashboard/logging

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_market_tracking.py -v`
Expected: PASS

### Task 2: Realtime loop 를 store 기반으로 전환

**Files:**
- Modify: `src/main.py`
- Modify: `tests/test_main.py`
- Test: `tests/test_market_tracking.py`

**Step 1: Write the failing tests**

Extend realtime tests so they prove:
- session transition 뒤 다음 overseas universe build 는 이전 session runtime universe 를 받지 않는다
- close cleanup 은 closed market state 만 제거하고 다른 market state 는 유지한다
- scan result 기록이 `active_stocks`, `scan_candidates`, `last_scan_time` 를 store 안에서 함께 갱신한다

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "session_transition_clears_tracking_cache or handle_realtime_market_closures" -v`
Expected: FAIL once tests are rewritten to require store-based lifecycle behavior.

**Step 3: Write minimal implementation**

- instantiate `MarketTrackingStore` in `run()`
- replace raw dict access with store reads/writes
- wire close/session transition handlers to explicit clear/rollover methods
- switch overseas fallback lookup to session-aware accessor

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "session_transition_clears_tracking_cache or handle_realtime_market_closures or realtime_mode_reconciles_close_and_session_transition_independently" -v`
Expected: PASS

### Task 3: Runtime diagnostics 를 status API 와 로그에 추가

**Files:**
- Modify: `src/dashboard/app.py`
- Modify: `src/main.py`
- Modify: `tests/test_dashboard.py`

**Step 1: Write the failing tests**

Add tests that prove:
- `/api/status` market payload includes `runtime_tracking` when a provider is injected
- `runtime_tracking.session_id`, `active_stocks`, `candidate_count`, `last_scan_age_seconds` are exposed
- provider 가 없을 때 기존 status endpoint 는 계속 응답한다

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard.py -k "runtime_tracking or status_endpoint_returns_market_operating_summary" -v`
Expected: FAIL because the endpoint has no runtime diagnostics field.

**Step 3: Write minimal implementation**

- let `create_dashboard_app()` accept an optional diagnostics provider
- merge provider payload into `/api/status`
- add concise runtime summary logs after scan update / session rollover / close cleanup

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard.py -k "runtime_tracking or status_endpoint_returns_market_operating_summary" -v`
Expected: PASS

### Task 4: Verification and documentation sync

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-27-oor-864-session-tracking-store-design.md`
- Modify: `docs/plans/2026-03-27-oor-864-session-tracking-store.md`

**Step 1: Run targeted verification**

Run: `pytest tests/test_market_tracking.py tests/test_main.py tests/test_dashboard.py -k "tracking or runtime_tracking or session_transition_clears_tracking_cache or handle_realtime_market_closures" -v`
Expected: PASS

**Step 2: Run repo checks**

Run: `ruff check src/ tests/`
Expected: PASS

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 3: Run completion verification**

Run: `pytest -v --cov=src --cov-report=term-missing`
Expected: PASS

**Step 4: Commit**

```bash
git add src/core/market_tracking.py src/main.py src/dashboard/app.py tests/test_market_tracking.py tests/test_main.py tests/test_dashboard.py docs/plans/2026-03-27-oor-864-session-tracking-store*.md workflow/session-handover.md
git commit -m "feat: add per-market session tracking store"
```
