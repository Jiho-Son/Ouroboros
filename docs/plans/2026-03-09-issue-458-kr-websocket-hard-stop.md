# Issue #458 KR WebSocket Hard-Stop Monitoring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add KR WebSocket-backed hard-stop monitoring for open domestic positions so SELL orders are submitted closer to the applied hard-stop threshold during fast price moves.

**Architecture:** Keep the existing polling-based staged-exit engine as the policy source of truth, but publish KR hard-stop thresholds into a dedicated realtime monitor service. The monitor listens to WebSocket price events, deduplicates triggers, and calls a shared SELL execution helper so order submission, trade logging, and cooldown behavior remain consistent.

**Tech Stack:** Python, asyncio, aiohttp/websocket client, sqlite, pytest

---

### Task 1: Establish broker WebSocket integration surface

**Files:**
- Modify: `src/broker/kis_api.py`
- Create: `src/broker/kis_websocket.py`
- Test: `tests/test_kis_websocket.py`

**Step 1: Write the failing tests**

- Add tests for:
  - parsing incoming KR price events into `{stock_code, price}`
  - subscription lifecycle callbacks
  - reconnect rebuilding subscriptions

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_kis_websocket.py -v`
Expected: FAIL because `src.broker.kis_websocket` does not exist.

**Step 3: Write minimal implementation**

- Add a WebSocket client wrapper that:
  - opens/closes a connection
  - maintains subscribed stock codes
  - dispatches parsed price events to a callback
  - reconnects with backoff
- Keep auth/config wiring in `src/broker/kis_api.py` or shared helpers so token/environment setup is not duplicated unsafely.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_kis_websocket.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/broker/kis_api.py src/broker/kis_websocket.py tests/test_kis_websocket.py
git commit -m "feat: add KIS websocket client wrapper"
```

### Task 2: Add KR hard-stop monitor runtime service

**Files:**
- Create: `src/core/realtime_hard_stop.py`
- Modify: `src/strategy/exit_manager.py`
- Test: `tests/test_realtime_hard_stop.py`

**Step 1: Write the failing tests**

- Add tests for:
  - registering a position stores `hard_stop_price`
  - updating the same symbol refreshes metadata idempotently
  - removing a symbol clears monitor state
  - price breach marks the symbol in-flight exactly once

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_realtime_hard_stop.py -v`
Expected: FAIL because runtime monitor service does not exist.

**Step 3: Write minimal implementation**

- Create a service that stores:
  - symbol metadata
  - derived hard-stop price
  - in-flight trigger flags
- Expose methods for register/update/remove and price-event evaluation.
- Add a small helper in `src/strategy/exit_manager.py` to publish effective hard-stop metadata for KR positions.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_realtime_hard_stop.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/core/realtime_hard_stop.py src/strategy/exit_manager.py tests/test_realtime_hard_stop.py
git commit -m "feat: add realtime hard-stop monitor service"
```

### Task 3: Extract shared SELL execution path for realtime triggers

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing tests**

- Add tests proving a WebSocket-triggered hard-stop:
  - submits one SELL order using the existing broker path
  - logs trade/decision evidence with a websocket source marker
  - avoids duplicate submission while already in-flight

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k websocket_hard_stop -v`
Expected: FAIL because no shared realtime sell helper or trigger wiring exists.

**Step 3: Write minimal implementation**

- Extract the existing SELL order execution steps into a helper reusable by:
  - periodic staged-exit SELLs
  - realtime hard-stop SELLs
- Ensure cooldown, log_trade, and runtime exit cache cleanup still happen in one place.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k websocket_hard_stop -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "refactor: share sell execution for realtime hard stops"
```

### Task 4: Wire monitor lifecycle into realtime trading loop

**Files:**
- Modify: `src/main.py`
- Modify: `src/broker/kis_api.py`
- Modify: `src/core/realtime_hard_stop.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing tests**

- Add tests proving:
  - KR open positions are registered with the realtime monitor
  - symbols are removed when positions close
  - WebSocket disconnect does not block polling fallback

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k realtime_hard_stop_monitor -v`
Expected: FAIL because the trading loop does not manage monitor lifecycle.

**Step 3: Write minimal implementation**

- Instantiate the monitor in the realtime run path.
- Refresh tracked KR positions as the loop observes holdings/open positions.
- Start/stop the WebSocket client alongside the trading runtime.
- Keep fallback behavior intact when realtime monitoring is unavailable.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k realtime_hard_stop_monitor -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/main.py src/broker/kis_api.py src/core/realtime_hard_stop.py tests/test_main.py
git commit -m "feat: wire realtime hard-stop monitor into trading loop"
```

### Task 5: Documentation and verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/live-trading-checklist.md`
- Modify: `workflow/session-handover.md`

**Step 1: Write the failing verification target**

- Identify the exact docs that need runtime behavior updates:
  - architecture overview for dual-path exits
  - live trading checklist for WebSocket hard-stop expectations/fallback

**Step 2: Run focused verification**

Run:
- `pytest tests/test_kis_websocket.py tests/test_realtime_hard_stop.py -v`
- `pytest tests/test_main.py -k 'websocket_hard_stop or realtime_hard_stop_monitor' -v`

Expected: PASS

**Step 3: Update documentation**

- Document that KR hard-stop monitoring is realtime/WebSocket-backed while take-profit remains on the polling loop.
- Note fallback semantics when WebSocket is disconnected.

**Step 4: Run broader regression**

Run:
- `pytest -q`

Expected: PASS

**Step 5: Commit**

```bash
git add docs/architecture.md docs/live-trading-checklist.md workflow/session-handover.md
git commit -m "docs: describe KR websocket hard-stop monitoring"
```
