"""Hourly OKX linear-swap candlestick scanner with Telegram alerts."""
from __future__ import annotations

import html
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

import exchanges
from patterns import Candle, Params, Signal, scan


TIMEFRAMES = {"30m": 30 * 60, "1h": 60 * 60, "4h": 4 * 60 * 60}
TF_TV = {"30m": "30", "1h": "60", "4h": "240"}
TF_LABEL = {"30m": "30 минут", "1h": "1 час", "4h": "4 часа"}
PATTERN_RU = {
    "hammer": "Молот",
    "shooting_star": "Падающая звезда",
    "morning_star": "Утренняя звезда",
    "evening_star": "Вечерняя звезда",
}
STATUS_RU = {
    "pending": "⏳ найден, ждёт подтверждения",
    "confirmed": "✅ подтверждён следующей свечой",
    "invalidated": "❌ сценарий отменён закрытием за экстремумом",
    "expired": "⚪ следующая свеча не подтвердила",
}
# В Telegram уходят только новые находки и подтверждённые. Статусы "не подтвердил" и
# "сценарий отменён" запоминаются в state, но сообщений не вызывают.
NOTIFY_STATUSES = {"pending", "confirmed"}
STATE_FILE = Path(os.getenv("STATE_FILE", "scan_state.json"))
RATE_LIMIT_WAIT = 60


def env(name: str, default: str = "") -> str:
    return (os.getenv(name) or "").strip() or default


def log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S} UTC] {message}", flush=True)


def pick_timeframes() -> list[str]:
    raw = env("TIMEFRAMES", "30m,1h,4h")
    timeframes = [item.strip().lower() for item in raw.split(",") if item.strip()]
    unknown = [item for item in timeframes if item not in TIMEFRAMES]
    if unknown or not timeframes:
        raise SystemExit(f"Неизвестные таймфреймы: {unknown or timeframes}; доступны {list(TIMEFRAMES)}")
    return timeframes


def closed_only(rows, timeframe: str, now_ms: int) -> list[Candle]:
    duration_ms = TIMEFRAMES[timeframe] * 1000
    return [Candle(*row) for row in sorted(rows) if row[0] + duration_ms <= now_ms]


