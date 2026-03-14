# Unfilled Order Policy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop repeated unfilled retries from using stale last-trade prices when the executable quote is materially different.

**Architecture:** Add small quote-parsing helpers and one session-aware executable-gap policy, wire them into pending-order repricing for domestic and overseas markets, and keep the current one-retry-per-session behavior intact.

**Tech Stack:** Python, pytest, Pydantic settings, existing KIS domestic/overseas broker clients

---

### Task 1: Lock the current failure in tests

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Write the failing overseas pending-order test**

Add a regression where:
- the pending BUY is in a low-liquidity US session,
- the last trade is much lower than `pask1`,
- the test expects the retry to be cancelled instead of resubmitted.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "pending_order and quote_gap" -v`
Expected: FAIL because the current code still resubmits from `last * 1.004`.

**Step 3: Write the failing domestic pending-order test**

Add the same shape for the domestic retry path using `stck_askp1` / `stck_bidp1`.

**Step 4: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "domestic_pending and quote_gap" -v`
Expected: FAIL because the domestic retry path also ignores the orderbook.

### Task 2: Add the quote client and pricing helper

**Files:**
- Modify: `src/config.py`
- Modify: `src/broker/kis_api.py`
- Modify: `src/broker/overseas.py`
- Modify: `src/broker/pending_orders.py`
- Test: `tests/test_overseas_broker.py`

**Step 1: Add a configurable executable-gap cap**

Introduce one setting for the maximum allowed best-quote-vs-last gap in low-liquidity sessions, keeping it overrideable through session-risk profiles.

**Step 2: Add quote extraction helpers**

Expose small helpers that read:
- domestic best ask/bid from the domestic orderbook payload,
- overseas best ask/bid from the overseas orderbook payload.

**Step 3: Add the overseas orderbook API client**

Implement the official `해외주식 현재가 호가` call in `OverseasBroker` and add focused broker coverage for the request mapping.

**Step 4: Add the executable-gap policy**

In `pending_orders.py`, resolve the executable quote, compare it against the last trade, and return either:
- the executable quote to use for retry pricing, or
- a cancel-without-retry decision when the quote gap exceeds the cap in a low-liquidity session.

### Task 3: Wire the policy into pending-order retries and verify

**Files:**
- Modify: `src/broker/pending_orders.py`
- Modify: `tests/test_main.py`
- Modify: `docs/architecture.md`

**Step 1: Replace last-price repricing in both pending BUY/SELL paths**

Use the helper so:
- BUY retries submit at the best ask when acceptable,
- SELL retries submit at the best bid when acceptable,
- wide-gap low-liquidity retries cancel instead of re-entering.

**Step 2: Keep rollback/cooldown semantics intact**

Make sure cancel-only wide-gap decisions still flow through the existing notification, cooldown, and rollback logic that the current code uses after exhausted retries.

**Step 3: Update the architecture note**

Document that low-liquidity pending retries now use executable quotes and abort when the quote gap exceeds the policy cap.

**Step 4: Run focused verification**

Run: `pytest tests/test_main.py -k "pending and quote_gap" -v`
Expected: PASS.

**Step 5: Run touched-surface checks**

Run: `pytest tests/test_overseas_broker.py -k "orderbook" -v`
Expected: PASS.

Run: `ruff check src/config.py src/broker/kis_api.py src/broker/overseas.py src/broker/pending_orders.py tests/test_main.py tests/test_overseas_broker.py docs/architecture.md`
Expected: PASS.
