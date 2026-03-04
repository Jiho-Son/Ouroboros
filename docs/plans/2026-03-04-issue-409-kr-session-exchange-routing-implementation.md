# Issue #409 KR Session Exchange Routing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix #409 by making KR screening/order routing session-aware and adding dual-listing exchange priority with deterministic fallback, then run 24h runtime observation for #409/#318/#325.

**Architecture:** Introduce a dedicated `KRExchangeRouter` module that resolves exchange by session and dual-listing metadata. Keep session classification in `order_policy`, and inject router outputs into `KISBroker` ranking/order requests. Add explicit routing logs for runtime evidence and keep non-KR behavior unchanged.

**Tech Stack:** Python 3.12, aiohttp client layer, pytest/pytest-asyncio, Gitea CLI (`tea`), bash runtime monitor scripts.

---

### Task 1: Preflight and Branch Runtime Gate

**Files:**
- Modify: `workflow/session-handover.md`

**Step 1: Add handover entry for this ticket branch**

```md
### 2026-03-04 | session=codex-issue409-start
- branch: feature/issue-409-kr-session-exchange-routing
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #409, #318, #325
- next_ticket: #409
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: #409 code fix + 24h monitor, runtime anomaly creates separate issue ticket
```

**Step 2: Run strict handover check**

Run: `python3 scripts/session_handover_check.py --strict`
Expected: PASS

**Step 3: Commit**

```bash
git add workflow/session-handover.md
git commit -m "chore: add handover entry for issue #409"
```

### Task 2: Add Router Unit Tests First (TDD)

**Files:**
- Create: `tests/test_kr_exchange_router.py`

**Step 1: Write failing tests for session mapping**

```python
from src.broker.kr_exchange_router import KRExchangeRouter


def test_ranking_market_code_by_session() -> None:
    router = KRExchangeRouter()
    assert router.resolve_for_ranking("KRX_REG") == "J"
    assert router.resolve_for_ranking("NXT_PRE") == "NX"
    assert router.resolve_for_ranking("NXT_AFTER") == "NX"
```

**Step 2: Write failing tests for dual-listing fallback behavior**

```python
def test_order_exchange_falls_back_to_session_default_on_missing_data() -> None:
    router = KRExchangeRouter()
    resolved = router.resolve_for_order(
        stock_code="0001A0",
        session_id="NXT_PRE",
        is_dual_listed=True,
        spread_krx=None,
        spread_nxt=None,
        liquidity_krx=None,
        liquidity_nxt=None,
    )
    assert resolved.exchange_code == "NXT"
    assert resolved.reason == "fallback_data_unavailable"
```

**Step 3: Run tests to verify fail**

Run: `pytest tests/test_kr_exchange_router.py -v`
Expected: FAIL (`ModuleNotFoundError` or missing class)

**Step 4: Commit tests-only checkpoint**

```bash
git add tests/test_kr_exchange_router.py
git commit -m "test: add failing tests for KR exchange router"
```

### Task 3: Implement Router Minimal Code

**Files:**
- Create: `src/broker/kr_exchange_router.py`
- Modify: `src/broker/__init__.py`

**Step 1: Add routing dataclass + session default mapping**

```python
@dataclass(frozen=True)
class ExchangeResolution:
    exchange_code: str
    reason: str


class KRExchangeRouter:
    def resolve_for_ranking(self, session_id: str) -> str:
        return "NX" if session_id in {"NXT_PRE", "NXT_AFTER"} else "J"
```

**Step 2: Add dual-listing decision path + fallback**

```python
if is_dual_listed and spread_krx is not None and spread_nxt is not None:
    if spread_nxt < spread_krx:
        return ExchangeResolution("NXT", "dual_listing_spread")
    return ExchangeResolution("KRX", "dual_listing_spread")

return ExchangeResolution(default_exchange, "fallback_data_unavailable")
```

**Step 3: Run router tests**

Run: `pytest tests/test_kr_exchange_router.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add src/broker/kr_exchange_router.py src/broker/__init__.py
git commit -m "feat: add KR session-aware exchange router"
```

### Task 4: Broker Request Wiring (Ranking + Order)

**Files:**
- Modify: `src/broker/kis_api.py`
- Modify: `tests/test_broker.py`

**Step 1: Add failing tests for ranking param and order body exchange field**

```python
assert called_params["FID_COND_MRKT_DIV_CODE"] == "NX"
assert called_json["EXCG_ID_DVSN_CD"] == "NXT"
```

**Step 2: Run targeted test subset (fail first)**

Run: `pytest tests/test_broker.py -k "market_rankings or EXCG_ID_DVSN_CD" -v`
Expected: FAIL on missing field/value

**Step 3: Implement minimal wiring**

```python
session_id = runtime_session_id or classify_session_id(MARKETS["KR"])
market_div_code = self._kr_router.resolve_for_ranking(session_id)
params["FID_COND_MRKT_DIV_CODE"] = market_div_code

resolution = self._kr_router.resolve_for_order(...)
body["EXCG_ID_DVSN_CD"] = resolution.exchange_code
```

**Step 4: Add routing evidence logs**

```python
logger.info(
    "KR routing resolved",
    extra={"session_id": session_id, "exchange": resolution.exchange_code, "reason": resolution.reason},
)
```

**Step 5: Re-run broker tests**

