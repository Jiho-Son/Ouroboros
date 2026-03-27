# OOR-863 Session Playbook Selection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** session-scoped playbook persistence 위에 restart vs live session transition selection policy 를 명시적으로 올려, current session playbook reuse 와 fresh generation 규칙을 분리한다.

**Architecture:** `PlaybookStore` 는 current-session 최신 row 와 refresh timing metadata 를 돌려주는 entry API 를 제공하고, `src/main.py` 는 `resume_current_session` / `force_fresh_on_transition` intent 로 selection decision 을 계산한다. realtime restart 와 daily restart 는 current-session latest reuse 를 허용하고, live session transition 은 1회 fresh generation 을 강제한다.

**Tech Stack:** Python 3.12, SQLite, Pydantic, pytest, ruff

---

### Task 1: Store metadata-aware read API

**Files:**
- Modify: `src/strategy/playbook_store.py`
- Test: `tests/test_playbook_store.py`

**Step 1: Write the failing tests**

Add tests that prove:
- `load_latest_entry()` returns the current-session latest playbook plus `slot`
- `load_latest()` remains a compatibility wrapper around the entry API

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_playbook_store.py -k "load_latest_entry or current_session_metadata" -v`
Expected: FAIL because the entry API does not exist yet.

**Step 3: Write minimal implementation**

- Add a small dataclass/value object for stored playbook entries
- Implement `load_entry()` / `load_latest_entry()`
- Keep existing `load()` / `load_latest()` returning `DayPlaybook | None`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_playbook_store.py -k "load_latest_entry or current_session_metadata" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/strategy/playbook_store.py tests/test_playbook_store.py
git commit -m "feat: add playbook store entry metadata reads"
```

### Task 2: Encode explicit selection intent

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing tests**

Add tests that prove:
- realtime restart reuses stored `US_REG` / `KRX_REG` playbook for the same current session
- live session transition still forces fresh generation even if a matching current-session row already exists

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "stored_regular_session_playbook or force_fresh_on_transition" -v`
Expected: FAIL because current logic still hard-codes regular-session skip.

**Step 3: Write minimal implementation**

- Replace session-type hardcoded reuse rule with explicit selection intent
- Track per-market `force_fresh` state after live session transition
- Clear the force flag once the market gets a new current-session cache

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "stored_regular_session_playbook or force_fresh_on_transition" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: make playbook selection intent explicit"
```

### Task 3: Reuse latest current-session playbook in daily path

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

Add a test that proves `_load_or_generate_daily_playbook()` reuses a stored same-session mid refresh instead of only checking `slot="open"`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "daily_playbook_reuses_latest_current_session" -v`
Expected: FAIL because daily path only calls `playbook_store.load(... slot='open')`.

**Step 3: Write minimal implementation**

- Switch the daily helper to the store entry API
- Reuse latest current-session playbook when present
- Preserve held-only fallback behavior when no stored current-session playbook exists

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "daily_playbook_reuses_latest_current_session" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/main.py tests/test_main.py
git commit -m "feat: reuse latest daily playbook within session"
```

### Task 4: Verification and documentation sync

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-27-oor-863-session-playbook-selection-design.md`
- Modify: `docs/plans/2026-03-27-oor-863-session-playbook-selection.md`

**Step 1: Run targeted verification**

Run: `pytest tests/test_playbook_store.py tests/test_main.py -k "playbook and (session or latest or refresh)" -v`
Expected: PASS for the new session-selection coverage.

**Step 2: Run repo gates for touched surface**

Run: `ruff check src/ tests/`
Expected: PASS

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 3: Run completion verification**

Run: `pytest -v --cov=src --cov-report=term-missing`
Expected: PASS

**Step 4: Commit**

```bash
git add docs/plans workflow/session-handover.md
git commit -m "docs: record OOR-863 session playbook design and verification"
```
