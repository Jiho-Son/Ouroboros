# Issue 318/325 Runtime Evidence Design

## Context

`#318` and `#325` are implemented and tested in unit tests, but current runtime evidence is too weak to close them. The main gap is observability: `decision_logs.input_data` does not persist the staged-exit feature inputs or the resolved thresholds that actually govern the exit decision.

## Chosen Approach

Extend the existing decision log payloads so every HOLD/SELL decision that flows through staged-exit logic records:

- `atr_value`
- `pred_down_prob`
- resolved `stop_loss_threshold`
- resolved `be_arm_pct`
- resolved `arm_pct`
- staged-exit `reason` when an override triggers

This keeps evidence in the same storage path the system already uses for runtime review.

## Alternatives Considered

1. Expand `decision_logs` payloads in place.
   Smallest change, no schema migration needed, directly queryable from current DB.

2. Add a dedicated staged-exit audit table.
   Cleaner long-term, but too much surface area for the immediate evidence gap.

## Validation

- Add a failing regression test that asserts staged-exit values are persisted in `decision_logs`.
- Implement the minimal logging changes.
- Re-run focused pytest and lint.