Run: `pytest tests/test_broker.py -k "market_rankings or EXCG_ID_DVSN_CD" -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/broker/kis_api.py tests/test_broker.py
git commit -m "fix: apply KR exchange routing to rankings and orders"
```

### Task 5: Scanner Session Alignment

**Files:**
- Modify: `src/analysis/smart_scanner.py`
- Modify: `tests/test_smart_scanner.py`

**Step 1: Add failing test for domestic session-aware ranking path**

```python
assert mock_broker.fetch_market_rankings.call_args_list[0].kwargs["session_id"] == "NXT_PRE"
```

**Step 2: Run scanner tests (fail first)**

Run: `pytest tests/test_smart_scanner.py -k "session" -v`
Expected: FAIL on missing session argument

**Step 3: Implement scanner call wiring**

```python
fluct_rows = await self.broker.fetch_market_rankings(
    ranking_type="fluctuation",
    limit=50,
    session_id=session_id,
)
```

**Step 4: Re-run scanner tests**

Run: `pytest tests/test_smart_scanner.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/analysis/smart_scanner.py tests/test_smart_scanner.py
git commit -m "fix: align domestic scanner rankings with KR session routing"
```

### Task 6: Full Verification and Regression

**Files:**
- No new files

**Step 1: Run focused regressions for #409**

Run:
- `pytest tests/test_kr_exchange_router.py tests/test_broker.py tests/test_smart_scanner.py -v`
Expected: PASS

**Step 2: Run related runtime-path regressions for #318/#325**

Run:
- `pytest tests/test_main.py -k "atr or staged_exit or pred_down_prob" -v`
Expected: PASS

**Step 3: Run lint/type checks for touched modules**

Run:
- `ruff check src/broker/kis_api.py src/broker/kr_exchange_router.py src/analysis/smart_scanner.py tests/test_kr_exchange_router.py tests/test_broker.py tests/test_smart_scanner.py`
- `mypy src/broker/kis_api.py src/broker/kr_exchange_router.py src/analysis/smart_scanner.py --strict`
Expected: PASS

**Step 4: Commit final fixup if needed**

```bash
git add -A
git commit -m "chore: finalize #409 verification adjustments"
```

### Task 7: PR Creation, Self-Review, and Merge

**Files:**
- Modify: PR metadata only

**Step 1: Push branch**

Run: `git push -u origin feature/issue-409-kr-session-exchange-routing`
Expected: remote branch created

**Step 2: Create PR to `main` with issue links**

```bash
PR_BODY=$(cat <<'MD'
## Summary
- fix KR session-aware exchange routing for rankings and orders (#409)
- add dual-listing exchange priority with deterministic fallback
- add logs and tests for routing evidence

## Validation
- pytest tests/test_kr_exchange_router.py tests/test_broker.py tests/test_smart_scanner.py -v
- pytest tests/test_main.py -k "atr or staged_exit or pred_down_prob" -v
- ruff check ...
- mypy ...
MD
)

tea pr create --base main --head feature/issue-409-kr-session-exchange-routing --title "fix: KR session-aware exchange routing (#409)" --description "$PR_BODY"
```

**Step 3: Validate PR body integrity**

Run: `python3 scripts/validate_pr_body.py --pr <PR_NUMBER>`
Expected: PASS

**Step 4: Self-review checklist (blocking)**
- Re-check diff for missing `EXCG_ID_DVSN_CD`
- Confirm session mapping (`KRX_REG=J`, `NXT_PRE/NXT_AFTER=NX`)
- Confirm fallback reason logging exists
- Confirm tests cover dual-listing fallback

**Step 5: Merge only if no minor issues remain**

Run: `tea pr merge <PR_NUMBER> --merge`
Expected: merged

### Task 8: Restart Program and 24h Runtime Monitoring

**Files:**
- Runtime artifacts: `data/overnight/*.log`

**Step 1: Restart runtime from merged state**

Run:
- `bash scripts/stop_overnight.sh`
- `bash scripts/run_overnight.sh`
Expected: live process and watchdog healthy

**Step 2: Start 24h monitor**

Run:
- `INTERVAL_SEC=60 MAX_HOURS=24 POLICY_TZ=Asia/Seoul bash scripts/runtime_verify_monitor.sh`
Expected: monitor loop runs and writes `data/overnight/runtime_verify_*.log`

**Step 3: Track #409/#318/#325 evidence in loop**

Run examples:
- `rg -n "KR routing resolved|EXCG_ID_DVSN_CD|session=NXT_|session=KRX_REG" data/overnight/run_*.log`
- `rg -n "atr_value|dynamic hard stop|staged exit|pred_down_prob" data/overnight/run_*.log`

Expected:
- #409 routing evidence present when KR flows trigger
- #318/#325 evidence captured if runtime conditions occur

**Step 4: If anomaly found, create separate issue ticket immediately**

```bash
ISSUE_BODY=$(cat <<'MD'
## Summary
- runtime anomaly detected during #409 monitor

## Evidence
- log: data/overnight/run_xxx.log
- timestamp: <UTC/KST>
- observed: <symptom>

## Suspected Scope
- related to #409/#318/#325 monitoring path

## Next Action
- triage + reproducible test
MD
)

tea issues create -t "bug: runtime anomaly during #409 monitor" -d "$ISSUE_BODY"
```

**Step 5: Post monitoring summary to #409/#318/#325**
- Include PASS/FAIL/NOT_OBSERVED matrix and exact timestamps.
- Do not close #318/#325 without concrete acceptance evidence.
