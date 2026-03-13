# OOR-408 US Websocket Hard-Stop Diagnostics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild `OOR-408` from current `origin/main` and add the full US websocket hard-stop diagnostics set, including the overlapping startup/subscription evidence that should supersede `OOR-403`.

**Architecture:** Keep runtime hard-stop behavior unchanged while adding structured observability at three boundaries: websocket lifecycle/parsing, realtime hard-stop evaluation, and runtime persistence. Use failing `caplog` tests first, then add operator docs that define restart close criteria and `NOT_OBSERVED` handling.

**Tech Stack:** Python, asyncio, pytest, caplog, GitHub CLI, Linear workpad workflow

---

### Task 1: Lock the reset evidence and overlap decision

**Files:**
- Modify: `workflow/session-handover.md`
- Create: `docs/plans/2026-03-13-oor-408-us-websocket-hard-stop-diagnostics-design.md`
- Create: `docs/plans/2026-03-13-oor-408-us-websocket-hard-stop-diagnostics.md`

**Step 1: Record the current-base evidence**

- Update the Linear workpad with:
  - stale PR `#813` closed,
  - fresh branch cut from `origin/main@65983bd`,
  - clean pull result,
  - source-based reproduction showing current main only has the generic startup line, existing `no_trigger`/`dispatch_trigger`, and `action=persisted`.

**Step 2: Verify the evidence commands**

Run:

```bash
rg -n "Realtime hard-stop websocket monitor started|Realtime websocket action=connect|Realtime websocket action=(resubscribe|subscribe|unsubscribe)|Realtime websocket action=(parsed_us_event|ignore_us_parse_failure)|Realtime hard-stop evaluate action=(enter|result)|Realtime price event action=(received_us_event|no_trigger|dispatch_trigger)|Realtime hard-stop action=(decision_logged|trade_logged|persisted)" src tests docs -S
git diff --name-only origin/main...origin/feature/issue-403-us-realtime-hard-stop-runtime-evidence-r2
git diff --name-only origin/main...origin/feature/issue-408-us-websocket-hard-stop-diagnostics
```

Expected: current main is missing the deeper US diagnostics; `OOR-403` only covers startup/subscription/checklist files; stale `OOR-408` covers the full diagnostics set.

### Task 2: Write failing websocket lifecycle and parse tests

**Files:**
- Modify: `tests/test_kis_websocket.py`

**Step 1: Add failing tests**

- Add `caplog` assertions for:
  - US `action=connect`
  - US `action=subscribe`
  - US `action=unsubscribe`
  - US `action=resubscribe`
  - US `action=parsed_us_event`
  - US `action=ignore_us_parse_failure`

**Step 2: Run the websocket subset and verify RED**

Run:

```bash
pytest tests/test_kis_websocket.py -k "subscribe or unsubscribe or reconnects or parse or connect" -v
```

Expected: FAIL because those structured US diagnostics are absent on current main.

### Task 3: Write failing evaluation and runtime-boundary tests

**Files:**
- Modify: `tests/test_realtime_hard_stop.py`
- Modify: `tests/test_main.py`

**Step 1: Add failing tests**

- Add monitor tests asserting US `action=enter` and `action=result` logs for `above_stop`, `in_flight`, and `triggered`.
- Add runtime tests asserting:
  - startup coverage log with enabled markets and `source=websocket_hard_stop`
  - subscribe sync log for tracked US positions
  - US `received_us_event`, `no_trigger`, and `dispatch_trigger`
  - US `decision_logged` and `trade_logged` persistence-boundary logs

**Step 2: Run the monitor/runtime subset and verify RED**

Run:

```bash
pytest tests/test_realtime_hard_stop.py tests/test_main.py -k "realtime_hard_stop or websocket_hard_stop" -v
```

Expected: FAIL because the current implementation does not emit those logs yet.

### Task 4: Implement the minimal diagnostics

**Files:**
- Modify: `src/broker/kis_websocket.py`
- Modify: `src/core/realtime_hard_stop.py`
- Modify: `src/main.py`

**Step 1: Add the websocket/client diagnostics**

- Log `action=connect` on successful websocket open.
- Log US `subscribe`, `unsubscribe`, and `resubscribe` sends without double-prefixing the symbol key.
- Log US parse success and US parse-ignore reasons only for overseas payloads.

**Step 2: Add the evaluation/runtime diagnostics**

- Add US `evaluate action=enter` and `evaluate action=result` logs in `RealtimeHardStopMonitor`.
- Add startup coverage, US receive/no-trigger/dispatch, and US persistence-boundary logs in `src.main`.

**Step 3: Run the focused scope and verify GREEN**

Run:

```bash
pytest tests/test_kis_websocket.py tests/test_realtime_hard_stop.py tests/test_main.py -k "realtime_hard_stop or websocket_hard_stop or subscribe or unsubscribe or reconnects or parse or connect" -v
```

Expected: PASS.

### Task 5: Document restart validation and close criteria

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/live-trading-checklist.md`

**Step 1: Update the docs**

- Add a restart-time grep recipe for the US websocket hard-stop evidence path.
- Define required startup, subscription, event-path, and persistence evidence.
- State that any missing required row is `NOT_OBSERVED` and fails close criteria.

**Step 2: Run the docs gate**

Run:

```bash
python3 scripts/validate_docs_sync.py
```

Expected: PASS.

### Task 6: Final verification and tracker cleanup

**Files:**
- Modify: `workflow/session-handover.md`

**Step 1: Run the required validation**

Run:

```bash
pytest tests/test_kis_websocket.py tests/test_realtime_hard_stop.py tests/test_main.py -v
pytest -v --cov=src --cov-report=term-missing
ruff check src/broker/kis_websocket.py src/core/realtime_hard_stop.py src/main.py tests/test_kis_websocket.py tests/test_realtime_hard_stop.py tests/test_main.py
python3 scripts/validate_docs_sync.py
git diff --check
python3 scripts/session_handover_check.py --strict
```

Expected: PASS across all commands.

**Step 2: Publish and reconcile**

- Update the Linear workpad with completed plan, acceptance, validation, pull, and reproduction notes.
- Publish the replacement `OOR-408` PR and attach it to Linear with the `symphony` label.
- Resolve the overlap explicitly so `OOR-403` and PR `#814` no longer remain the active path for the shared diagnostics scope.
