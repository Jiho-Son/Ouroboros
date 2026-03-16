# OOR-825 Repeated SELL Unfilled Rework Design

## Context

- Ticket `OOR-825` reports that repeated unfilled SELL handling is still broken on the latest `main`.
- Current flow splits responsibility:
  - `src/broker/pending_orders.py` cancels an unfilled SELL, resubmits once at a wider spread, then restores the position on the second unfilled event.
  - `src/main.py` does not receive that exhausted retry state when `trading_cycle()` evaluates the same symbol again.
- That means the runtime can restore the position after cancel-only handling and then immediately emit a fresh ordinary SELL, recreating the loop.
- The human comment on the issue is stricter than the previous attempt: repeated unfilled SELLs should be resolved by quickly cutting the position and finishing, not merely by suppressing one code path.

## Approaches Considered

### 1. Suppress further SELL submission after retry exhaustion

- Pass exhausted pending-order state into `trading_cycle()` and return early when the next SELL decision appears.
- Pros: minimal patch surface.
- Cons: it stops the loop but can leave the position open indefinitely, which does not satisfy the “손절하고 끝내기” intent.

### 2. Force terminal exit from `trading_cycle()` after retry exhaustion

- Pass exhausted pending-order state into `trading_cycle()` / `_execute_trading_cycle_action()`.
- When the next SELL is evaluated for the same position:
  - use a market order in sessions where order policy allows it,
  - otherwise fall back to the most aggressive supported limit price path.
- Pros: keeps DB/decision logging in the main execution path, preserves pending-order isolation, and aligns with the issue comment’s desired behavior.
- Cons: requires threading retry state through runtime entrypoints and adding deterministic tests around forced SELL pricing.

### 3. Execute the final forced SELL directly inside `pending_orders`

- Change the second unfilled branch from cancel-only restore to an immediate final SELL submission there.
- Pros: reacts one function earlier.
- Cons: the pending-order layer does not own trade logging / decision logging / runtime risk plumbing today, so this broadens coupling and increases rework risk.

## Recommendation

Choose approach 2.

The root cause is missing state propagation from pending-order recovery into the main SELL execution path. Fixing that propagation while turning the next SELL into a terminal exit preserves the current architecture and matches the reviewer’s intent better than the rejected “just skip SELL” approach.

## Proposed Design

### Runtime State Propagation

- Introduce a helper in `src/main.py` that derives the pending SELL retry key from `market` + `stock_code`.
- Thread `sell_resubmit_counts` into:
  - `trading_cycle()`
  - `_execute_trading_cycle_action()`
  - the runtime `run()` call site

### Forced Exit Behavior

- When a SELL decision is evaluated and the pending SELL retry key already has `>= 1` attempts:
  - log that the runtime is escalating because pending retry budget is exhausted,
  - choose a terminal-exit order price:
    - regular-liquidity session: `price=0` market order,
    - low-liquidity session: keep policy compliance and use the more aggressive SELL retry limit path instead of the normal `0.998` limit.
- Clear stale retry state after a successful BUY or SELL so a new lifecycle for the same symbol starts cleanly.

### Why This Is Different From The Previous Attempt

- The previous attempt treated exhausted retry state as a reason to block further SELL submission.
- This rework treats the same signal as a reason to escalate liquidation aggressiveness while staying inside the normal trade persistence path.

## Files Expected To Change

- `src/main.py`
- `tests/test_main.py`
- `workflow/session-handover.md`

## Verification Plan

- Add failing runtime tests first for:
  - domestic exhausted pending SELL -> forced terminal exit order,
  - overseas exhausted pending SELL -> forced terminal exit order,
  - stale retry state cleared after a successful new BUY lifecycle.
- Run targeted pytest for the new tests and nearby pending-order regressions.
- Run `ruff check src/ tests/`.
- Run the full suite only after the targeted proof is green.
