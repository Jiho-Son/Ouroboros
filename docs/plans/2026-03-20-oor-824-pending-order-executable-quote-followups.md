# OOR-824 Pending-Order Executable Quote Follow-Ups Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the PR `#829` follow-ups by clarifying config validation ownership, unifying orderbook extraction, tightening the pending-order quote async contract, documenting SELL retry policy, and removing unreachable retry-price branches.

**Architecture:** Keep pending-order retry ownership in `src/broker/pending_orders.py`, but lock each contract boundary with RED-first tests: one validator boundary in `src/config.py`, one shared orderbook extraction implementation in `src/broker/orderbook_utils.py`, one strict async quote-fetch helper, and one explicit SELL executable-bid retry policy. Replace defensive `None` checks with assertions once the tests prove the control flow.

**Tech Stack:** Python 3.12, pydantic validators, asyncio, pytest, unittest.mock, Markdown docs.

---

### Task 1: Lock the current gaps with failing tests

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_pending_orders.py`
- Modify: `tests/test_main.py`

**Step 1: Write the failing tests**

- Add a config test that proves provider validation does not own executable-gap JSON parsing while invalid JSON still fails settings construction.
- Add shared extraction tests that explicitly cover `output1`, `output2`, and `output`.
- Add quote-helper tests that require an async callable and reject sync-returning fallbacks.
- Add domestic and overseas SELL retry tests that provide executable bids and expect the resubmit price to use that bid rather than the multiplier fallback.

**Step 2: Run test to verify it fails**

Run: `pytest -v tests/test_config.py tests/test_pending_orders.py tests/test_main.py -k "gap_caps or SharedTopLevelExtraction or strict_async or executable_bid"`

Expected: FAIL because the current helper still accepts sync return values, SELL executable-bid behavior is not yet covered, and config validation ownership is not separated.

### Task 2: Split config validation responsibility

**Files:**
- Modify: `src/config.py`
- Modify: `tests/test_config.py`

**Step 1: Write minimal implementation**

- Add a dedicated model validator for executable gap-cap JSON validation.
- Remove `_parse_executable_quote_gap_caps_by_market()` invocation from `_validate_selected_llm_provider()`.
- Keep `executable_quote_gap_caps_by_market` as the cached parsing path.

**Step 2: Run tests to verify GREEN**

Run: `pytest -v tests/test_config.py`

Expected: PASS with invalid JSON still rejected and cached property behavior preserved.

### Task 3: Tighten quote helper contracts and unify extraction

**Files:**
- Modify: `src/broker/pending_orders.py`
- Modify: `src/broker/overseas.py`
- Modify: `src/broker/orderbook_utils.py`
- Modify: `tests/test_pending_orders.py`

**Step 1: Write minimal implementation**

- Remove `inspect.isawaitable` from `_fetch_optional_quote_payload()` and `await` the quote method directly.
- Keep `{}` fallback only for missing/non-callable methods and non-dict payload results.
- Make domestic and overseas top-level extraction share the same `extract_orderbook_top_levels()` implementation with support for `output1`, `output2`, and `output`.

**Step 2: Run tests to verify GREEN**

Run: `pytest -v tests/test_pending_orders.py`

Expected: PASS with the strict async contract and shared extraction coverage.

### Task 4: Document SELL retry policy and remove unreachable branches

**Files:**
- Modify: `src/broker/pending_orders.py`
- Modify: `tests/test_main.py`

**Step 1: Write minimal implementation**

- Add concise policy comments that SELL retry uses executable bid when present and intentionally skips gap-cap enforcement to avoid blocking exits.
- Replace post-gap-rejection `if new_price is None` branches with assertions aligned to `_resolve_retry_price_from_executable_quote()` control flow.
- Update domestic and overseas SELL retry tests to prove executable bid is used.

**Step 2: Run tests to verify GREEN**

Run: `pytest -v tests/test_main.py -k "test_sell_pending_is_cancelled_then_resubmitted or test_buy_pending_prefers_executable_best_ask"`

Expected: PASS with SELL price assertions now tied to executable bid when provided.

### Task 5: Finish verification and publish state

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: Linear workpad comment

**Step 1: Scope validation**

- `pytest -v tests/test_config.py tests/test_pending_orders.py`
- `pytest -v tests/test_main.py -k "HandleOverseasPendingOrders or HandleDomesticPendingOrders"`

**Step 2: Repo validation**

- `ruff check src/ tests/`
- `python3 scripts/validate_docs_sync.py`
- `pytest -v --cov=src --cov-report=term-missing`

**Step 3: Publish**

- Commit the code/docs changes together.
- Push `feature/issue-824-pending-order-executable-quote-follow-ups`.
- Create/link the PR, add label `symphony`, attach the PR to Linear, and refresh the single workpad comment with validation evidence.
