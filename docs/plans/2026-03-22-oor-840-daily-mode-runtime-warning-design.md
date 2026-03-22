# OOR-840 Daily Mode Runtime Warning Design

## Context

`src/main.py` currently enters the daily-mode loop, runs `run_daily_session()`
immediately, then sleeps `SESSION_INTERVAL_HOURS` before the next batch.
The startup log only says `Daily trading mode: %d sessions every %d hours`, and
the runtime log after each batch only says `Next session in %.1f hours`.

That means operators do not get an explicit signal that:

- the daily batch cadence is anchored to the process start timestamp, and
- for a market such as `KR`, a process started late in the regular session can
  have only one remaining regular-session batch before the market closes.

The unattended reproduction for `KR` is:

- startup at `2026-03-23T09:31:00+09:00`
- next scheduled batch at `2026-03-23T15:31:00+09:00`
- `KR` regular close at `2026-03-23T15:30:00+09:00`

`get_open_markets(["KR"], now=start)` returns `["KR"]`, while the same call at
`next_batch` returns `[]`.

This unattended run treats the Linear issue body as the approved requirement for
the design.

## Constraints

- Keep the existing daily scheduler behavior unchanged; the ticket is about
  observability, not cadence redesign.
- Make the startup signal explicit without adding a new operator workflow.
- Emit the runtime warning only when there is truly no additional regular-session
  batch before that market closes.
- Support lunch-break markets correctly; a next batch during lunch should not
  warn if a later scheduled batch still lands before close.
- Keep documentation aligned with the runtime semantics.

## Options

### Option 1: Startup-only log/notification

- Pros: smallest surface area.
- Cons: misses the per-market live warning required by the ticket and leaves the
  "last batch before close" case implicit.

### Option 2: Add daily-mode helper functions that log startup cadence and warn per market when the current batch is the last regular-session opportunity

- Pros: keeps behavior unchanged, makes the operator signal explicit, and allows
  deterministic tests around schedule math.
- Cons: adds a small amount of scheduling logic inside `src/main.py`.

### Option 3: Redesign the daily scheduler to anchor to market open instead of process start

- Pros: could reduce the operator surprise directly.
- Cons: changes runtime behavior, exceeds ticket scope, and contradicts the
  documented "intended behavior" from the issue background.

## Recommendation

Choose Option 2.

The root cause is not the batch cadence itself; it is the lack of explicit
runtime signal around the existing startup-anchored cadence. The safest change is:

- log the actual daily-mode anchor timestamp and next scheduled batch at startup,
- compute whether any later scheduled batch still lands inside each currently
  open market's regular session,
- warn when the current batch is the last regular-session opportunity for that
  market,
- document that the first daily batch is immediate and subsequent batches stay
  anchored to that startup timestamp.

## Intended Behavior

- When daily mode starts, logs explicitly state that the first batch runs
  immediately and the next batch time is derived from the process-start anchor.
- Before each daily batch, any currently open market that has no later scheduled
  regular-session batch before close emits a warning with:
  - the market identifier,
  - the current batch time in that market's timezone,
  - the next scheduled batch time in that market's timezone.
- Markets with lunch breaks only warn when no later scheduled batch after lunch
  still lands before close.
- Existing batch timing and trading execution remain unchanged.

## Test Strategy

- Add pure helper coverage for:
  - startup-anchored last-batch detection for `KR`,
  - lunch-break handling where a later batch after lunch still prevents warning.
- Add a daily-mode `run()` regression that:
  - proves the startup anchor log is emitted,
  - proves the `KR` warning is emitted for a late `09:31 KST` startup.
- Update docs to match the startup-anchored cadence and warning semantics.

## Documentation Update

Update `docs/architecture.md` near the daily-mode section to state that the
first batch runs immediately at process start, later batches are spaced by
`SESSION_INTERVAL_HOURS` from that anchor, and the runtime warns when a market
has no additional regular-session batch before close.
