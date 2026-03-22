# OOR-837 Telegram Trade Symbol Display Design

## Context

`src/notifications/telegram_client.py` formats trade execution alerts as
`Symbol: <code>{stock_code}</code> ({market})`. The current contract exposes only
the stock code, even though the ticket requires `{name}({code})` so the message
shows what was bought or sold more clearly.

Current trade notification call sites in `src/main.py` pass `stock_code` but do
not pass a stock name. Two of those paths already have a nearby name source:

- trading cycle uses `DayPlaybook.stock_playbooks[*].stock_name`
- daily session builds `candidate_map` with `ScanCandidate.name`

The realtime hard-stop SELL path does not call Telegram directly from the place
where the name is known, so that path needs name propagation through the monitor
state to keep the notification contract consistent.

This unattended run treats the Linear issue body as the approved requirement for
the design.

## Constraints

- Keep existing BUY/SELL notification context, quantity, price, and confidence.
- Avoid adding a broker/network lookup just to render the name.
- Keep realtime hard-stop monitoring behavior unchanged except for carrying the
  display name through existing state.
- Add regression tests that prove the new message contract and name propagation.
- Update nearby notification docs because the output format is user-facing.

## Options

### Option 1: Change only Telegram formatting and require `stock_name`

- Pros: explicit API contract at the formatter boundary.
- Cons: every call site must already know the name; realtime hard-stop path
  still cannot satisfy it without additional propagation.

### Option 2: Pass a preformatted display string from each caller

- Pros: keeps formatter simple.
- Cons: duplicates formatting and fallback logic across call sites and tests.

### Option 3: Add `stock_name` to trade notifications and propagate it through existing contexts

- Pros: keeps formatting centralized, reuses existing playbook/scanner/realtime
  monitor state, and minimizes behavior changes outside message rendering.
- Cons: requires small signature updates in `TelegramClient`, `src/main.py`, and
  realtime hard-stop monitor state.

## Recommendation

Choose Option 3.

The root cause is not just formatting; the notification API currently lacks a
name input. The safest fix is:

- extend `notify_trade_execution()` to accept `stock_name`,
- centralize `{name}({code})` assembly inside `telegram_client.py`,
- thread `stock_name` from playbook/scanner contexts in `src/main.py`,
- persist `stock_name` in realtime hard-stop tracking so SELL alerts can use the
  same display contract later.

## Intended Behavior

- Trade execution alerts render `Symbol: <code>Samsung(005930)</code> (Korea)`
  style output.
- When a name is available, it is always paired with its code as `{name}({code})`.
- Realtime hard-stop SELL notifications use the same format as normal trade
  execution notifications.
- If a path genuinely lacks a name, fallback behavior stays explicit and local
  to the formatter instead of scattering per-call-site conditionals.

## Test Strategy

- Update `tests/test_telegram.py` to assert the rendered message contains
  `{name}({code})`.
- Update targeted `tests/test_main.py` assertions so main trade notification
  call sites pass `stock_name`.
- Add/revise realtime hard-stop monitor coverage so tracked state preserves the
  name needed for later SELL notifications.

## Documentation Update

Update `src/notifications/README.md` trade execution example so the documented
message matches the new `{name}({code})` contract.
