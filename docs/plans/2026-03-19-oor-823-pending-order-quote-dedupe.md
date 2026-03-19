# OOR-823 Pending-Order Quote Dedupe Implementation Plan

**Goal:** Remove duplicated pending-order orderbook lookup code while preserving OOR-813 retry behavior and fallback semantics.

**Architecture:** Add one shared orderbook extraction utility in `src/broker/`, keep one pending-order helper for optional quote lookup and side selection, then switch the four domestic/overseas BUY/SELL retry paths to that helper. Cover the refactor with RED-first helper tests and the existing pending-order regression suite.

**Tech Stack:** Python async runtime, pytest, unittest.mock, Markdown docs/workpad.

---

### Task 1: Lock the helper contract with failing tests

**Files:**
- Create: `tests/test_pending_orders.py`

**Step 1: Write the failing tests**
- Add one async test that calls `src.broker.pending_orders._fetch_optional_quote_payload()` with a present but non-callable attribute and expects `{}`.
- Add one extraction test that proves the shared rule must accept both:
  - domestic aliases like `output1.stck_askp1` / `stck_bidp1`
  - overseas aliases like `output2.pask1` / `pbid1`

**Step 2: Verify RED**
- Run: `pytest tests/test_pending_orders.py -v`
- Expected: FAIL on current branch because non-callable quote attributes still raise and no shared extractor handles both alias sets from one utility boundary.

### Task 2: Implement the shared extraction utility and safe quote helper

**Files:**
- Create: `src/broker/orderbook_utils.py`
- Modify: `src/broker/pending_orders.py`
- Modify: `src/broker/overseas.py`

**Step 1: Add the extractor**
- Implement a utility that unwraps `output1` / `output2` / `output`, tolerates list wrappers, and returns the first positive ask/bid match from ordered alias tuples.

**Step 2: Harden optional quote fetching**
- Update `_fetch_optional_quote_payload()` so it returns `{}` when the attribute is missing or non-callable.
- Keep the awaitable handling and dict-only return contract.

**Step 3: Wire compatibility**
- Replace the local domestic parser with the new utility.
- Make `OverseasBroker._extract_orderbook_top_levels()` delegate to the same utility.

**Step 4: Verify GREEN**
- Run: `pytest tests/test_pending_orders.py -v`

### Task 3: Remove the four duplicated retry lookup blocks

**Files:**
- Modify: `src/broker/pending_orders.py`
- Modify: `tests/test_main.py` only if expectations need adjustment

**Step 1: Extract the common retry helper**
- Add one helper that fetches optional orderbook payload, logs quote-fetch failures, and returns either executable ask or executable bid depending on the retry side.

**Step 2: Replace call sites**
- Switch domestic BUY/SELL and overseas BUY/SELL retry branches to the helper.
- Preserve existing fallback multipliers, gap-cap behavior, and notification/rollback handling.

**Step 3: Run targeted regressions**
- Run: `pytest -v tests/test_main.py -k "HandleOverseasPendingOrders or HandleDomesticPendingOrders"`

### Task 4: Finish validation and publish

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: Linear workpad comment

**Step 1: Scope validation**
- `pytest tests/test_pending_orders.py -v`
- `pytest -v tests/test_main.py -k "HandleOverseasPendingOrders or HandleDomesticPendingOrders"`
- `pytest -v tests/test_broker.py tests/test_overseas_broker.py`

**Step 2: Repo validation**
- `ruff check src/ tests/`
- `python3 scripts/validate_docs_sync.py`
- `pytest -v --cov=src --cov-report=term-missing`

**Step 3: Publish**
- Commit the refactor and docs together.
- Push `feature/issue-823-pending-orders-quote-dedupe`.
- Create/link the PR, add label `symphony`, attach the PR to Linear, and refresh the single workpad comment with validation evidence.
