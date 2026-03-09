# Issue #458 Design - KR WebSocket Hard-Stop Monitoring

**Issue:** #458  
**Follow-up:** #459

## Goal

Reduce KR sell overshoot beyond the applied hard-stop threshold by monitoring open domestic positions with realtime WebSocket price events and submitting SELL orders through the existing execution path as soon as the hard-stop is breached.

## Scope

Included:
- KR open positions only
- Hard-stop monitoring only
- WebSocket-backed price event ingestion
- Duplicate-order prevention and structured evidence logging
- Tests covering monitor lifecycle and trigger behavior

Excluded:
- Take-profit and ATR trailing WebSocket migration
- Overseas market realtime exit changes
- Playbook generation changes
- Replacing the existing polling-based staged-exit loop

## Current Problem

The realtime trading loop evaluates exits on a periodic cycle. KR staged-exit logic computes a valid hard-stop threshold, but a fast move between loop iterations can push realized loss beyond the intended threshold before a SELL order is submitted. Recent `187660` execution evidence showed the applied threshold was `-3.5%`, but the order was sent at `-5.13%` because the breach was detected on the next periodic evaluation.

## Design Options

### Option A: Hard-stop-only WebSocket monitor

- Add a dedicated KR realtime monitor that tracks open positions and their hard-stop price.
- Subscribe to domestic realtime trade/quote events.
- When the latest price is at or below the hard-stop price, trigger the existing sell execution path.
- Keep staged-exit take-profit logic on the periodic loop.

Pros:
- Directly addresses the risk-control gap
- Smallest change set
- Preserves current staged-exit semantics for non-hard-stop exits

Cons:
- Two exit paths must share order guards cleanly
- Hard-stop still cannot guarantee exact threshold fills during gaps

### Option B: WebSocket hard-stop plus trailing peak updates

- Implement Option A and also feed peak-price updates from WebSocket into the runtime exit cache.

Pros:
- Better trailing responsiveness later

Cons:
- More shared-state complexity now
- Blurs scope with #459

### Option C: Full staged-exit evaluation on each WebSocket event

Pros:
- Lowest theoretical exit latency

Cons:
- Overly large scope
- ATR/model/liquidity inputs are not all naturally tick-driven
- Higher risk of inconsistent state transitions

## Recommended Approach

Adopt Option A for #458 and leave Option B/C for #459 if needed. The hard-stop path is the urgent risk-control gap and can be isolated without rewriting the entire exit engine.

## Architecture

### 1. Realtime hard-stop monitor service

Add a new KR-specific runtime service responsible for:
- tracking which open positions need WebSocket monitoring
- storing the hard-stop threshold price per position
- receiving domestic realtime price events
- deduplicating trigger attempts while a SELL is in flight

This service should be independent from playbook generation and scanner logic. It should depend on existing broker auth/config and emit minimal, auditable runtime logs.

### 2. Explicit monitor inputs

Each monitored position needs:
- `market_code`
- `stock_code`
- `entry_price`
- `quantity`
- `hard_stop_pct`
- derived `hard_stop_price`
- `decision_id` and position timestamp for stable identity

The service should not recompute staged-exit policy on every tick. Instead, the periodic loop remains the source of truth for policy inputs, and publishes the current hard-stop threshold when a KR position is discovered or refreshed.

### 3. Trigger path reuse

When WebSocket price `<= hard_stop_price`, the monitor should call an extracted sell helper that reuses the current SELL submission behavior:
- quantity resolution
- order policy validation
- broker order submission
- trade logging
- decision logging / rationale text
- cooldown and runtime-exit cache cleanup

The monitor should not implement a second bespoke sell stack.

## Data Flow

1. Periodic trading loop discovers or refreshes KR open positions.
2. For each open KR position, staged-exit logic computes the effective hard-stop threshold.
3. The loop registers or refreshes the position in the realtime hard-stop monitor.
4. The monitor keeps a WebSocket subscription set for tracked symbols.
5. On each price event, if the price breaches the stored hard-stop price, the monitor marks the symbol as in-flight and invokes the shared sell execution helper.
6. On successful close or confirmed no-position state, the symbol is removed from monitoring.
7. If WebSocket is unavailable, the existing polling path remains active as fallback.

## State and Concurrency Rules

- One active hard-stop trigger per symbol at a time
- Periodic loop may refresh monitor metadata, but must not clear an in-flight trigger
- Monitor registration must be idempotent
- Closed positions must be removed promptly to avoid stale subscriptions
- WebSocket reconnect must rebuild subscriptions from current tracked positions

## Failure Handling

- WebSocket disconnect: reconnect with backoff and rebuild subscriptions
- Subscribe failure for one symbol: log and retain polling fallback
- SELL submission failure: clear in-flight guard after logging so polling can retry
- Position already closed by another path: treat as benign and remove from monitor
- Token/auth issues: rely on existing broker auth path where possible; avoid parallel auth implementations

## Logging and Evidence

Add structured logs for:
- monitor registration/update/removal
- WebSocket connect/disconnect/reconnect
- hard-stop trigger event with `stock_code`, `last_price`, `hard_stop_price`, `source=websocket_hard_stop`
- duplicate-trigger suppression

Decision/trade evidence should clearly distinguish WebSocket-triggered hard-stop exits from polling-loop exits.

## Testing Strategy

Unit/integration tests should cover:
- registering a KR position computes and stores the stop price correctly
- price event above stop does nothing
- price event at/below stop triggers exactly one SELL
- duplicate price events do not submit duplicate orders while in-flight
- closing/removing a position unsubscribes or disables monitoring
- reconnect rebuilds subscriptions from tracked positions
- polling fallback still functions when monitor is absent or disconnected

## External Reference

Implementation should follow the current official KIS Open API sources for WebSocket usage:
- KIS Developers portal documents separate WebSocket environments for development/production
- Official `koreainvestment/open-trading-api` sample repository includes domestic stock WebSocket examples and HTS ID requirements

## Acceptance

- KR hard-stop breach is detected from realtime price events without waiting for the next full trading loop iteration
- SELL submission uses the existing order/logging pipeline
- No duplicate SELL orders are sent for the same breach event
- Existing staged-exit take-profit behavior remains unchanged
- Test coverage demonstrates monitor lifecycle, trigger behavior, and fallback safety
