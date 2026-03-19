# OOR-824 Pending-Order Executable Quote Follow-Ups Design

## Context

- Ticket `OOR-824` captures reviewer follow-ups intentionally deferred from PR `#829`.
- The current code already preserves OOR-813 correctness, but several maintenance and contract gaps remain:
  - `src/config.py` validates market gap-cap JSON by reparsing it inside `_validate_selected_llm_provider()` and then reparsing again inside the cached property.
  - `src/broker/pending_orders.py` and `src/broker/overseas.py` still expose two orderbook top-level extraction entry points for the same payload family.
  - `_fetch_optional_quote_payload()` supports both async and sync quote methods through `inspect.isawaitable`, even though the production broker methods are async.
  - SELL retry paths use executable bid quotes with `enforce_gap_cap=False`, but that policy intent is not documented or locked by a focused regression test.
  - After `gap_rejected` early-continue branches, `if new_price is None` remains as a defensive branch that should be unreachable under the current control flow.

## Approaches Considered

### 1. Minimal local cleanups only

- Split the config validator, add comments, and trim the unreachable checks without touching helper boundaries.
- Pros: smallest patch.
- Cons: leaves quote extraction ownership split across modules and preserves the ambiguous async compatibility contract.

### 2. Normalize helper contracts at the module boundary

- Keep retry behavior in `pending_orders.py`, but make helper contracts explicit:
  - dedicated config validator for gap-cap JSON,
  - single shared orderbook top-level extraction implementation,
  - strict async quote-fetch helper,
  - explicit SELL retry policy comment plus regression tests,
  - replace unreachable `None` branch with an assertion that matches actual flow.
- Pros: directly addresses every acceptance criterion with narrow surface-area changes.
- Cons: requires touching both helper tests and pending-order integration tests.

### 3. Push all executable-quote logic into broker classes

- Move async quote fetching and top-level extraction fully into `KISBroker` / `OverseasBroker`.
- Pros: stronger object ownership.
- Cons: much broader refactor than the ticket asks for and unnecessary risk for a follow-up cleanup ticket.

## Recommendation

Choose approach 2.

The ticket is not asking for a new broker abstraction. It is asking for clearer contracts around validation, quote extraction, and retry policy. A small helper/validator cleanup plus RED-first tests closes the review follow-ups without reopening the larger OOR-813 design.

## Proposed Design

### Config validation

- Add a dedicated `@model_validator(mode="after")` path that validates `EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON` once for structure and numeric ranges.
- Keep `executable_quote_gap_caps_by_market` as the single place that parses-and-caches the normalized mapping.
- Narrow `_validate_selected_llm_provider()` back to provider selection concerns only.

### Shared orderbook extraction

- Use `src/broker/orderbook_utils.py::extract_orderbook_top_levels()` as the single implementation for domestic and overseas payloads.
- Remove the extra overseas-only container ordering so both paths share the same default alias/container contract while still supporting `output1`, `output2`, and `output`.
- Keep `OverseasBroker._extract_orderbook_top_levels()` as a thin compatibility wrapper for existing callers/tests.

### Quote fetch contract

- Tighten `_fetch_optional_quote_payload()` to require an async callable and `await` it directly.
- Treat sync-returning mocks as invalid test doubles instead of supported production behavior.
- Cover the contract with focused helper tests so future changes cannot silently reintroduce mixed sync/async semantics.

### SELL retry policy and control flow

- Keep SELL retries anchored to executable best-bid when available, with last-price multiplier as fallback.
- Add a short comment that SELL retries intentionally skip gap-cap enforcement so exits remain executable instead of being blocked by a wide spread during unwind.
- Replace post-resolution `if new_price is None` branches with assertions that document the invariant implied by `_resolve_retry_price_from_executable_quote()`.

## Verification Plan

- Add RED-first helper tests for:
  - config validation no longer reparsing JSON through the provider validator path,
  - shared extraction handling `output1`, `output2`, and `output`,
  - strict async quote-fetch contract,
  - SELL retry choosing executable bid when present.
- Re-run focused pending-order regressions in `tests/test_main.py`.
- Finish with `ruff`, docs sync, and full `pytest --cov` evidence before reporting completion.
