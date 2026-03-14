# OOR-813 Unfilled Order Policy Design

**Ticket:** `OOR-813`

**Problem:** Pending BUY/SELL retries currently reprice from the last trade alone. In thin sessions such as `US_PRE`, the last trade can be far away from the executable best ask/bid, so the runtime cancels and resubmits another stale-price limit that is still unlikely to fill.

**Approval source:** This is an unattended orchestration session. The Linear ticket body is treated as the design input and approval boundary.

## Constraints

- Keep the change narrow to the unfilled-order retry path unless code inspection shows the same helper can be reused without broad regressions.
- Preserve the existing "at most one resubmit per key per session" invariant.
- Do not replace the current session-aware order-policy guardrails; extend them with executable-quote awareness.
- Use documented KIS quote surfaces rather than guessing field names.

## Options

### Option 1: Keep last-price repricing and widen the premium/discount

- Pros: tiny diff.
- Cons: still ignores executable quotes, so wide pre-market spreads remain unresolved.

### Option 2: Always chase to the best ask/bid

- Pros: removes the stale-price retry.
- Cons: can overpay or undersell aggressively when the spread is abnormally wide.

### Option 3: Use the best executable quote with a low-liquidity gap cap

- Pros: prices retries from the actual fillable quote, but cancels instead of blindly chasing when the best quote is too far from the last trade.
- Cons: introduces one new policy threshold and quote-fetch plumbing.

## Decision

Choose **Option 3**.

The ticket is specifically about gaps between current price and executable price. The fix should therefore use the executable quote when it is sane, and explicitly refuse to chase when the quote gap itself signals thin liquidity.

## Design

### 1. Quote fetch and parsing

- Domestic pending retries should read the top-of-book from the existing domestic orderbook API.
- Overseas pending retries should add a dedicated orderbook call backed by the official `해외주식 현재가 호가` endpoint so `pask1` / `pbid1` can be used in `US_PRE`, `US_REG`, and `US_AFTER`.

### 2. Retry pricing policy

- BUY retries use the executable best ask.
- SELL retries use the executable best bid.
- If the executable quote is missing, fall back to the current last-price-based retry so non-quote failures do not fully disable retries.
- In low-liquidity sessions, if the best executable quote differs from the last trade by more than a configured percentage, cancel the pending order, apply the existing cooldown/rollback path, and do not submit a replacement order.

### 3. Scope of the first patch

- Implement the policy in `src/broker/pending_orders.py`, where repeated unfilled retries currently happen.
- Keep the initial order-submission path unchanged in this patch unless the new helper can be reused with no extra policy complexity.

## Test Strategy

- Add failing pending-order regression tests first for domestic and overseas retry paths.
- Add focused broker tests for the new overseas orderbook client.
- Verify the new cancel-without-retry behavior when the best quote gap exceeds the cap in a low-liquidity session.
