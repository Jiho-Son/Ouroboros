# OOR-862 Market Lifecycle Reconciler Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** realtime loop 에 per-market lifecycle reconciler 를 도입해 `opened`, `closed`, `session_changed` 를 분리 계산하고, close/session transition 후속 동작을 각 market 단위로 독립 실행한다.

**Architecture:** `src/main.py` 에 previous/current market snapshot 을 비교하는 lifecycle reconciler 와 event record 를 추가한다. loop 시작 시 current snapshot 을 계산하고, close/open/session transition 을 각각 별도 helper/callback 으로 처리한다. session transition notification 포맷은 `src/notifications/telegram_client.py` 에 별도 메서드로 추가한다.

**Tech Stack:** Python, asyncio, pytest, unittest.mock

---

### Task 1: lifecycle diff contract 를 failing test 로 고정

**Files:**
- Modify: `tests/test_main.py`
- Modify: `src/main.py`

**Step 1: Write the failing test**

```python
def test_reconcile_market_lifecycle_separates_open_and_session_transition() -> None:
    diff = _reconcile_market_lifecycle(
        previous_market_states={"US_NASDAQ": "US_PRE", "KR": "KRX_REG"},
        current_market_sessions={"US_NASDAQ": "US_REG", "US_NYSE": "US_PRE"},
        current_markets={
            "US_NASDAQ": MARKETS["US_NASDAQ"],
            "US_NYSE": MARKETS["US_NYSE"],
        },
    )

    assert [event.market.code for event in diff.opened] == ["US_NYSE"]
    assert [event.market.code for event in diff.closed] == ["KR"]
    assert [event.market.code for event in diff.session_changed] == ["US_NASDAQ"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "reconcile_market_lifecycle" -v`
Expected: FAIL because reconciler helpers and event records do not exist yet

**Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class MarketLifecycleEvent:
    market: MarketInfo
    previous_session_id: str | None
    current_session_id: str | None

def _reconcile_market_lifecycle(...) -> MarketLifecycleDiff:
    ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "reconcile_market_lifecycle" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_main.py src/main.py docs/plans/2026-03-27-oor-862-market-lifecycle-reconciler*.md
git commit -m "test: define per-market lifecycle diff contract"
```

### Task 2: realtime loop 를 reconciler 기반으로 전환

**Files:**
- Modify: `src/main.py`
- Modify: `tests/test_main.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_run_realtime_mode_reconciles_close_and_session_transition_independently() -> None:
    ...
    assert close_handler.await_args.kwargs["market_code"] == "KR"
    session_transition_handler.assert_awaited_once()
    assert telegram.notify_market_open.await_count == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "reconciles_close_and_session_transition_independently" -v`
Expected: FAIL because current loop conflates first open and session transition and has no separate transition callback

**Step 3: Write minimal implementation**

```python
current_market_context = _build_current_market_context(open_markets)
diff = _reconcile_market_lifecycle(...)
await _handle_realtime_market_lifecycle_events(diff=diff, ...)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "reconciles_close_and_session_transition_independently" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_main.py src/main.py
git commit -m "fix: reconcile market lifecycle per market"
```

### Task 3: session transition notification/로그 포맷을 분리

**Files:**
- Modify: `src/notifications/telegram_client.py`
- Modify: `tests/test_telegram.py`
- Modify: `src/main.py`
- Modify: `tests/test_main.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_notify_market_session_transition_format() -> None:
    ...
    assert "Market Session Transition" in payload["text"]
    assert "US_PRE -> US_REG" in payload["text"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram.py -k "session_transition_format" -v`
Expected: FAIL because the notification method does not exist yet

**Step 3: Write minimal implementation**

```python
async def notify_market_session_transition(...):
    ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram.py -k "session_transition_format" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/notifications/telegram_client.py tests/test_telegram.py src/main.py tests/test_main.py
git commit -m "feat: add market session transition notification"
```

### Task 4: validation and documentation sync

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-27-oor-862-market-lifecycle-reconciler-design.md`
- Modify: `docs/plans/2026-03-27-oor-862-market-lifecycle-reconciler.md`

**Step 1: Run targeted tests**

Run: `pytest tests/test_main.py -k "reconcile_market_lifecycle or reconciles_close_and_session_transition_independently" -v`
Expected: PASS

**Step 2: Run notification regression**

Run: `pytest tests/test_telegram.py -k "session_transition_format" -v`
Expected: PASS

**Step 3: Run broader checks for touched surface**

Run: `ruff check src/ tests/`
Expected: PASS

Run: `pytest -v --cov=src --cov-report=term-missing`
Expected: PASS

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 4: Commit**

```bash
git add src/main.py src/notifications/telegram_client.py tests/test_main.py tests/test_telegram.py docs/plans/2026-03-27-oor-862-market-lifecycle-reconciler*.md workflow/session-handover.md
git commit -m "fix: split per-market lifecycle reconciliation"
```
