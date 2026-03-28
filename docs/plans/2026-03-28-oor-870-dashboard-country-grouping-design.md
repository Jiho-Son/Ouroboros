# OOR-870 Dashboard Country Grouping Design

## Context

`/api/status` and the overview surface currently group dashboard data by the raw
`market` value stored in SQLite. For U.S. activity, that means
`US_NASDAQ`/`US_NYSE`/`US_AMEX` render as separate dashboard markets even though
the product expectation is a single country-level `US` view, mirroring the
existing `KR` behavior.

The overview surface shares its market focus with:

- market summary cards from `/api/status`
- history filters from `/api/decisions`
- the P&L chart from `/api/pnl/history`
- open-position filtering from `/api/positions`

If only the visible label changes, clicking `US` would still query raw
exchange-level endpoints and show empty or partial data. The grouping therefore
has to be consistent across the overview data contract, not just in HTML.

## Options

### Option 1: Frontend-only label aliasing

- Pros: minimal backend changes
- Cons: totals remain split, `US` card clicks break filters/history/positions,
  and the API contract stays inconsistent

### Option 2: Dashboard backend grouping helper

- Pros: one normalization rule can be reused by overview endpoints, keeps raw
  market data untouched in the database, and supports `US` filters without
  changing other product surfaces
- Cons: requires careful aggregation of counts/P&L/status fields

### Option 3: Persist grouped market codes in the database

- Pros: simple dashboard queries afterward
- Cons: broad schema/producer impact well beyond this ticket and risks changing
  non-dashboard consumers

## Recommendation

Choose option 2.

Introduce a dashboard-local helper that maps exchange-level market codes to a
country-level overview key:

- `KR` remains `KR`
- `US_*` maps to `US`
- all other markets fall back to their existing code

Use that helper to:

- aggregate `/api/status` rows before building overview cards
- allow `/api/decisions?market=US` and expose grouped market filter options
- allow `/api/pnl/history?market=US`
- return grouped `market` values from `/api/positions` so overview filtering
  still works after selecting `US`

Keep diagnostics/playbook/scorecard/scenario endpoints on raw market codes for
now. Those surfaces are explicitly market-specific and are not part of the
shared overview focus path described in the ticket.

## Validation Strategy

1. Add a failing dashboard regression test that seeds all three U.S. exchanges
   and expects `/api/status` to return a single `US` entry with combined counts.
2. Add focused regression coverage for grouped `US` filters in decisions,
   positions, and P&L history so overview card clicks remain functional.
3. Run the targeted dashboard tests and `ruff check` on touched files.
