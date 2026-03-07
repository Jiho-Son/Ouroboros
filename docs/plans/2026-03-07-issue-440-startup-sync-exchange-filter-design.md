# Issue 440 Startup Sync Exchange Filter Design

## Context

`#440` fixed exchange-specific holdings filtering in the overseas symbol universe and runtime holdings merge paths, but startup broker sync still reads overseas holdings without passing `exchange_code`.

That leaves one inconsistent path where a mixed overseas balance payload can record a held symbol under the wrong logical market during process startup.

## Chosen Approach

Apply the same `exchange_code=market.exchange_code` filter in `sync_positions_from_broker()` for overseas markets and add a regression test that reproduces a mixed NASD/NYSE payload.

## Alternatives Considered

1. Add the missing filter only in startup sync.
   This is the smallest change and matches the already-shipped pattern in the other two paths.

2. Wrap all overseas holdings extraction behind a dedicated helper.
   This would reduce future drift, but it expands scope beyond the single bug and is not needed to resolve the review finding.

## Validation

- Add a failing startup sync regression test first.
- Run the new targeted pytest case and verify it fails before the fix.
- Apply the minimal production change.
- Re-run the targeted pytest case and related lint checks.
