# OOR-837 Telegram Trade Symbol Display Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Change Telegram trade execution alerts to render `{name}({code})` and
thread the stock name through all trade notification paths.

**Architecture:** Keep trade alert formatting centralized in
`src/notifications/telegram_client.py`, add `stock_name` to the notification API,
and propagate that value from playbook/scanner/realtime hard-stop state in
`src/main.py` and `src/core/realtime_hard_stop.py`.

**Tech Stack:** Python, pytest, Markdown docs

---

### Task 1: Record the current signal and active context

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-22-issue-837-telegram-notify-symbol-design.md`
- Modify: `docs/plans/2026-03-22-issue-837-telegram-notify-symbol-display.md`

**Step 1: Capture the current trade alert text**

Run:

```bash
python3 - <<'PY'
import asyncio
from unittest.mock import AsyncMock, patch
from src.notifications.telegram_client import TelegramClient

async def main():
    client = TelegramClient(bot_token="123:abc", chat_id="456", enabled=True)
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    with patch("aiohttp.ClientSession.post", return_value=mock_resp) as mock_post:
        await client.notify_trade_execution(
            stock_code="TSLA",
            market="United States",
            action="SELL",
            quantity=5,
            price=250.50,
            confidence=92.0,
        )
        print(mock_post.call_args.kwargs["json"]["text"])
    await client.close()

asyncio.run(main())
PY
```

Expected: message contains `Symbol: <code>TSLA</code> (United States)`.

**Step 2: Record the reproduction and pull evidence in the Linear workpad**

Expected: workpad `Notes` and `Validation` include the command result and
`origin/main` sync outcome.

### Task 2: Write failing tests first

**Files:**
- Modify: `tests/test_telegram.py`
- Modify: `tests/test_main.py`

**Step 1: Update the formatter-level test**

Change the trade notification test to call `notify_trade_execution()` with
`stock_name` and assert the message contains `{name}({code})`.

**Step 2: Update main-path assertions**

Adjust targeted `tests/test_main.py` expectations so main trade execution and
realtime hard-stop flows pass `stock_name` into `telegram.notify_trade_execution`.

**Step 3: Run the targeted red tests**

Run:

```bash
pytest -q tests/test_telegram.py -k trade_execution_format
pytest -q tests/test_main.py -k "trade_execution_notification_sent or realtime_hard_stop"
```

Expected: FAIL because the implementation does not yet accept or propagate
`stock_name`.

### Task 3: Implement minimal propagation and formatting changes

**Files:**
- Modify: `src/notifications/telegram_client.py`
- Modify: `src/core/realtime_hard_stop.py`
- Modify: `src/main.py`

**Step 1: Extend the notification API**

Add `stock_name` to `notify_trade_execution()` and centralize `{name}({code})`
rendering there.

**Step 2: Propagate names through main trade paths**

- Trading cycle: derive from `stock_playbook.stock_name`
- Daily session: derive from `candidate_map` or playbook data
- Realtime hard-stop: persist `stock_name` in hard-stop tracking and use it when
  the SELL notification fires

**Step 3: Keep fallback behavior local and explicit**

If a path lacks a usable name, use a single formatter fallback rather than
duplicating special cases across call sites.

**Step 4: Re-run the targeted tests**

Run the same targeted pytest commands and expect PASS.

### Task 4: Update user-facing docs

**Files:**
- Modify: `src/notifications/README.md`

**Step 1: Update the trade execution example**

Replace the example symbol line so the README documents `{name}({code})`.

### Task 5: Verify and prepare review state

**Files:**
- Modify: `src/notifications/telegram_client.py`
- Modify: `src/core/realtime_hard_stop.py`
- Modify: `src/main.py`
- Modify: `tests/test_telegram.py`
- Modify: `tests/test_main.py`
- Modify: `src/notifications/README.md`
- Modify: `docs/plans/2026-03-22-issue-837-telegram-notify-symbol-design.md`
- Modify: `docs/plans/2026-03-22-issue-837-telegram-notify-symbol-display.md`
- Modify: `workflow/session-handover.md`

**Step 1: Run targeted tests**

Run:

```bash
pytest -q tests/test_telegram.py tests/test_main.py -k "trade_execution or realtime_hard_stop"
```

Expected: PASS

**Step 2: Run scoped lint/docs checks**

Run:

```bash
ruff check src/notifications/telegram_client.py src/core/realtime_hard_stop.py src/main.py tests/test_telegram.py tests/test_main.py
python3 scripts/validate_docs_sync.py
```

Expected: PASS

**Step 3: Run the repo standard verification**

Run:

```bash
pytest -v --cov=src --cov-report=term-missing
```

Expected: PASS

**Step 4: Commit**

Run:

```bash
git add workflow/session-handover.md \
  src/notifications/telegram_client.py \
  src/core/realtime_hard_stop.py \
  src/main.py \
  tests/test_telegram.py \
  tests/test_main.py \
  src/notifications/README.md \
  docs/plans/2026-03-22-issue-837-telegram-notify-symbol-design.md \
  docs/plans/2026-03-22-issue-837-telegram-notify-symbol-display.md
git commit -m "fix(notifications): show stock name with code in telegram alerts"
```

Expected: clean commit ready for PR creation and Linear linkage.
