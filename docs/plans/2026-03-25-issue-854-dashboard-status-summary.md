# OOR-854 Dashboard Status Summary Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 시장별 상태 요약을 운영 판단 기준으로 재정의하고, `Diagnostics` 를 메인 상태 화면과 분리하며, summary/chart/history 간 `market` 연동 규칙을 테스트와 문서로 고정한다.

**Architecture:** `src/dashboard/app.py` 의 `/api/status` 를 richer market summary 계약으로 확장하고, `src/dashboard/static/index.html` 에 `Overview` / `Diagnostics` surface 와 overview market focus 상태를 도입한다. 메인 surface 는 `market` 차원만 chart/history 와 공유하고, diagnostics selectors 는 독립 상태로 유지한다.

**Tech Stack:** FastAPI, SQLite, static HTML/CSS/vanilla JS, pytest

---

### Task 1: Fix the contract with failing dashboard tests

**Files:**
- Modify: `tests/test_dashboard.py`
- Reference: `src/dashboard/app.py`
- Reference: `src/dashboard/static/index.html`

**Step 1: Write the failing test**

```python
def test_status_endpoint_returns_market_operating_summary(tmp_path: Path) -> None:
    app = _app(tmp_path)
    get_status = _endpoint(app, "/api/status")
    body = get_status()
    kr = body["markets"]["KR"]

    assert kr["open_position_count"] == 1
    assert kr["latest_decision_action"] == "BUY"
    assert kr["status_tone"] in {"active", "watching", "ready"}


def test_index_exposes_overview_and_diagnostics_surfaces(tmp_path: Path) -> None:
    app = _app(tmp_path)
    html = TestClient(app).get("/").text

    assert "Overview" in html
    assert "Diagnostics" in html
    assert "market-summary-grid" in html
    assert "diagnostics-surface" in html
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard.py -k "market_operating_summary or overview_and_diagnostics_surfaces" -v`
Expected: FAIL because `/api/status` does not yet return the richer market fields and the HTML does not yet expose the new surface markers.

**Step 3: Write minimal implementation**

```python
market_status[market]["open_position_count"] = ...
market_status[market]["latest_decision_action"] = ...
market_status[market]["status_tone"] = ...
```

```html
<button data-surface="overview">Overview</button>
<button data-surface="diagnostics">Diagnostics</button>
<section id="overview-surface">...</section>
<section id="diagnostics-surface" hidden>...</section>
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard.py -k "market_operating_summary or overview_and_diagnostics_surfaces" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_dashboard.py src/dashboard/app.py src/dashboard/static/index.html
git commit -m "feat: add market operating summary surfaces"
```

### Task 2: Add overview market linkage with TDD

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `src/dashboard/static/index.html`
- Reference: `src/dashboard/app.py`

**Step 1: Write the failing test**

```python
def test_index_documents_overview_market_linkage_rules(tmp_path: Path) -> None:
    app = _app_with_trace_decisions(tmp_path)
    html = TestClient(app).get("/").text

    assert "activeOverviewMarket" in html
    assert "syncOverviewMarket" in html
    assert "메인 화면에서는 market 필터만 공유" in html
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard.py -k "overview_market_linkage_rules" -v`
Expected: FAIL because the current JS does not define shared overview-market state or describe the linkage rule.

**Step 3: Write minimal implementation**

```javascript
let activeOverviewMarket = 'all';

function syncOverviewMarket(market, source) {
  activeOverviewMarket = market || 'all';
  document.getElementById('decision-market').value = activeOverviewMarket;
  fetchPnlHistory(currentDays, activeOverviewMarket);
  if (source !== 'history') fetchDecisions();
}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard.py -k "overview_market_linkage_rules" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_dashboard.py src/dashboard/static/index.html
git commit -m "feat: link overview market across chart and history"
```

### Task 3: Document the final contract and run full verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/commands.md`
- Modify: `tests/test_dashboard.py`
- Modify: `workflow/session-handover.md`

**Step 1: Write the failing test**

```python
def test_status_endpoint_returns_latest_session_and_cb_state(tmp_path: Path) -> None:
    app = _app(tmp_path)
    body = _endpoint(app, "/api/status")()

    assert body["markets"]["US_NASDAQ"]["latest_session_id"] is not None
    assert "circuit_breaker_status" in body["markets"]["KR"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard.py -k "latest_session_and_cb_state" -v`
Expected: FAIL until the final API fields are implemented.

**Step 3: Write minimal implementation**

```python
latest_decision = conn.execute(...)
market_status[market]["latest_session_id"] = latest_decision["session_id"]
market_status[market]["circuit_breaker_status"] = ...
```

Update docs to describe:
- overview vs diagnostics surfaces
- `market` linkage scope
- richer `/api/status` fields

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_dashboard.py -k "latest_session_and_cb_state" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_dashboard.py src/dashboard/app.py docs/architecture.md docs/commands.md workflow/session-handover.md
git commit -m "docs: capture dashboard linkage contract"
```

### Task 4: Final verification and publish readiness

**Files:**
- Modify: `src/dashboard/app.py`
- Modify: `src/dashboard/static/index.html`
- Modify: `tests/test_dashboard.py`
- Modify: `docs/architecture.md`
- Modify: `docs/commands.md`

**Step 1: Run targeted regression**

Run: `pytest tests/test_dashboard.py -v`
Expected: PASS

**Step 2: Run lint + docs sync**

Run: `ruff check src/ tests/`
Expected: PASS

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 3: Run broader repo verification**

Run: `pytest -v --cov=src --cov-report=term-missing`
Expected: PASS

**Step 4: Prepare workpad + branch publish**

```bash
git status --short
git add src/dashboard/app.py src/dashboard/static/index.html tests/test_dashboard.py docs/architecture.md docs/commands.md docs/plans/2026-03-25-issue-854-dashboard-status-summary-design.md docs/plans/2026-03-25-issue-854-dashboard-status-summary.md workflow/session-handover.md
git commit -m "feat: refine dashboard market status summary"
```

**Step 5: Publish**

```bash
git push -u origin feature/issue-854-dashboard-status-summary
gh pr create --base main --head feature/issue-854-dashboard-status-summary ...
```
