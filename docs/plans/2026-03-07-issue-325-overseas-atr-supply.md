# Issue 325 Overseas ATR Supply Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend staged-exit feature injection so overseas holdings also receive real ATR values via the KIS overseas daily-price API, while preserving the existing KR path and RSI-based `pred_down_prob`.

**Architecture:** Add a dedicated overseas daily-price client method to `OverseasBroker`, add a matching ATR computation helper in `src/main.py`, and split `_inject_staged_exit_features()` into domestic/overseas ATR branches. Keep the current RSI proxy for `pred_down_prob`; only ATR sourcing changes in this task.

**Tech Stack:** Python, pytest, asyncio, aiohttp

---

### Task 1: Add Red Tests For Overseas ATR Flow

**Files:**
- Modify: `tests/test_overseas_broker.py`
- Modify: `tests/test_main.py`
- Modify: `src/broker/overseas.py`
- Modify: `src/main.py`

**Step 1: Write the failing test**

Add:
- an `OverseasBroker.get_daily_prices()` test that verifies `HHDFS76240000` and `/uapi/overseas-price/v1/quotations/dailyprice` with normalized exchange code and parsed OHLC rows
- a staged-exit feature injection test that verifies overseas holdings get non-zero `atr_value`

**Step 2: Run test to verify it fails**

Run:
- `pytest -q tests/test_overseas_broker.py -k get_daily_prices`
- `pytest -q tests/test_main.py -k inject_staged_exit_features_sets_atr_for_overseas`

Expected: FAIL because overseas daily-price support does not exist yet.

**Step 3: Write minimal implementation**

Implement:
- `OverseasBroker.get_daily_prices(exchange_code, stock_code, days=20)`
- `_compute_overseas_atr_value(...)`
- `_inject_staged_exit_features(..., overseas_broker=...)` branch for non-domestic markets

**Step 4: Run test to verify it passes**

Run:
- `pytest -q tests/test_overseas_broker.py -k get_daily_prices`
- `pytest -q tests/test_main.py -k inject_staged_exit_features_sets_atr_for_overseas`

Expected: PASS.

**Step 5: Commit**

```bash
git add docs/plans/2026-03-07-issue-325-overseas-atr-supply.md src/broker/overseas.py src/main.py tests/test_overseas_broker.py tests/test_main.py
git commit -m "feat: add overseas ATR supply for staged exits"
```
