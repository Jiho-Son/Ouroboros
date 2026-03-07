# Telegram Notifications

Real-time trading event notifications via Telegram Bot API.

## Setup

### 1. Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` command
3. Follow prompts to name your bot
4. Save the **bot token** (looks like `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)

### 2. Get Your Chat ID

**Option A: Using @userinfobot**
1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. Send `/start`
3. Save your numeric **chat ID** (e.g., `123456789`)

**Option B: Using @RawDataBot**
1. Message [@RawDataBot](https://t.me/rawdatabot) on Telegram
2. Look for `"id":` in the JSON response
3. Save your numeric **chat ID**

### 3. Configure Environment

Add to your `.env` file:

```bash
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
TELEGRAM_ENABLED=true
```

### 4. Test the Bot

Start a conversation with your bot on Telegram first (send `/start`), then run:

```bash
python -m src.main --mode=live
```

You should receive a startup notification.

## Message Examples

### Trade Execution
```
🟢 BUY
Symbol: AAPL (United States)
Quantity: 10 shares
Price: 150.25
Confidence: 85%
```

### Circuit Breaker
```
🚨 CIRCUIT BREAKER TRIPPED
P&L: -3.15% (threshold: -3.0%)
Trading halted for safety
```

### Fat-Finger Protection
```
⚠️ Fat-Finger Protection
Order rejected: TSLA
Attempted: 45.0% of cash
Max allowed: 30%
Amount: 45,000 / 100,000
```

### Market Open/Close
```
ℹ️ Market Open
Korea trading session started

ℹ️ Market Close
Korea trading session ended
📈 P&L: +1.25%
```

### System Status
```
📝 System Started
Mode: PAPER
Markets: KRX, NASDAQ

System Shutdown
Normal shutdown
```

## Notification Priorities

| Priority | Emoji | Use Case |
|----------|-------|----------|
| LOW | ℹ️ | Market open/close |
| MEDIUM | 📊 | Trade execution, system start/stop |
| HIGH | ⚠️ | Fat-finger protection, errors |
| CRITICAL | 🚨 | Circuit breaker trips |

## Rate Limiting

- Default: 1 message per second
- Prevents hitting Telegram's global rate limits
- Configurable via `rate_limit` parameter

## Troubleshooting

### No notifications received

1. **Check bot configuration**
   ```bash
   # Verify env variables are set
   grep TELEGRAM .env
   ```

2. **Start conversation with bot**
   - Open bot in Telegram
   - Send `/start` command
   - Bot cannot message users who haven't started a conversation

3. **Check logs**
   ```bash
   # Look for Telegram-related errors
   python -m src.main --mode=live 2>&1 | grep -i telegram
   ```

4. **Verify bot token**
   ```bash
   curl https://api.telegram.org/bot<YOUR_TOKEN>/getMe
   # Should return bot info (not 401 error)
   ```

5. **Verify chat ID**
   ```bash
   curl -X POST https://api.telegram.org/bot<YOUR_TOKEN>/sendMessage \
     -H 'Content-Type: application/json' \
     -d '{"chat_id": "<YOUR_CHAT_ID>", "text": "Test"}'
   # Should send a test message
   ```

### Notifications delayed

- Check rate limiter settings
- Verify network connection
- Look for timeout errors in logs

### "Chat not found" error

- Incorrect chat ID
- Bot blocked by user
- Need to send `/start` to bot first

### "Unauthorized" error

- Invalid bot token
- Token revoked (regenerate with @BotFather)

## Graceful Degradation

The system works without Telegram notifications:

- Missing credentials → notifications disabled automatically
- API errors → logged but trading continues
- Network timeouts → trading loop unaffected
- Rate limiting → messages queued, trading proceeds

**Notifications never crash the trading system.**

## Security Notes

- Never commit `.env` file with credentials
- Bot token grants full bot control
- Chat ID is not sensitive (just a number)
- Messages are sent over HTTPS
- No trading credentials in notifications

## Advanced Usage

### Group Notifications

1. Add bot to Telegram group
2. Get group chat ID (negative number like `-123456789`)
3. Use group chat ID in `TELEGRAM_CHAT_ID`

### Multiple Recipients

Create multiple bots or use a broadcast group with multiple members.

### Custom Rate Limits

Not currently exposed in config, but can be modified in code:

```python
telegram = TelegramClient(
    bot_token=settings.TELEGRAM_BOT_TOKEN,
    chat_id=settings.TELEGRAM_CHAT_ID,
    rate_limit=2.0,  # 2 messages per second
)
```

## Bidirectional Commands

Control your trading bot remotely via Telegram commands. The bot not only sends notifications but also accepts commands for real-time control.

### Available Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message with quick start guide |
| `/help` | List all available commands |
| `/status` | Current trading status (mode, markets, P&L, circuit breaker) |
| `/positions` | View current holdings grouped by market |
| `/stop` | Pause all trading operations |
| `/resume` | Resume trading operations |

### Command Examples

**Check Trading Status**
```
You: /status

Bot:
📊 Trading Status

Mode: PAPER
Markets: Korea, United States
Trading: Active

Current P&L: +2.50%
Circuit Breaker: -3.0%
```

**View Holdings**
```
You: /positions

Bot:
💼 Current Holdings

🇰🇷 Korea
• 005930: 10 shares @ 70,000
• 035420: 5 shares @ 200,000

🇺🇸 Overseas
• AAPL: 15 shares @ 175
• TSLA: 8 shares @ 245

Cash: ₩5,000,000
```

**Pause Trading**
```
You: /stop

Bot:
⏸️ Trading Paused

All trading operations have been suspended.
Use /resume to restart trading.
```

**Resume Trading**
```
You: /resume

Bot:
▶️ Trading Resumed

Trading operations have been restarted.
```

### Security

**Chat ID Verification**
- Commands are only accepted from the configured `TELEGRAM_CHAT_ID`
- Unauthorized users receive no response
- Command attempts from wrong chat IDs are logged

**Authorization Required**
- Only the bot owner (chat ID in `.env`) can control trading
- No way for unauthorized users to discover or use commands
- All command executions are logged for audit

### Configuration

Add to your `.env` file:

```bash
# Commands are enabled by default
TELEGRAM_COMMANDS_ENABLED=true

# Polling interval (seconds) - how often to check for commands
TELEGRAM_POLLING_INTERVAL=1.0
```

To disable commands but keep notifications:
```bash
TELEGRAM_COMMANDS_ENABLED=false
```

### How It Works

1. **Long Polling**: Bot checks Telegram API every second for new messages
2. **Command Parsing**: Messages starting with `/` are parsed as commands
3. **Authentication**: Chat ID is verified before executing any command
4. **Execution**: Command handler is called with current bot state
5. **Response**: Result is sent back via Telegram

### Error Handling

- Command parsing errors → "Unknown command" response
- API failures → Graceful degradation, error logged
- Invalid state → Appropriate message (e.g., "Trading is already paused")
- Trading loop isolation → Command errors never crash trading

### Troubleshooting Commands

**Commands not responding**
1. Check `TELEGRAM_COMMANDS_ENABLED=true` in `.env`
2. Verify you started conversation with `/start`
3. Check logs for command handler errors
4. Confirm chat ID matches `.env` configuration

**Wrong chat ID**
- Commands from unauthorized chats are silently ignored
- Check logs for "unauthorized chat_id" warnings

**Delayed responses**
- Polling interval is 1 second by default
- Network latency may add delay
- Check `TELEGRAM_POLLING_INTERVAL` setting

## API Reference

See `telegram_client.py` for full API documentation.

### Notification Methods
- `notify_trade_execution()` - Trade alerts
- `notify_circuit_breaker()` - Emergency stops
- `notify_fat_finger()` - Order rejections
- `notify_market_open/close()` - Session tracking
- `notify_system_start/shutdown()` - Lifecycle events
- `notify_error()` - Error alerts

### Command Handler
- `TelegramCommandHandler` - Bidirectional command processing
- `register_command()` - Register custom command handlers
- `start_polling()` / `stop_polling()` - Lifecycle management
