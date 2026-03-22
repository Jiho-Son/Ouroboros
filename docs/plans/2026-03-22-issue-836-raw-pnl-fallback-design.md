# OOR-836 Raw PnL Unit Fallback Design

## Context

`src/strategy/pre_market_planner.py` maps scorecard raw PnL units by market via
`_RAW_PNL_UNIT_BY_MARKET`. Supported prompt labels are currently `KR -> KRW` and
`US -> USD`. For any unmapped market, `_raw_pnl_unit_for_market()` falls back to
returning the market code itself.

That behavior is silent and unsafe for prompt semantics. If a new market such as
`JP` is introduced before the mapping is updated, the planner prompt injects
`Realized PnL (JP, raw)` even though `JP` is a market identifier, not a quote
currency. The LLM then receives a misleading unit string with no warning signal.

## Constraints

- Keep the planner resilient; unsupported markets should not crash prompt generation.
- Do not silently reuse the unsupported market code as a pseudo-currency.
- Preserve current prompt behavior for supported markets.
- Add regression tests that prove the unsupported path changed.
- Reflect the contract change in nearby docs or code comments.

## Options

### Option 1: Raise on unsupported market

- Pros: forces mapping completeness immediately.
- Cons: prompt generation can fail for newly added markets and may trigger broader
  planner fallback behavior for what is really a labeling gap.

### Option 2: Return explicit generic fallback and log warning

- Example fallback: `UNKNOWN_CURRENCY`.
- Pros: planner remains operational, prompt no longer lies about the unit, and
  logs provide an observable signal for missing mapping updates.
- Cons: prompt becomes less specific until mapping is added.

### Option 3: Infer quote currency from market naming conventions

- Example: derive `JPY` from `JP`, derive `USD` from `US_*`.
- Pros: may reduce future mapping churn.
- Cons: bakes heuristics into a contract that should stay explicit, and risks new
  silent mislabels when market naming diverges from quote currency.

## Recommendation

Choose Option 2.

This ticket is about preventing silent corruption of the prompt contract without
degrading planner availability. An explicit fallback string plus a warning log is
the smallest safe change:

- supported markets still emit canonical units,
- unsupported markets become visibly unresolved instead of deceptively valid,
- operators get a warning signal that mapping maintenance is required.

## Intended Behavior

- `KR` returns `KRW`
- `US` returns `USD`
- any unsupported market logs a warning and returns `UNKNOWN_CURRENCY`
- planner prompt sections that render raw scorecard PnL inherit that explicit
  fallback label instead of echoing the market code

## Test Strategy

- Add a direct helper test proving unsupported market input no longer echoes the
  market code.
- Add prompt-level regression coverage proving `_build_prompt()` renders
  `UNKNOWN_CURRENCY` for an unsupported market scorecard path.
- Keep existing `KRW`/`USD` prompt coverage unchanged.

## Documentation Update

Update `docs/architecture.md` near the `DailyScorecard` contract note to state
that planner prompt rendering maps market -> raw PnL unit explicitly and falls
back to `UNKNOWN_CURRENCY` for unsupported markets until the mapping is extended.
