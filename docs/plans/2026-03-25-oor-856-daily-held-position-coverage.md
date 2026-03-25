# OOR-856 Daily Held Position Coverage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `TRADE_MODE=daily` 경로가 scanner `top_n` 밖의 기존 보유 포지션도 반드시 평가하도록 바꾸고, entry ranking 과 mandatory exit coverage 를 분리한 계약을 테스트와 문서로 고정한다.

**Architecture:** daily mode 는 scanner 가 만든 신규 진입 candidate set 을 그대로 유지하되, broker/DB open holding 으로 별도의 mandatory evaluation set 을 만든다. playbook/scenario 단계에는 scanner candidates 와 holdings context 를 함께 전달하고, market data / stock loop 는 `scanner_codes + held_codes` 합집합을 사용해 exit 평가 누락을 제거한다.

**Tech Stack:** Python, sqlite, pytest, Linear workpad, ruff

---

### Task 1: red 테스트로 held-position 누락을 고정

**Files:**
- Modify: `tests/test_main.py`
- Modify: `tests/test_db.py`

**Step 1: Write the failing test**

- `run_daily_session()` 에서 scanner 가 `CRCD`, `AAOX`, `LITX` 만 반환해도 DB open position `PLU` 가 daily evaluation loop 에 포함되는지 검증하는 async regression test 를 추가한다.
- DB helper 를 추가한다면 latest BUY rows 만 반환하는 helper contract test 를 함께 추가한다.

**Step 2: Run test to verify it fails**

Run: `pytest -v tests/test_main.py -k 'daily_session and held'`
Expected: FAIL because current daily path builds `watchlist` from scanner candidates only and skips held-only symbols.

Run: `pytest -v tests/test_db.py -k open_positions`
Expected: FAIL if the new helper is not implemented yet.

### Task 2: daily evaluation set 에 holdings 를 병합

**Files:**
- Modify: `src/main.py`
- Modify: `src/db.py`
- Test: `tests/test_main.py`
- Test: `tests/test_db.py`

**Step 1: Write minimal implementation**

- market-scoped open positions 를 읽는 DB helper 를 추가하거나 동등한 query helper 를 만든다.
- daily path 에서 scanner candidates 와 별개로 broker/DB holdings metadata 를 수집하는 helper 를 추가한다.
- `_run_daily_session_market()` 는 `scanner_codes + held_codes` 합집합으로 evaluation stock list 를 만들고, holdings-only symbols 도 `stocks_data` 와 `_process_daily_session_stock()` 로 흘려보낸다.
- playbook 생성 시 scanner candidates 는 그대로 유지하고, holdings metadata 는 `current_holdings` 로 전달한다.

**Step 2: Run focused tests**

Run: `pytest -v tests/test_main.py -k 'daily_session and held'`
Expected: PASS

Run: `pytest -v tests/test_db.py -k open_positions`
Expected: PASS

### Task 3: 문서화와 검증

**Files:**
- Modify: `docs/architecture.md`
- Modify: `tests/test_main.py`

**Step 1: Update docs**

- daily mode 에서 scanner ranking 이 신규 진입 후보만 결정하고, 기존 보유 포지션은 mandatory exit coverage 로 별도 병합된다는 설명을 architecture 문서에 추가한다.

**Step 2: Run validation**

Run: `pytest -v tests/test_main.py -k 'daily_session and held'`
Expected: PASS

Run: `pytest -v tests/test_db.py -k open_positions`
Expected: PASS

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS
