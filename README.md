# Bybit Candle Scanner → Telegram

Read-only scanner for Bybit USDT linear perpetual contracts. It selects up to 50 qualifying contracts by 24-hour turnover and looks for hammer, shooting star, morning star, and evening star formations on 30-minute, 1-hour, and 4-hour candles.

It does not place orders and requires no Bybit API key.

## What it checks

- Closed candles only; an unfinished candle cannot trigger a pattern.
- A preceding move is required (at least 2 ATR over six candles).
- A candidate must pass at least two of three context checks: proximity to a 50-candle extreme, volume at least 1.5 times its recent average, and RSI in an overbought/oversold zone.
- It reports a new formation, then updates it once as confirmed, invalidated, or not confirmed when the next candle closes. Confirmation is a mechanical rule, not a guarantee of future movement.
- It inspects recent bars so an hourly run can cover both 30-minute candles that closed since the previous run.
- It remembers alert states in `scan_state.json` to avoid repeat notifications. GitHub Actions caches that file between runs.

These are heuristic filters, not a proven profitable strategy. Candlestick results vary by market and evaluation method. Bybit's public API currently documents a broad IP request limit, but shared GitHub runner IPs and service policies can still change; this scanner waits 8 seconds between every Bybit request and stops on HTTP 403 rather than switching to mismatched market data. See [Bybit rate limits](https://bybit-exchange.github.io/docs/v5/rate-limit).

## Settings

| Variable | Default | Meaning |
|---|---:|---|
| `TIMEFRAMES` | `30m,1h,4h` | Bybit candle intervals scanned |
| `SYMBOL_LIMIT` | `50` | Maximum number of contracts |
| `MIN_TURNOVER_USDT` | `15000000` | Minimum 24-hour quote turnover |
| `QUOTE_CURRENCY` | `USDT` | Quote asset used for contract selection |
| `REQUEST_DELAY` | `8` | Minimum seconds between API requests |
| `MIN_CONTEXT` | `2` | Required context checks, from 0 to 3 |
| `SEND_EMPTY` | `0` | Set to `1` to send a no-pattern report |
| `STATE_FILE` | `scan_state.json` | Local alert-deduplication state path |

One hourly pass makes about 151 market-data requests (one market list plus up to 50 × 3 candle requests), so the configured pauses alone take about 20 minutes. GitHub Actions has a 45-minute job timeout and starts on the hour. The schedule can be delayed by GitHub.

## Run locally

Requires Python 3.10+ and a Telegram bot token/chat ID. Install dependencies and set the two environment variables in your shell. Never put the token in the source code or commit it.

```powershell
python -m pip install -r requirements.txt
$env:TELEGRAM_BOT_TOKEN = "your-token"
$env:TELEGRAM_CHAT_ID = "your-chat-id"
python main.py
```

## Run on GitHub Actions

1. Add these files to your GitHub repository.
2. In **Settings → Secrets and variables → Actions**, add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. Enable Actions and run **Candle Pattern Scanner → Run workflow** once.
4. Scheduled scans run hourly. They only send Telegram messages when a new formation or a status update is found, unless `SEND_EMPTY=1`.

The alert links to the Bybit perpetual chart for the same contract. An alert is a prompt to inspect the chart, not an instruction to trade. False signals remain possible; test rules with historical data and fees before risking money.
