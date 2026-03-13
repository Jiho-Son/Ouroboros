# OOR-816 Buy Entry Timing Design

## Context

- Ticket `OOR-816` reports repeated BUY entries when a name is already sharply extended and trading at the local/session high.
- Current BUY suppression in `src/main.py` only covers confidence, duplicate holdings, US minimum price, and cooldown guards.
- Quote enrichment already exposes `session_high_price` in the realtime path, but the BUY decision path does not use it.
- The daily-session path enriches `price_change_pct` for every stock, but currently only records `session_high_price` for overseas stocks.

## Approaches Considered

### 1. Planner/schema expansion

- Extend `src/strategy/models.py`, `src/strategy/scenario_engine.py`, and `src/strategy/pre_market_planner.py` with recent-high and pullback-aware conditions.
- Pros: expressive and AI-visible.
- Cons: broad surface, prompt/schema churn, and AI-generated playbooks could still omit the new guard.

### 2. Execution-time chase guard using quote high and intraday gain

- Add a pure helper in `src/core/order_helpers.py` that blocks BUY when:
  - price is already up materially on the session, and
  - current price is still too close to the session high.
- Call it from both BUY execution paths in `src/main.py`.
- Pros: deterministic, narrow, applies to both AI and fallback playbooks, and works without changing playbook generation.
- Cons: less expressive than a full scenario language change.

### 3. Hybrid planner + execution guard

- Implement approach 2 now and also teach the planner/scenario schema about pullback-aware entries.
- Pros: strongest long-term modeling.
- Cons: highest scope and more review surface than the ticket requires.

## Recommendation

Choose approach 2.

It solves the observed failure mode at the actual order gate, covers both realtime and daily session execution, and avoids depending on AI-generated scenario quality. The helper should use session-risk-resolved thresholds so the guard remains tunable without hardcoding behavior into the trade loop.

## Proposed Behavior

- Define a BUY as “high chase” when all of the following are true:
  - `price_change_pct` is above a configurable minimum gain threshold.
  - `session_high_price` is available and positive.
  - `current_price` is within a configurable pullback buffer from that session high.
- In that case, suppress BUY to HOLD with a rationale that includes the intraday gain and pullback-from-high percentage.
- When price pulls back beyond that buffer, the BUY path is allowed again.

## Initial Thresholds

- `BUY_CHASE_MIN_INTRADAY_GAIN_PCT = 4.0`
- `BUY_CHASE_MAX_PULLBACK_FROM_HIGH_PCT = 0.5`

These defaults aim to block obvious late chases while allowing normal trend-follow entries that are not already stretched.

## Files Expected To Change

- [`src/config.py`](/home/agentson/code/symphony-workspaces/OOR-816/src/config.py)
- [`.env.example`](/home/agentson/code/symphony-workspaces/OOR-816/.env.example)
- [`src/core/order_helpers.py`](/home/agentson/code/symphony-workspaces/OOR-816/src/core/order_helpers.py)
- [`src/main.py`](/home/agentson/code/symphony-workspaces/OOR-816/src/main.py)
- [`tests/test_main.py`](/home/agentson/code/symphony-workspaces/OOR-816/tests/test_main.py)
- [`docs/architecture.md`](/home/agentson/code/symphony-workspaces/OOR-816/docs/architecture.md)

## Verification Plan

- Reproduce with a failing integration test in `trading_cycle` showing BUY still executes while price is up sharply and sitting at the session high.
- Add a second regression around the daily-session path so the same guard holds under the default daily trade mode.
- Add focused helper boundary coverage for the new chase-guard function.
