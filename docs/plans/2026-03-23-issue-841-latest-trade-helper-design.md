# OOR-841 Latest Trade Helper Contract Design

## Context

- `get_latest_buy_trade()` and `get_latest_sell_trade()` are intentionally not symmetric today.
- PR #847 review surfaced two pre-existing differences that were left as follow-up work:
  - BUY filters out rows where `decision_id IS NULL`, SELL does not.
  - BUY returns `selection_context`, SELL returns `timestamp`.
- OOR-841 asks whether those differences are bugs or part of the current helper contract, and to reduce future confusion with code/docs/tests.

## Current Behavior

- `get_latest_buy_trade(..., exchange_code=None)` skips the newest BUY row when it has no `decision_id`.
- `get_latest_sell_trade(..., exchange_code=None)` can return the newest SELL row even when `decision_id` is `NULL`.
- BUY helper call sites use `decision_id` and `selection_context` for restore/audit/PnL follow-up logic.
- SELL helper call sites use the freshest SELL `price` and `timestamp` for recent-sell guard logic.

## Options

### Option 1: Add `decision_id IS NOT NULL` to SELL helper

- Pros: makes BUY/SELL SQL filtering more symmetric.
- Cons: hides decision-less SELL rows from recent-sell guard calculations.
- Risk: real SELL records from recovery/synthetic flows can lose protection if they do not carry a `decision_id`.

### Option 2: Keep SELL helper unfiltered and document the asymmetry

- Pros: matches current caller needs and avoids changing recent-sell guard behavior.
- Pros: lowest-risk path because runtime behavior stays unchanged.
- Cons: helper APIs remain asymmetric and need explicit documentation/tests.

### Option 3: Normalize helper return schemas

- Pros: cleaner API surface long term.
- Cons: requires broader caller updates and is out of scope for this follow-up cleanup.

## Decision

Choose Option 2.

- BUY helper remains decision-linked because its consumers need audit linkage and `selection_context`.
- SELL helper remains timestamp-first and does not require `decision_id` because recent-sell guard only needs the freshest eligible SELL evidence.
- The asymmetry should be made explicit in tests and docstrings/comments rather than hidden behind a behavior change with weak justification.

## Verification Plan

- Add DB regression coverage that proves BUY skips decision-less rows while SELL keeps them.
- Add contract assertions for the different return keys (`selection_context` vs `timestamp`).
- Update helper docstrings/comments so the decision is visible at the definition site.
