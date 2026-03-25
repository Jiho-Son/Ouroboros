# OOR-857 Live Daily Hard-Stop Monitoring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** live + `TRADE_MODE=daily` runtime 에서도 supported held positions 를 realtime websocket hard-stop monitoring 으로 계속 보호한다.

**Architecture:** realtime hard-stop startup eligibility 를 entry cadence 와 분리하고, daily evaluation path 에도 existing `RealtimeHardStopMonitor` / `KISWebSocketClient` plumbing 을 전달한다. daily HOLD evaluation 이 realtime path 와 동일한 `_sync_realtime_hard_stop_monitor()` 계약을 사용하도록 맞춰 startup, register, subscribe, remove behavior 를 한 곳에서 유지한다.

**Tech Stack:** Python, asyncio, pytest, ruff, Linear workpad, repository docs

---

### Task 1: Startup contract red test

**Files:**
- Modify: `tests/test_main.py`
- Modify: `src/main.py`

**Step 1: Write the failing test**

- live + `TRADE_MODE=daily` + `REALTIME_HARD_STOP_ENABLED=True` + `ENABLED_MARKETS=US`
  settings 에서 realtime hard-stop startup predicate/helper 가 `True` 를 반환해야
  하는 unit test 를 추가한다.

**Step 2: Run test to verify it fails**

Run: `pytest -v tests/test_main.py -k 'realtime_hard_stop and daily and startup'`
Expected: FAIL because current startup guard requires `TRADE_MODE == "realtime"`.

**Step 3: Write minimal implementation**

- startup eligibility helper 를 추가하고 main bootstrap guard 를 helper 기반으로
  전환한다.

**Step 4: Run test to verify it passes**

Run: `pytest -v tests/test_main.py -k 'realtime_hard_stop and daily and startup'`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_main.py src/main.py
git commit -m "fix: enable live daily hard-stop startup"
```

### Task 2: Daily path sync red test

**Files:**
- Modify: `tests/test_main.py`
- Modify: `src/main.py`

**Step 1: Write the failing test**

- daily HOLD evaluation 경로가 open position + staged exit evidence 를 가진 supported
  market symbol 에 대해 `_sync_realtime_hard_stop_monitor()` 를 호출하고,
  monitor/client 를 `_process_daily_session_stock()` 까지 전달하는 test 를 추가한다.

**Step 2: Run test to verify it fails**

Run: `pytest -v tests/test_main.py -k 'daily and hard_stop and sync'`
Expected: FAIL because daily path currently has no monitor/client plumbing.

**Step 3: Write minimal implementation**

- `run_daily_session()`, `_run_daily_session_market()`,
  `_process_daily_session_stock()` 시그니처에 optional monitor/client parameter 를
  추가한다.
- HOLD branch 에서 `_sync_realtime_hard_stop_monitor()` 를 호출한다.

**Step 4: Run test to verify it passes**

Run: `pytest -v tests/test_main.py -k 'daily and hard_stop and sync'`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_main.py src/main.py
git commit -m "fix: wire daily hard-stop monitor sync"
```

### Task 3: Docs and focused validation

**Files:**
- Modify: `docs/live-trading-checklist.md`
- Modify: `docs/architecture.md`
- Modify: `tests/test_main.py`

**Step 1: Update docs**

- live daily mode 에서도 websocket hard-stop monitoring 이 startup/sync 된다는
  operator expectation 을 checklist 와 architecture 문서에 기록한다.

**Step 2: Run validation**

Run: `pytest -v tests/test_main.py -k 'realtime_hard_stop and (daily or startup or sync)'`
Expected: PASS

Run: `ruff check src/ tests/`
Expected: PASS

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 3: Commit**

```bash
git add docs/live-trading-checklist.md docs/architecture.md tests/test_main.py src/main.py
git commit -m "docs: describe live daily hard-stop monitoring"
```