class ExchangeFetcher:
    """Enforce a minimum delay between every public API request."""

    def __init__(self, delay_seconds: float):
        self.delay_seconds = max(0.0, delay_seconds)
        self.last_request = 0.0

    def _pause(self) -> None:
        if self.last_request:
            remaining = self.delay_seconds - (time.monotonic() - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request = time.monotonic()

    def symbols(self, quote_currency: str, limit: int, min_turnover: float) -> list[str]:
        return self._request(exchanges.get_symbols, quote_currency, limit, min_turnover)

    def candles(self, symbol: str, timeframe: str, limit: int = 200):
        return self._request(exchanges.get_klines, symbol, timeframe, limit)

    def _request(self, function, *args):
        for attempt in range(2):
            self._pause()
            try:
                return function(*args)
            except exchanges.RateLimited:
                if attempt:
                    raise
                log(f"Биржа ответила 429; жду {RATE_LIMIT_WAIT} секунд и повторяю запрос")
                time.sleep(RATE_LIMIT_WAIT)
        raise RuntimeError("Exchange request retry exhausted")


def signal_key(symbol: str, timeframe: str, signal: Signal, candles: list[Candle]) -> str:
    return f"{symbol}:{timeframe}:{signal.pattern}:{candles[signal.index].t}"


def read_state() -> dict[str, str]:
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return payload.get("signals", {}) if isinstance(payload, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def status_of(signal: Signal) -> str:
    if signal.confirmed:
        return "confirmed"
    if signal.invalidated:
        return "invalidated"
    if signal.expired:
        return "expired"
    return "pending"


def signal_text(symbol: str, timeframe: str, signal: Signal, current_price: float,
                signal_candle: Candle, status: str) -> str:
    label = PATTERN_RU.get(signal.pattern, signal.pattern)
    bits = []
    if signal.rsi is not None:
        bits.append(f"RSI {signal.rsi:.0f}")
    if signal.vol_ratio is not None:
        bits.append(f"объём ×{signal.vol_ratio:.1f} к среднему")
    if signal.context.get("near_extreme"):
        bits.append("рядом с экстремумом последних свечей")
    direction = "возможный разворот вверх" if signal.direction == "bull" else "возможный разворот вниз"
    tv_symbol = quote(f"OKX:{symbol}.P", safe=":.")
    chart_url = f"https://www.tradingview.com/chart/?symbol={tv_symbol}&interval={TF_TV[timeframe]}"
    formed_at = datetime.fromtimestamp(signal_candle.t / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    details = " · ".join(bits) if bits else "контекстных фильтров мало"
    return (
        f"• <b>{html.escape(symbol)}</b> — <b>{label}</b>\n"
        f"  Статус: {STATUS_RU[status]}\n"
        f"  Таймфрейм: {TF_LABEL[timeframe]} · свеча: {formed_at}\n"
        f"  Направление паттерна: {direction}\n"
        f"  Закрытие свечи формации: {signal_candle.c:.8g} · текущая цена: {current_price:.8g}\n"
        f"  Контекст: {details} · фильтры {signal.score}/3\n"
        f"  <a href=\"{chart_url}\">Открыть график OKX Perpetual</a>"
    )


def build_message(timeframe: str, rows: list[tuple[str, Signal, float, Candle, str]],
                  scanned: int, failures: list[str], now: datetime) -> str:
    header = (
        f"📊 <b>OKX USDT perpetual · {TF_LABEL[timeframe]}</b> · "
        f"{now:%Y-%m-%d %H:%M} UTC\nПроверено контрактов: {scanned}"
    )
    if rows:
        ordered = sorted(rows, key=lambda row: (row[4] != "confirmed", -row[1].score))
        parts = [header, ""]
        parts.extend(signal_text(symbol, timeframe, signal, price, candle, status)
                     for symbol, signal, price, candle, status in ordered)
    else:
        parts = [header, "Подходящих паттернов не найдено."]
    if failures and len(failures) > 0.3 * (scanned + len(failures)):
        parts.append(f"⚠️ Нет данных по: {html.escape(', '.join(failures))}")
    parts.append(
        "<i>Это наблюдение за формацией, не рекомендация на вход и не гарантия движения. "
        "Проверьте график и риск самостоятельно.</i>"
    )
    return "\n\n".join(parts)


def split_text(text: str, limit: int = 3800) -> list[str]:
    chunks, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def send_telegram(text: str) -> None:
    token, chat_id = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Задайте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID")
    for chunk in split_text(text):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                      "disable_web_page_preview": True}, timeout=30,
            )
            response.raise_for_status()
            if not response.json().get("ok"):
                raise RuntimeError("Telegram отклонил сообщение")
        except requests.RequestException as error:
            raise RuntimeError(f"Ошибка отправки в Telegram: {type(error).__name__}") from None
        time.sleep(1)


def run() -> int:
    delay = float(env("REQUEST_DELAY", "8"))
    limit = max(1, min(100, int(env("SYMBOL_LIMIT", "50"))))
    min_turnover = float(env("MIN_TURNOVER_USDT", "15000000"))
    quote_currency = env("QUOTE_CURRENCY", "USDT").upper()
    params = Params(min_context=int(env("MIN_CONTEXT", "2")))
    timeframes = pick_timeframes()
    fetcher = ExchangeFetcher(delay)
    state = read_state()

    symbols = fetcher.symbols(quote_currency, limit, min_turnover)
    if not symbols:
        raise RuntimeError("Биржа не вернула подходящих контрактов для сканирования")
    request_count = 1 + len(symbols) * len(timeframes)
    estimate_minutes = max(0, request_count - 1) * delay / 60
    log(f"Найдено контрактов: {len(symbols)}; таймфреймы: {timeframes}; "
        f"минимальный интервал запросов: {delay:g} с; проход ~{estimate_minutes:.1f} мин")

    sent_count = 0
    failures_all = []
    for timeframe in timeframes:
        events: list[tuple[str, Signal, float, Candle, str]] = []
        failures = []
        for symbol in symbols:
            try:
                rows = fetcher.candles(symbol, timeframe)
                candles = closed_only(rows, timeframe, int(time.time() * 1000))
                if len(candles) < 60:
                    failures.append(symbol)
                    log(f"{symbol} {timeframe}: недостаточно закрытых свечей")
                    continue
                for signal in scan(candles, params):
                    key = signal_key(symbol, timeframe, signal, candles)
                    status = status_of(signal)
                    previous = state.get(key)
                    should_send = status in NOTIFY_STATUSES and (
                        previous is None or (previous == "pending" and status == "confirmed"))
                    state[key] = status if previous is None or status != "pending" else previous
                    if should_send:
                        events.append((symbol, signal, candles[-1].c,
                                       candles[signal.index], status))
                        sent_count += 1
                        log(f"Найдено: {symbol} {timeframe} {signal.pattern} — {status}")
            except exchanges.Blocked as error:
                raise RuntimeError(str(error)) from None
            except exchanges.RateLimited as error:
                raise RuntimeError(f"Биржа ограничила частоту запросов: {error}") from None
            except exchanges.ExchangeError as error:
                failures.append(symbol)
                log(f"{symbol} {timeframe}: пропуск ({error})")

        failures_all.extend(failures)
        if events or env("SEND_EMPTY") == "1" or len(failures) > len(symbols) * 0.3:
            send_telegram(build_message(timeframe, events, len(symbols) - len(failures),
                                        failures, datetime.now(timezone.utc)))
        STATE_FILE.write_text(json.dumps({"signals": dict(list(state.items())[-5000:])},
                                         ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"Завершено. Новых уведомлений: {sent_count}; ошибок пар: {len(failures_all)}")
    return 0


def main() -> int:
    try:
        return run()
    except Exception as error:  # noqa: BLE001
        log(f"ОШИБКА: {type(error).__name__}: {error}")
        try:
            send_telegram(f"⚠️ <b>Сканер не завершил проверку</b>\n{html.escape(str(error)[:500])}")
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
