# OOR-408 US Websocket Hard-Stop Diagnostics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add production-visible diagnostics for the US websocket hard-stop path so operators can distinguish subscription, parse, evaluation, trigger, and source-persistence failures after a restart.

**Architecture:** Keep the hard-stop trading behavior unchanged, but add structured diagnostics at the websocket client boundary, the realtime hard-stop monitor boundary, and the runtime trigger/persistence boundary. Drive the change with failing log-assertion tests first, then update operator docs with explicit restart validation criteria.

**Tech Stack:** Python, asyncio, pytest, caplog, Linear workpad workflow

---

### Task 1: Freeze the missing-evidence proof

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-13-oor-408-us-websocket-hard-stop-diagnostics-design.md`
- Modify: `docs/plans/2026-03-13-oor-408-us-websocket-hard-stop-diagnostics.md`

**Step 1: Record reproduction and pull evidence**

- Update the Linear workpad notes with:
  - the strict handover gate result,
  - the clean `origin/main` merge result,
  - the local reproduction signal showing the repo lacks runtime `data/`/`logs/` artifacts and that current searchable evidence is concentrated in `src/main.py` plus existing tests.

**Step 2: Verify the evidence commands**

Run:

```bash
git -c merge.conflictstyle=zdiff3 merge origin/main
rg -n "websocket_hard_stop|Staged exit override|Realtime hard-stop websocket monitor started" -S src tests
```

Expected: clean merge output and repo-local evidence showing `websocket_hard_stop` is visible only near trigger/persistence assertions, not as end-to-end US runtime diagnostics.

### Task 2: Write failing websocket diagnostic tests

**Files:**
- Modify: `tests/test_kis_websocket.py`

**Step 1: Add failing tests**

- Add `caplog` assertions covering:
  - US subscribe send
  - US unsubscribe send
  - per-symbol US resubscribe during reconnect
  - ignored US payload parse-failure logging
  - parsed US price-event logging

**Step 2: Run the websocket subset and verify RED**

Run:

```bash
pytest tests/test_kis_websocket.py -k "subscribe or unsubscribe or reconnects or parse" -v
```

Expected: FAIL because the current logs are too generic or the needed US-specific action logs do not exist yet.

### Task 3: Write failing hard-stop monitor and runtime handler tests

**Files:**
- Modify: `tests/test_realtime_hard_stop.py`
- Modify: `tests/test_main.py`

**Step 1: Add failing tests**

- Add monitor tests that assert US evaluation entry/result diagnostics for `above_stop`, `in_flight`, and `triggered`.
- Add runtime tests that assert:
  - US realtime price-event receive/no-trigger logging
  - US trigger dispatch logging
  - decision/trade persistence boundary logging with `source=websocket_hard_stop`

**Step 2: Run the monitor/runtime subset and verify RED**

Run:

```bash
pytest tests/test_realtime_hard_stop.py tests/test_main.py -k "realtime_hard_stop or websocket_hard_stop" -v
```

Expected: FAIL because those structured diagnostics are not emitted yet.

### Task 4: Implement the minimal diagnostics

**Files:**
- Modify: `src/broker/kis_websocket.py`
- Modify: `src/core/realtime_hard_stop.py`
- Modify: `src/main.py`

**Step 1: Add minimal implementation**

- Add US-focused lifecycle and parse diagnostics in `KISWebSocketClient`.
- Add evaluation entry/result diagnostics in `RealtimeHardStopMonitor`.
- Add US-focused receive/no-trigger/dispatch/persistence logs in `src.main`.

**Step 2: Run the scoped test suite and verify GREEN**

Run:

```bash
pytest tests/test_kis_websocket.py tests/test_realtime_hard_stop.py tests/test_main.py -k "realtime_hard_stop or websocket_hard_stop or subscribe or unsubscribe or reconnects or parse" -v
```

Expected: PASS.

### Task 5: Document restart validation and close criteria

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/live-trading-checklist.md`

**Step 1: Update docs**

- Add restart-time validation guidance for US websocket hard-stop diagnostics.
- Add explicit close criteria that identify the evidence required in logs and DB tables.

**Step 2: Run docs validation**

Run:

```bash
python3 scripts/validate_docs_sync.py
```

Expected: PASS.

### Task 6: Final verification and workpad sync

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-13-oor-408-us-websocket-hard-stop-diagnostics-design.md`
- Modify: `docs/plans/2026-03-13-oor-408-us-websocket-hard-stop-diagnostics.md`

**Step 1: Run the final scoped validation**

Run:

```bash
pytest tests/test_kis_websocket.py tests/test_realtime_hard_stop.py tests/test_main.py -k "realtime_hard_stop or websocket_hard_stop or subscribe or unsubscribe or reconnects or parse" -v
ruff check src/broker/kis_websocket.py src/core/realtime_hard_stop.py src/main.py tests/test_kis_websocket.py tests/test_realtime_hard_stop.py tests/test_main.py
python3 scripts/validate_docs_sync.py
git diff --check
python3 scripts/session_handover_check.py --strict
```

Expected: PASS across all commands.

**Step 2: Update the Linear workpad**

- Check off completed plan, acceptance, and validation items.
- Record reproduction evidence, pull evidence, and final validation results.
