# OOR-408 US Websocket Hard-Stop Diagnostics Design

**Ticket:** `OOR-408`

**Problem:** Fresh `origin/main@65983bd` still lacks production-visible diagnostics that separate US websocket hard-stop connect/subscribe, parse, evaluation, and persistence boundaries. The stale `OOR-408` branch had the full diagnostic set, but it was based on an older mainline and now overlaps with `OOR-403`, which already captured part of the startup/subscription observability gap.

**Approval source:** This is an unattended orchestration session. The Linear ticket body plus the human comment asking to merge the `OOR-403` overlap into `OOR-408` are treated as the design input and approval boundary.

## Constraints

- Do not change staged-exit or hard-stop trading policy.
- Keep KR websocket behavior unchanged unless a shared helper is behavior-neutral.
- Preserve existing `websocket_hard_stop` order and persistence behavior.
- Make restart-time diagnostics easy to grep without turning every websocket event into noisy global logging.

## Options

### Option 1: Keep `OOR-403` and `OOR-408` separate

Leave startup/subscription evidence in `OOR-403` and rebuild only the deeper parse/evaluate/persistence diagnostics in `OOR-408`.

- Pros: smallest immediate diff on this branch.
- Cons: leaves two active issues/PRs for one observability problem and keeps overlap in `src/main.py`, `src/broker/kis_websocket.py`, and the live-trading checklist.

### Option 2: Replay the full stale `OOR-408` branch as-is

Cherry-pick or reapply the old `OOR-408` patch onto current main without reconsidering the `OOR-403` subset.

- Pros: fast if the old diff still applies cleanly.
- Cons: repeats overlap instead of resolving it, and risks carrying stale assumptions from pre-`65983bd` main.

### Option 3: Rebuild `OOR-408` from current main and absorb the `OOR-403` subset

Use current `origin/main` as the base, replay the needed startup/subscription observability from `OOR-403`, then add the deeper US-only connect/parse/evaluate/persistence diagnostics that only existed in the stale `OOR-408` branch.

- Pros: leaves one active implementation path, matches the human overlap comment, and keeps the final PR coherent.
- Cons: larger rework patch and requires explicit tracker cleanup for `OOR-403`.

## Decision

Choose **Option 3**.

`OOR-403` is a subset of the broader `OOR-408` observability problem. The cleanest outcome is a fresh `OOR-408` branch that includes the startup/subscription evidence plus the deeper US runtime diagnostics, then explicitly retires the redundant `OOR-403` path.

## Design

### 1. Websocket lifecycle diagnostics

Extend [`src/broker/kis_websocket.py`](/home/agentson/code/symphony-workspaces/OOR-408/src/broker/kis_websocket.py) with structured US-focused action logs:

- `action=connect` when the websocket session is established
- `action=subscribe` and `action=unsubscribe` for US symbol sends
- `action=resubscribe` for tracked US symbols on reconnect
- `action=parsed_us_event` for successfully parsed US payloads
- `action=ignore_us_parse_failure` with the parse-failure reason for rejected US payloads

This keeps the diagnostics concentrated on the currently missing US path while preserving the existing KR behavior.

### 2. Hard-stop evaluation diagnostics

Extend [`src/core/realtime_hard_stop.py`](/home/agentson/code/symphony-workspaces/OOR-408/src/core/realtime_hard_stop.py) so `evaluate_price_diagnostic()` emits US-specific entry/result logs:

- `action=enter` with tracked-state visibility
- `action=result reason=untracked`
- `action=result reason=in_flight`
- `action=result reason=above_stop`
- `action=result reason=triggered`

This boundary is how operators distinguish "messages are arriving" from "messages are not producing a usable trigger."

### 3. Runtime handler and persistence-boundary diagnostics

Refine [`src/main.py`](/home/agentson/code/symphony-workspaces/OOR-408/src/main.py):

- startup log that names enabled realtime hard-stop markets and `source=websocket_hard_stop`
- explicit subscribe logging during monitor sync
- US event-path logs for `received_us_event`, `no_trigger`, and `dispatch_trigger`
- explicit `decision_logged`, `trade_logged`, and `persisted` logs on the US hard-stop SELL path

The goal is to separate the runtime path into search-friendly checkpoints without altering the sell logic itself.

### 4. Operator validation docs

Update [`docs/commands.md`](/home/agentson/code/symphony-workspaces/OOR-408/docs/commands.md) and [`docs/live-trading-checklist.md`](/home/agentson/code/symphony-workspaces/OOR-408/docs/live-trading-checklist.md) so restart validation has concrete close criteria:

- startup/connect evidence
- tracked-symbol subscribe or resubscribe evidence
- parsed-event, ignored-event, no-trigger, or dispatch evidence
- `decision_logs` and `trades` persistence evidence when a websocket SELL fires
- `NOT_OBSERVED` handling when any required boundary is missing during the observation window

## Test Strategy

- Add failing `caplog` tests first in [`tests/test_kis_websocket.py`](/home/agentson/code/symphony-workspaces/OOR-408/tests/test_kis_websocket.py), [`tests/test_realtime_hard_stop.py`](/home/agentson/code/symphony-workspaces/OOR-408/tests/test_realtime_hard_stop.py), and [`tests/test_main.py`](/home/agentson/code/symphony-workspaces/OOR-408/tests/test_main.py).
- Reuse the existing DB assertions for `selection_context["source"] == "websocket_hard_stop"` and add log assertions around the persistence boundary.
- Validate with scoped pytest first, then `pytest -v --cov=src --cov-report=term-missing`, `ruff`, docs sync, `git diff --check`, and the strict handover gate.
