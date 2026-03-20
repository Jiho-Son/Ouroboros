# OOR-829 Recent SELL Fee Buffer Design

## Context

- `OOR-829` is a follow-up to `OOR-815` / PR `#833`.
- The current recent-SELL guard already blocks immediate BUY retries when:
  - the latest SELL is still inside `SELL_REENTRY_PRICE_GUARD_SECONDS`, and
  - `current_price > last_sell_price`.
- The open question is whether the guard should become `last_sell_price + fee_buffer`.

## Current Signal

- `src/core/order_helpers.py` currently performs a strict comparison with no fee or slippage adjustment.
- `tests/test_main.py` already proves the guard on both execution paths:
  - higher-price re-entry is blocked inside the window,
  - lower-price re-entry is allowed,
  - the guard expires after the window.

## Approaches Considered

### 1. Keep the strict guard and document the boundary

- Pros:
  - preserves the exact `OOR-815` invariant: "block immediate BUY only when it is above the latest SELL price,"
  - remains deterministic at decision time,
  - avoids introducing a pseudo-cost model that the runtime cannot justify yet.
- Cons:
  - does not try to recover fees or spread on the immediate re-entry decision.

### 2. Add a fixed fee buffer setting now

- Example: `SELL_REENTRY_PRICE_BUFFER_BPS` or `SELL_REENTRY_PRICE_BUFFER_ABS`.
- Pros:
  - easy to wire into the existing helper.
- Cons:
  - a single fixed value is under-modeled for this system because fees, taxes, FX, spread, and slippage differ by market, product, and execution route,
  - it weakens the current invariant by allowing some higher-price re-buys immediately after a SELL,
  - the decision layer does not know the eventual execution slippage, so a fixed buffer would be false precision.

### 3. Add a market/product/route-aware cost model first

- Pros:
  - this is the first option that can justify a non-zero fee buffer without guessing.
- Cons:
  - larger scope than this ticket,
  - needs explicit modeling inputs that do not exist in the current settings surface.

## Recommendation

Choose approach 1 for `OOR-829`: keep the strict guard and document why.

The current ticket is scoped to "block immediate BUY when it is above the latest SELL price." A fee buffer would change that semantic contract before the repository has a trustworthy cost model. Because the execution gate is safety-oriented, the safer default is to keep the strict comparison and defer any buffer until cost inputs can be modeled per market/product/route.

## Decision

- Do not add `fee_buffer` settings in this ticket.
- Keep the comparison strict: `current_price > last_sell_price`.
- Add documentation and a boundary regression test showing that equal-price re-entry is still allowed, proving the guard has no hidden fee buffer.

## Validation

- `pytest tests/test_main.py -k "suppresses_buy_above_recent_sell_price or recent_sell_guard" -v`
- `ruff check src/core/order_helpers.py tests/test_main.py docs/architecture.md`
- `python3 scripts/validate_docs_sync.py`
