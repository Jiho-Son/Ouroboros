# OOR-823 Pending-Order Quote Dedupe Design

## Context

- Ticket `OOR-823` splits out reviewer feedback from the OOR-813 rework.
- `src/broker/pending_orders.py` currently repeats the same quote-fetch pattern four times:
  - domestic BUY retry
  - domestic SELL retry
  - overseas BUY retry
  - overseas SELL retry
- The repeated blocks all need the same safety properties:
  - tolerate missing quote methods,
  - tolerate sync vs async broker implementations,
  - fall back to last-price multipliers when quote fetch fails,
  - extract only the executable top ask/bid from varying payload shapes.
- Quote extraction is also duplicated between:
  - `src/broker/pending_orders.py::_extract_quote_from_mapping`
  - `src/broker/overseas.py::OverseasBroker._extract_orderbook_top_levels`

## Approaches Considered

### 1. Keep per-branch logic and only factor out the `await` wrapper

- Add one thin helper for `getattr` + `await`, but leave domestic/overseas payload parsing separate.
- Pros: smallest diff inside `pending_orders.py`.
- Cons: reviewer-visible duplication remains, and ask/bid parsing rules still drift between modules.

### 2. Add a shared orderbook utility plus one pending-order quote helper

- Introduce a broker-level utility that extracts top ask/bid from payloads using configurable container keys and field aliases.
- Keep a single `pending_orders` helper responsible for safe optional quote lookup and side-specific quote selection.
- Pros: directly resolves both review comments with a small API surface and keeps retry policy ownership in `pending_orders.py`.
- Cons: adds one new module and two thin compatibility wrappers.

### 3. Push all retry quote logic into broker classes

- Move quote fetching and ask/bid extraction into `KISBroker` / `OverseasBroker` methods and let `pending_orders` consume only normalized quotes.
- Pros: broker ownership is conceptually tidy.
- Cons: broader refactor, larger regression surface, and unnecessary scope expansion for this ticket.

## Recommendation

Choose approach 2.

The ticket only asks to deduplicate pending-order retry quote handling, not to redesign broker ownership. A small shared utility plus a single pending-order helper removes the four repeated fetch blocks, aligns domestic/overseas parsing rules, and keeps the fallback/gap-cap policy in the same module that already owns retry behavior.

## Proposed Design

### Shared Extraction Utility

- Create `src/broker/orderbook_utils.py`.
- Add one utility function that:
  - unwraps common payload containers (`output1`, `output2`, `output`),
  - tolerates either dict or first-row list payloads,
  - scans ordered ask/bid aliases and returns the first positive numeric values,
  - accepts alias tuples so domestic and overseas callers can share the same parser.

### Pending-Order Quote Helper

- In `src/broker/pending_orders.py`, replace the four inline quote-fetch blocks with one helper that:
  - checks whether the broker attribute exists and is callable,
  - calls it with provided kwargs,
  - awaits only when the result is awaitable,
  - swallows quote-fetch exceptions into warning logs,
  - returns the side-specific executable quote (`ask` or `bid`) or `None`.

### Compatibility

- Keep `OverseasBroker._extract_orderbook_top_levels()` as a thin wrapper around the new shared utility so existing broker callers do not break.
- Keep retry price resolution, policy validation, rollback handling, and notification semantics unchanged.

## Verification Plan

- Add RED-first unit tests for:
  - non-callable optional quote attributes returning `{}` instead of raising,
  - shared top-level extraction handling domestic and overseas alias variants through one rule set.
- Re-run the OOR-813 pending-order regressions in `tests/test_main.py`.
- Finish with lint, docs sync, and full `pytest --cov` to prove the refactor stayed behavior-preserving.
