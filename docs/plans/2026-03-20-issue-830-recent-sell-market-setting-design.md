# OOR-830 Recent SELL Guard Market Setting Design

## Context

- `OOR-830` follows PR `#833`, which intentionally deferred the `_resolve_market_setting` dependency cleanup inside `src/core/order_helpers.py`.
- The current `recent SELL` guard works correctly, but it resolves `SELL_REENTRY_PRICE_GUARD_SECONDS` by importing `src.core.session_risk._resolve_market_setting` inside `_should_block_buy_above_recent_sell()`.
- That lazy import hides the dependency boundary and mixes two responsibilities in one helper:
  - price/time comparison for the guard decision,
  - session-aware setting interpretation.

## Current Signal

- `pytest tests/test_main.py -k "recent_sell_guard or suppresses_buy_above_recent_sell_price" -v` is already green on `origin/main`.
- `src/core/order_helpers.py` currently has the dependency hidden inside the guard body:

```python
from src.core.session_risk import _resolve_market_setting
window_seconds = max(
    1,
    int(
        _resolve_market_setting(
            market=market,
            settings=settings,
            key="SELL_REENTRY_PRICE_GUARD_SECONDS",
            default=120,
        )
    ),
)
```

## Approaches Considered

### 1. Resolve `window_seconds` directly at each caller and inject the integer

- Pros:
  - makes `_should_block_buy_above_recent_sell()` fully pure,
  - removes any direct `session_risk` dependency from the guard helper.
- Cons:
  - today there are two duplicated BUY execution paths in `src/main.py`,
  - each path would need to repeat the same `SELL_REENTRY_PRICE_GUARD_SECONDS` normalization,
  - that spreads the setting interpretation across multiple call sites until the broader guard dedupe (`OOR-831`) happens.

### 2. Split a shared recent-SELL setting helper and inject the resolved integer into the guard

- Shape:
  - add a dedicated helper that resolves and normalizes `SELL_REENTRY_PRICE_GUARD_SECONDS`,
  - keep `_should_block_buy_above_recent_sell()` focused on comparing elapsed time and prices.
- Pros:
  - keeps setting interpretation in one place,
  - still removes the direct `session_risk` dependency from the guard itself,
  - minimizes the current scope because both main-path callers can reuse the same helper without tackling the larger duplicated guard block yet.
- Cons:
  - `order_helpers.py` still has a module-level dependency on `session_risk`,
  - adds one extra helper for a single setting.

## Recommendation

Choose approach 2 for `OOR-830`.

The ticket is about clarifying responsibility boundaries without expanding into the broader duplicate-BUY-guard refactor. A dedicated recent-SELL setting helper keeps the market-setting resolution in one place today, while `_should_block_buy_above_recent_sell()` becomes a pure guard that only evaluates explicit inputs. This preserves a small diff and avoids duplicating the normalization logic across the two existing BUY execution paths.

## Decision

- Add a dedicated helper for `SELL_REENTRY_PRICE_GUARD_SECONDS` resolution and normalization.
- Change `_should_block_buy_above_recent_sell()` to accept `window_seconds: int`.
- Remove the lazy import from the recent-SELL guard path and document why a normal import/shared helper is safe.
- Keep the broader duplicated recent-SELL guard block in `src/main.py` out of scope for this ticket.

## Validation

- `pytest tests/test_main.py -k "recent_sell_guard or suppresses_buy_above_recent_sell_price or resolve_market_setting_uses_session_profile_override" -v`
- `ruff check src/core/order_helpers.py src/main.py tests/test_main.py docs/architecture.md docs/plans/2026-03-20-issue-830-recent-sell-market-setting-design.md docs/plans/2026-03-20-issue-830-recent-sell-market-setting.md`
- `python3 scripts/validate_docs_sync.py`
