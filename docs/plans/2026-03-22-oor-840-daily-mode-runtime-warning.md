# OOR-840 Daily Mode Runtime Warning Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make daily-mode startup and live runtime logs explicitly warn when the startup-anchored batch cadence leaves no further regular-session batch before market close.

**Architecture:** Keep the scheduler behavior unchanged. Add small daily-mode helpers in `src/main.py` to compute the startup anchor and detect when the current batch is the last regular-session opportunity for each open market, lock the behavior with targeted regression tests, and update architecture docs to match.

**Tech Stack:** Python, pytest, logging, Markdown docs

---

### Task 1: Capture the current startup-anchored signal

**Files:**
- Modify: `workflow/session-handover.md`

**Step 1: Reproduce the `KR` late-start schedule gap**

Run: `python3 - <<'PY'\nfrom datetime import datetime, timedelta, UTC\nfrom zoneinfo import ZoneInfo\nfrom src.markets.schedule import get_open_markets\nstart = datetime(2026, 3, 23, 9, 31, tzinfo=ZoneInfo('Asia/Seoul'))\nnext_batch = start + timedelta(hours=6)\nprint(start.isoformat())\nprint(next_batch.isoformat())\nprint([m.code for m in get_open_markets(['KR'], now=start.astimezone(UTC))])\nprint([m.code for m in get_open_markets(['KR'], now=next_batch.astimezone(UTC))])\nPY`

Expected: `KR` is open at `09:31 KST` and not open at `15:31 KST`, proving the current cadence can leave only one regular-session batch.

**Step 2: Record the signal in the Linear workpad**

Expected: workpad `Notes` contains the command/result plus pull/handover evidence.

### Task 2: Add failing tests first

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Add helper-level last-batch detection tests**

Add direct tests for a new helper that assert:

- `KR` at `2026-03-23 09:31 KST` with a 6-hour interval has no additional
  regular-session batch before close.
- a lunch-break market with a later post-lunch batch does not warn falsely.

**Step 2: Add a daily-mode run logging regression**

Patch the runtime loop so one daily batch executes, capture logs with `caplog`,
and assert:

- startup log mentions the cadence is anchored to process start,
- runtime warning includes `market=KR`, the current batch time, and the next
  scheduled batch time.

**Step 3: Run the targeted red tests**

Run: `pytest -q tests/test_main.py -k "daily_mode_batch_cadence or daily_mode_warning"`

Expected: FAIL because the current runtime does not emit the new log lines yet.

### Task 3: Implement the minimal runtime warning helpers

**Files:**
- Modify: `src/main.py`

**Step 1: Add helper functions for daily-mode cadence/warning decisions**

- compute the next scheduled batch from the current batch timestamp,
- detect whether any future scheduled batch still lands inside the current
  market's regular session,
- log the startup anchor message and per-market last-batch warning.

**Step 2: Wire the helpers into the daily-mode loop**

- capture the batch timestamp before each daily batch,
- emit the startup anchor log on the first daily batch,
- emit market warnings before running the batch when it is the last regular-session
  opportunity.

**Step 3: Re-run the targeted tests**

Run: `pytest -q tests/test_main.py -k "daily_mode_batch_cadence or daily_mode_warning"`

Expected: PASS

### Task 4: Document the runtime semantics

**Files:**
- Modify: `docs/architecture.md`

**Step 1: Update the daily-mode section**

Document that the first daily batch runs immediately at process start, later
batches are spaced by `SESSION_INTERVAL_HOURS` from that anchor, and the runtime
warns when no further regular-session batch remains before close.

### Task 5: Run verification and prepare review artifacts

**Files:**
- Modify: `src/main.py`
- Modify: `tests/test_main.py`
- Modify: `docs/architecture.md`
- Modify: `docs/plans/2026-03-22-oor-840-daily-mode-runtime-warning-design.md`
- Modify: `docs/plans/2026-03-22-oor-840-daily-mode-runtime-warning.md`
- Modify: `workflow/session-handover.md`

**Step 1: Run targeted main-loop tests**

Run: `pytest -q tests/test_main.py -k "daily_mode_batch_cadence or daily_mode_warning"`

Expected: PASS

**Step 2: Run scoped static/doc checks**

Run: `ruff check src/main.py tests/test_main.py docs/architecture.md docs/plans/2026-03-22-oor-840-daily-mode-runtime-warning-design.md docs/plans/2026-03-22-oor-840-daily-mode-runtime-warning.md`

Expected: PASS

**Step 3: Run docs sync validation**

Run: `python3 scripts/validate_docs_sync.py`

Expected: PASS

**Step 4: Run repo verification gate**

Run: `pytest -v --cov=src --cov-report=term-missing`

Expected: PASS

**Step 5: Commit**

Run:

```bash
git add workflow/session-handover.md \
  src/main.py \
  tests/test_main.py \
  docs/architecture.md \
  docs/plans/2026-03-22-oor-840-daily-mode-runtime-warning-design.md \
  docs/plans/2026-03-22-oor-840-daily-mode-runtime-warning.md
git commit -m "fix(runtime): warn on daily mode last regular batch"
```

Expected: clean commit ready for PR creation and Linear linkage
