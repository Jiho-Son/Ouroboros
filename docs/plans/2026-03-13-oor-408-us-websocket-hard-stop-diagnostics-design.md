# OOR-408 US Websocket Hard-Stop Diagnostics Design

**Ticket:** `OOR-408`

**Problem:** After the 2026-03-09 restart, operations still have no runtime evidence proving that the US websocket hard-stop path is alive end-to-end. The current code already records `websocket_hard_stop` at the trigger/persistence boundary, but most of the lifecycle, parse, and no-trigger diagnostics are either generic or `debug`-only, so production logs cannot distinguish missing subscription, parse failure, trigger miss, and source-logging gaps.

**Approval source:** This is an unattended orchestration session, so the Linear ticket body and linked runtime evidence are treated as the design input and approval boundary.

## Constraints

- Keep staged-exit policy unchanged.
- Keep KR websocket behavior unchanged unless a shared helper can be proven behavior-neutral.
- Preserve existing hard-stop risk controls and order paths.
- Add reviewer-usable runtime diagnostics without turning the websocket loop into unbounded log spam.

## Options

### Option 1: Promote all websocket diagnostics to info globally

Raise the existing websocket and monitor diagnostics from `debug` to `info` for every market.

- Pros: small code diff, reuses existing log sites.
- Cons: noisy for KR, does not clearly isolate the US path that is currently missing evidence, and still leaves some boundaries too generic.

### Option 2: Add structured US-focused diagnostics at each boundary

Keep the existing behavior, but add explicit action-oriented logs for US websocket connect/resubscribe/subscribe/unsubscribe, parsed and ignored price events, `evaluate_price()` entry/result, and decision/trade persistence boundaries.

- Pros: directly addresses the ticket's failure modes while keeping KR scope stable.
- Cons: touches several modules and needs log-oriented regression tests.

### Option 3: Add counters/metrics only

Introduce counters or aggregate summaries instead of detailed logs.

- Pros: lower log volume.
- Cons: does not help the current operator workflow, which depends on restart-time logs and DB evidence rather than metrics infrastructure.

## Decision

Choose **Option 2**.

The ticket is explicitly about operational diagnostics for the missing US websocket hard-stop runtime path. The code already has the core execution logic; the gap is boundary observability at production log levels.

## Design

### 1. Websocket lifecycle diagnostics

Extend [`src/broker/kis_websocket.py`](/home/agentson/code/symphony-workspaces/OOR-408/src/broker/kis_websocket.py) with structured lifecycle logs that are explicit enough for runtime triage:

- connect success with websocket URL
- resubscribe batch count plus per-symbol resubscribe activity
- subscribe send and unsubscribe send for US symbols
- ignored US payloads with parse-failure classification
- parsed US price events with market, symbol, and TR ID

The existing generic logs remain useful, but the new action-oriented wording should make the runtime path searchable from a single restart log.

### 2. Hard-stop evaluation diagnostics

Extend [`src/core/realtime_hard_stop.py`](/home/agentson/code/symphony-workspaces/OOR-408/src/core/realtime_hard_stop.py) so the monitor exposes the boundary that is currently invisible in production:

- evaluation entry for tracked US symbols
- evaluation result for `untracked`, `in_flight`, `above_stop`, and `triggered`
- tracked hard-stop price and in-flight state in the log payload

This is the point where operators need to know whether messages are arriving but never becoming triggers.

### 3. Runtime handler diagnostics

Refine [`src/main.py`](/home/agentson/code/symphony-workspaces/OOR-408/src/main.py) around the US realtime handler:

- explicit receive/no-trigger/dispatch logs for US price events
- explicit `decision_logger.log_decision()` and `log_trade()` boundary logs that keep `source=websocket_hard_stop` visible
- no staged-exit behavior changes

This separates "trigger reached handler" from "source reached DB persistence".

### 4. Validation and close criteria docs

Update the operator docs to define the post-restart evidence required to close the ticket:

- what log lines must appear for US subscribe/resubscribe
- what log lines indicate parse success, parse ignore, no-trigger, or trigger dispatch
- what DB evidence proves `decision_logs` and `trades` both received `websocket_hard_stop`
- what counts as insufficient evidence after restart

## Test Strategy

- Add failing log-assertion tests first in [`tests/test_kis_websocket.py`](/home/agentson/code/symphony-workspaces/OOR-408/tests/test_kis_websocket.py), [`tests/test_realtime_hard_stop.py`](/home/agentson/code/symphony-workspaces/OOR-408/tests/test_realtime_hard_stop.py), and [`tests/test_main.py`](/home/agentson/code/symphony-workspaces/OOR-408/tests/test_main.py).
- Verify the source-recording path with the existing `decision_logs`/`trades` assertions plus new log assertions for the persistence boundary.
- Run scoped pytest, `ruff`, docs sync, `git diff --check`, and the strict handover gate before handoff.
