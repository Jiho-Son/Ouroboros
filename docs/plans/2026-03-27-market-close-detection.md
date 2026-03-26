# Individual Market Close Detection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Realtime 루프가 전체 시장 종료를 기다리지 않고, 닫힌 개별 마켓을 즉시 감지해 close handler 와 마켓별 runtime cache 정리를 정확히 한 번 수행하게 만든다.

**Architecture:** `src/main.py` 의 realtime 루프에서 `current open set` 과 `_market_states` 를 비교하는 helper 를 추가한다. 이 helper 는 닫힌 마켓에 대해 `_handle_market_close()` 를 호출하고, `_market_states` 및 마켓별 runtime cache 를 정리한 뒤 남아 있는 open 마켓 처리 흐름은 그대로 유지한다.

**Tech Stack:** Python, asyncio, pytest, unittest.mock

---

### Task 1: 닫힌 마켓 diff/cleanup helper 에 대한 failing test 추가

**Files:**
- Modify: `tests/test_main.py`
- Modify: `src/main.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_handle_realtime_market_closures_closes_removed_market_once() -> None:
    market_states = {"KR": "KRX_REG", "US_NASDAQ": "US_REG"}
    playbooks = {"KR": _make_playbook("KR"), "US_NASDAQ": _make_playbook("US_NASDAQ")}

    await main_module._handle_realtime_market_closures(
        current_open_markets=[MARKETS["US_NASDAQ"]],
        market_states=market_states,
        playbooks=playbooks,
        ...
    )

    assert "KR" not in market_states
    assert "KR" not in playbooks
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "handle_realtime_market_closures" -v`
Expected: FAIL with missing helper or missing immediate-close behavior

**Step 3: Write minimal implementation**

```python
async def _handle_realtime_market_closures(...):
    open_codes = {market.code for market in current_open_markets}
    for market_code in list(market_states):
        if market_code in open_codes:
            continue
        ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "handle_realtime_market_closures" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_main.py src/main.py docs/plans/2026-03-27-market-close-detection.md
git commit -m "fix: close realtime markets when individual markets end"
```

### Task 2: realtime 루프 통합 회귀 테스트 추가

**Files:**
- Modify: `tests/test_main.py`
- Modify: `src/main.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_run_closes_removed_market_while_other_market_stays_open() -> None:
    open_markets = [
        [MARKETS["KR"], MARKETS["US_NASDAQ"]],
        [MARKETS["US_NASDAQ"]],
        [MARKETS["US_NASDAQ"]],
    ]

    ...

    await main_module.run(settings)

    close_handler.assert_awaited_once()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "closes_removed_market_while_other_market_stays_open" -v`
Expected: FAIL because current loop only closes markets inside `if not open_markets:`

**Step 3: Write minimal implementation**

```python
open_markets = get_open_markets(...)
await _handle_realtime_market_closures(
    current_open_markets=open_markets,
    ...
)

if not open_markets:
    ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "closes_removed_market_while_other_market_stays_open" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_main.py src/main.py docs/plans/2026-03-27-market-close-detection.md
git commit -m "test: cover per-market close transitions in realtime loop"
```

### Task 3: 범위 검증과 문서 영향 점검

**Files:**
- Modify: `tests/test_main.py`
- Modify: `src/main.py`

**Step 1: Run targeted regression tests**

Run: `pytest tests/test_main.py -k "handle_realtime_market_closures or closes_removed_market_while_other_market_stays_open" -v`
Expected: PASS

**Step 2: Run broader checks for touched surface**

Run: `ruff check src tests && pytest tests/test_main.py -k "market_close or closes_removed_market_while_other_market_stays_open" -v`
Expected: PASS

**Step 3: Confirm docs impact**

```text
No user-facing workflow/doc contract changed; keep rationale for no additional docs update in PR/workpad.
```

**Step 4: Commit**

```bash
git add tests/test_main.py src/main.py docs/plans/2026-03-27-market-close-detection.md
git commit -m "docs: record OOR-858 implementation plan"
```
