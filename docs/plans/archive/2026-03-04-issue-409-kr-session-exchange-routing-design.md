# Issue #409 Design - KR Session-Aware Exchange Routing

## Context
- Issue: #409 (bug: KR 세션별 거래소 미분리 - 스크리닝/주문/이중상장 우선순위 미처리)
- Related runtime observation targets: #318, #325
- Date: 2026-03-04
- Confirmed approach: Option 2 (routing module introduction)

## Goals
1. Ensure domestic screening uses session-specific exchange market code.
2. Ensure domestic order submission explicitly sets exchange routing code.
3. Add dual-listing routing priority logic (spread/liquidity aware) with safe fallback.
4. Keep existing behavior stable for non-KR flows and existing risk/order policy guards.
5. Enable runtime observability for #409 while monitoring #318/#325 in parallel.

## Non-Goals
- Replacing current session classification model.
- Introducing new market sessions or changing session boundaries.
- Refactoring overseas order flow.

## Architecture
### New Component
- Add `KRExchangeRouter` (new module, e.g. `src/broker/kr_exchange_router.py`).
- Responsibility split:
  - `classify_session_id`: session classification only.
  - `KRExchangeRouter`: final domestic exchange selection (`KRX`/`NXT`) for ranking and order.
  - `KISBroker`: inject resolved routing values into request params/body.

### Integration Points
- `KISBroker.fetch_market_rankings`
  - Session-aware market division code:
    - `KRX_REG` -> `J`
    - `NXT_PRE`, `NXT_AFTER` -> `NX`
- `KISBroker.send_order`
  - Explicit `EXCG_ID_DVSN_CD` is always set.
- `SmartVolatilityScanner._scan_domestic`
  - Ensure domestic ranking API path resolves exchange consistently with current session.

## Data Flow
1. Scanner path:
   - Determine `session_id`.
   - `resolve_for_ranking(session_id)`.
   - Inject `J` or `NX` into ranking API params.
2. Order path:
   - Pass `session_id` into order path.
   - `resolve_for_order(stock_code, session_id)`.
   - Single listing: session default exchange.
   - Dual listing: select by spread/liquidity heuristic when data is available.
   - Data unavailable/error: fallback to session default.
   - Send order with explicit `EXCG_ID_DVSN_CD`.
3. Observability:
   - Log `session_id`, `resolved_exchange`, `routing_reason`.

## Dual-Listing Routing Priority
- Preferred decision source: spread/liquidity comparison.
- Deterministic fallback: session-default exchange.
- Proposed reasons in logs:
  - `session_default`
  - `dual_listing_spread`
  - `dual_listing_liquidity`
  - `fallback_data_unavailable`

## Error Handling
- Router does not block order path when auxiliary data is unavailable.
- Fail-open strategy for routing selection (fallback to session default) while preserving existing API/network error semantics.
- `send_order` exchange field omission is forbidden by design after this change.

## Testing Strategy
### Unit
- Router mapping by session (`KRX_REG`, `NXT_PRE`, `NXT_AFTER`).
- Dual-listing routing priority and fallback.
- Broker order body includes `EXCG_ID_DVSN_CD`.
- Ranking params use session-aware market code.

### Integration/Regression
- `smart_scanner` domestic calls align with session exchange.
- Existing order policy tests remain green.
- Re-run regression sets covering #318/#325 related paths.

### Runtime Observation (24h)
- Restart program from working branch build.
- Run runtime monitor for up to 24h.
- Verify and track:
  - #409: session-aware routing evidence in logs.
  - #318: ATR dynamic stop evidence.
  - #325: ATR/pred_down_prob injection evidence.
- If anomalies are detected during monitoring, create separate issue tickets with evidence and links.

## Acceptance Criteria
1. No domestic ranking call uses hardcoded KRX-only behavior across NXT sessions.
2. No domestic order is sent without `EXCG_ID_DVSN_CD`.
3. Dual-listing path has explicit priority logic and deterministic fallback.
4. Tests pass for new and affected paths.
5. Runtime monitor evidence is collected for #409, #318, #325; anomalies are ticketed.

## Risks and Mitigations
- Risk: Increased routing complexity introduces regressions.
  - Mitigation: isolate router, high-coverage unit tests, preserve existing interfaces where possible.
- Risk: Runtime events for #318/#325 may not naturally occur in 24h.
  - Mitigation: mark as `NOT_OBSERVED` and keep issue state based on evidence policy; do not force-close without proof.

## Planned Next Step
- Invoke `writing-plans` workflow and produce implementation plan before code changes.
