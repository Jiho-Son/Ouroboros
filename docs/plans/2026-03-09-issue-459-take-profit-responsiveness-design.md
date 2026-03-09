# Issue #459 Favorable Exit Responsiveness Design

## Context
- Issue `#459` asks for improved take-profit and trailing-exit responsiveness after the hard-stop websocket rollout.
- Hard-stop realtime execution is explicitly out of scope and remains covered by `#458`.
- The current staged-exit path already evaluates `peak_price`, ATR trailing, BE lock, and model/liquidity assist in `src/strategy/exit_manager.py`, but the realtime loop does not have a narrow path to ingest websocket-fed favorable-exit highs ahead of the next full trading cycle.

## Goals
- Improve favorable-exit responsiveness without changing hard-stop ownership or semantics.
- Preserve the existing staged-exit evaluation contract defined by `REQ-V2-004`.
- Keep realtime integration narrow enough that websocket data can augment peak tracking without forcing full staged-exit evaluation on every websocket event.

## Non-Goals
- No websocket-driven direct SELL execution for take-profit paths in this ticket.
- No change to hard-stop realtime execution paths from `#458`.
- No redefinition of ATR/model/liquidity semantics.

## Approaches Considered

### Recommended: realtime peak hint injection only
- Add a small API in the exit runtime helper layer that lets websocket handlers publish a higher favorable-exit peak for an already-open position.
- Keep staged-exit evaluation in the existing trading-cycle path.
- On the next HOLD override evaluation, combine persisted runtime peak, playbook/session highs, and websocket hint into the effective `peak_price`.

Why this is preferred:
- Preserves one place for exit semantics and decision logging.
- Avoids mixing websocket tick data with bar-derived ATR/model/liquidity inputs.
- Keeps the hard-stop websocket rollout isolated from favorable-exit logic.

### Rejected: full staged-exit evaluation on every websocket event
- Evaluate `evaluate_exit()` directly from websocket callbacks and trigger SELL immediately.

Why rejected:
- ATR and model/liquidity inputs are not naturally refreshed at websocket cadence.
- Duplicates exit orchestration between websocket callbacks and the trading loop.
- Raises the risk of semantic drift between hard-stop and favorable-exit paths.

## Design

### Runtime peak update contract
- Add an exported helper in `src/strategy/exit_manager.py` that updates the runtime peak cache only when:
  - the symbol has an open position runtime key, and
  - the new websocket price is finite and above the existing cached peak.
- Reuse the same runtime key shape as staged-exit evaluation so the websocket hint lands on the same position state.

### Trading-loop behavior
- Leave `_apply_staged_exit_override_for_hold()` as the only place that decides whether HOLD becomes SELL.
- Make that function consume the websocket-updated peak cache as part of `peak_price = max(entry, current, prior runtime peak, hint highs)`.
- Result: trailing-stop and armed-state exits react earlier on the next loop iteration without changing exit ordering.

### Realtime integration point
- Wire the new peak update helper into the existing KR websocket monitoring path added for hard-stop coverage, but only for favorable-exit high updates.
- Restrict the update to positive price/high values and open positions.
- Do not emit direct orders from this path.

### Logging and evidence
- Existing staged-exit evidence logging remains the source of truth.
- Add a small architecture note documenting that websocket highs can seed staged-exit peak tracking, while exit decisions still happen in the trading cycle.

## Testing
- Add a regression test proving websocket-fed peak updates tighten the trailing-stop threshold used by staged-exit evaluation.
- Add a regression test proving lower or invalid websocket values do not downgrade the cached peak.
- Run focused unit tests for exit manager behavior, then full regression suite.

## Traceability
- REQ: `REQ-V2-004`
- TASK: `TASK-CODE-002`
- TEST: `TEST-ACC-011`
