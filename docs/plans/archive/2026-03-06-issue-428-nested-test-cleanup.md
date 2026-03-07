# Issue 428 Nested Test Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restore pytest collection for the accidentally nested `TestExtractAvgPriceFromBalance` test cases in `tests/test_main.py`.

**Architecture:** Keep production code unchanged. Fix only test structure by moving the nested `test_returns_zero_*` and related cases back to class scope so pytest can collect them, then verify collection and execution with targeted commands.

**Tech Stack:** Python, pytest

---

### Task 1: Expose Nested Avg-Price Tests To Pytest

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

Use the existing misplaced test names as the failure signal by checking pytest collection output for:
- `test_returns_zero_when_field_empty_string`
- `test_returns_zero_when_stock_not_found`
- `test_returns_zero_when_output1_empty`
- `test_returns_zero_when_output1_key_absent`
- `test_handles_output1_as_dict`
- `test_case_insensitive_code_matching`
- `test_returns_zero_for_non_numeric_string`
- `test_returns_correct_stock_among_multiple`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py --collect-only -q | rg 'test_returns_zero_when_field_empty_string|test_returns_zero_when_stock_not_found|test_returns_zero_when_output1_empty|test_returns_zero_when_output1_key_absent|test_handles_output1_as_dict|test_case_insensitive_code_matching|test_returns_zero_for_non_numeric_string|test_returns_correct_stock_among_multiple'`

Expected: no matches because the tests are nested inside another test function and are not collected.

**Step 3: Write minimal implementation**

Move the nested test functions into `TestExtractAvgPriceFromBalance` class scope by fixing indentation only.

**Step 4: Run test to verify it passes**

Run:
- `pytest tests/test_main.py --collect-only -q | rg 'test_returns_zero_when_field_empty_string|test_returns_zero_when_stock_not_found|test_returns_zero_when_output1_empty|test_returns_zero_when_output1_key_absent|test_handles_output1_as_dict|test_case_insensitive_code_matching|test_returns_zero_for_non_numeric_string|test_returns_correct_stock_among_multiple'`
- `pytest -q tests/test_main.py -k 'returns_zero_when_field_empty_string or returns_zero_when_stock_not_found or returns_zero_when_output1_empty or returns_zero_when_output1_key_absent or handles_output1_as_dict or case_insensitive_code_matching or returns_zero_for_non_numeric_string or returns_correct_stock_among_multiple'`

Expected: collection finds all eight tests and targeted execution passes.

**Step 5: Commit**

```bash
git add docs/plans/2026-03-06-issue-428-nested-test-cleanup.md tests/test_main.py
git commit -m "test: restore nested avg-price tests collection"
```
