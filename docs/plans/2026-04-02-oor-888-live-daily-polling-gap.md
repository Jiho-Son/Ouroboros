# OOR-888 Live Daily Polling Gap Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** late-start regular session 에서 live daily mode 가 entry/staged-exit 평가를 `RESCAN_INTERVAL_SECONDS` cadence 로 다시 수행하게 만든다.

**Architecture:** daily loop 의 기본 `SESSION_INTERVAL_HOURS` cadence 는 유지하되, `MODE=live` + `TRADE_MODE=daily` + current regular session 조건에서만 다음 batch 시각을 더 짧은 poll candidate 로 줄인다. hard-stop websocket 은 그대로 독립된 realtime safety path 로 두고, entry/staged-exit 는 기존 `run_daily_session()` polling path 안에서 계속 평가한다.

**Tech Stack:** Python 3.12, asyncio, pytest, existing daily loop helpers in `src/main.py`, Markdown docs

---

### Task 1: failing late-start live-daily regression 추가

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

```python
def test_resolve_daily_mode_next_batch_at_caps_live_regular_session_gap() -> None:
    current_batch_started_at = datetime(2026, 3, 23, 0, 31, tzinfo=UTC)
    batch_completed_at = current_batch_started_at

    next_batch = main_module._resolve_daily_mode_next_batch_at(
        open_markets=[MARKETS["KR"]],
        current_batch_started_at=current_batch_started_at,
        batch_completed_at=batch_completed_at,
        session_interval=timedelta(hours=6),
        live_regular_session_poll_interval=timedelta(seconds=300),
    )

    assert next_batch == batch_completed_at + timedelta(seconds=300)
```

```python
@pytest.mark.asyncio
async def test_run_live_daily_mode_late_start_regular_session_repools_before_close(...) -> None:
    settings = _make_settings(
        MODE="live",
        TRADE_MODE="daily",
        ENABLED_MARKETS="KR",
        SESSION_INTERVAL_HOURS=6,
        RESCAN_INTERVAL_SECONDS=300,
    )
    ...
    run_daily_session_mock.assert_has_awaits([call(...), call(...)])
    assert "daily_cycle phase=4" in caplog.text
    assert "next_batch=2026-03-23T00:36:00+00:00" in caplog.text
    assert "last_regular_batch_markets=KR" not in caplog.text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "live_daily_mode_late_start_regular_session or caps_live_regular_session_gap" -v`

Expected: FAIL because current resolver does not yet accept/apply the live regular-session poll cap.

### Task 2: live-daily regular-session poll cap 구현

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Add minimal helper / resolver extension**

```python
def _resolve_live_daily_regular_session_poll_candidate(
    *,
    open_markets: list[MarketInfo],
    batch_completed_at: datetime,
    poll_interval: timedelta | None,
) -> datetime | None:
    if poll_interval is None or poll_interval <= timedelta(0):
        return None
    ...
```

- regular-session (`KRX_REG`, `US_REG`) 인 현재 open market 만 후보로 본다.
- `batch_completed_at + poll_interval` 이 여전히 같은 regular session 내부에 있을 때만
  candidate 로 사용한다.

**Step 2: Wire the candidate into daily next-batch resolution**

```python
next_scheduled_batch_at = _resolve_daily_mode_next_batch_at(
    open_markets=current_open_markets,
    current_batch_started_at=current_batch_started_at,
    batch_completed_at=batch_completed_at,
    session_interval=session_interval,
    live_regular_session_poll_interval=live_regular_session_poll_interval,
)
```

- live daily mode 에서만 `live_regular_session_poll_interval =
  timedelta(seconds=settings.RESCAN_INTERVAL_SECONDS)` 를 전달한다.
- paper daily mode 는 `None` 을 전달해 기존 동작을 유지한다.
- 기존 `US_PRE -> US_REG` catch-up logic 와 `last_regular_batch_markets` warning 계산은
  새 next batch 결과를 기준으로 계속 동작하게 둔다.

**Step 3: Run targeted tests**

Run: `pytest tests/test_main.py -k "live_daily_mode_late_start_regular_session or caps_live_regular_session_gap or warning_logs_startup_anchor or late_start_daily_cycle" -v`

Expected: PASS

### Task 3: architecture / checklist 문서 정합화

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/live-trading-checklist.md`
- Modify: `docs/plans/2026-04-02-oor-888-live-daily-polling-gap-design.md`

**Step 1: Update docs**

- architecture 에 live daily mode regular-session entry/staged-exit polling cadence 가
  `RESCAN_INTERVAL_SECONDS` 로 capped 된다는 설명 추가
- checklist 에 operator 가 live daily mode 에서 hard-stop websocket startup 뿐 아니라
  shortened regular-session polling cadence 도 확인해야 한다는 항목 추가

**Step 2: Run docs validation**

Run: `python3 scripts/validate_docs_sync.py`

Expected: PASS

### Task 4: scope verification and handoff evidence

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `src/main.py`
- Modify: `tests/test_main.py`
- Modify: `docs/architecture.md`
- Modify: `docs/live-trading-checklist.md`

**Step 1: Run focused verification**

Run: `pytest tests/test_main.py -k "live_daily_mode_late_start_regular_session or caps_live_regular_session_gap or warning_logs_startup_anchor or late_start_daily_cycle or starts_realtime_hard_stop_monitor" -v`

Expected: PASS

**Step 2: Run lint**

Run: `ruff check src/main.py tests/test_main.py docs/architecture.md docs/live-trading-checklist.md workflow/session-handover.md`

Expected: PASS

**Step 3: Update workpad**

- Linear workpad 에 reproduction, design decision, targeted verification, final diff 범위를 체크리스트와 함께 반영한다.
