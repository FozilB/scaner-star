"""Read-only public market-data access for OKX USDT linear perpetual swaps.

Интерфейс (get_symbols, get_klines, ExchangeError, Blocked, RateLimited) такой же,
как был у версии для Bybit, поэтому main.py почти не менялся.
"""
from __future__ import annotations

from typing import Any

import requests

BASE_URL = "https://www.okx.com"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "read-only-candle-pattern-scanner/1.0"})

# symbol вида "BTCUSDT" -> instId вида "BTC-USDT-SWAP" (заполняется в get_symbols)
_INST_IDS: dict[str, str] = {}

BAR = {"30m": "30m", "1h": "1H", "4h": "4H"}


class ExchangeError(Exception):
    """A market-data request failed."""


class Blocked(ExchangeError):
    """The exchange rejected or blocked this request/IP."""


class RateLimited(ExchangeError):
    """The exchange returned a rate-limit response."""


def _get(path: str, params: dict[str, Any]) -> list[Any]:
    try:
        response = SESSION.get(f"{BASE_URL}{path}", params=params, timeout=20)
    except requests.RequestException as error:
        raise ExchangeError(f"network error: {type(error).__name__}") from None
    if response.status_code in (403, 451):
        raise Blocked(f"HTTP {response.status_code}; остановил сканирование, чтобы не усугублять ограничение IP")
    if response.status_code == 429:
        raise RateLimited("HTTP 429")
    if response.status_code >= 400:
        raise ExchangeError(f"HTTP {response.status_code}: {response.text[:160]}")
    try:
        payload = response.json()
    except ValueError:
        raise ExchangeError("OKX returned a non-JSON response") from None
    code = str(payload.get("code"))
    if code in ("50011", "50061"):  # too many requests
        raise RateLimited(payload.get("msg", "OKX rate limit"))
    if code != "0":
        raise ExchangeError(f"OKX {code}: {payload.get('msg', 'unknown API error')}")
    return payload.get("data", [])


def get_symbols(quote: str = "USDT", limit: int = 50,
                min_turnover: float = 15_000_000) -> list[str]:
    """Most liquid OKX linear perpetuals by 24h turnover (in quote currency, approx.)."""
    suffix = f"-{quote}-SWAP"
    candidates = []
    for item in _get("/api/v5/market/tickers", {"instType": "SWAP"}):
        inst_id = item.get("instId", "")
        if not inst_id.endswith(suffix):
            continue
        try:
            # для деривативов volCcy24h = объём в монете, last = цена
            turnover = float(item["volCcy24h"]) * float(item["last"])
        except (KeyError, TypeError, ValueError):
            continue
        if turnover >= min_turnover:
            symbol = inst_id[: -len("-SWAP")].replace("-", "")
            candidates.append((turnover, symbol, inst_id))
    candidates.sort(reverse=True)
    chosen = candidates[:limit]
    for _, symbol, inst_id in chosen:
        _INST_IDS[symbol] = inst_id
    return [symbol for _, symbol, _ in chosen]


def get_klines(symbol: str, interval: str, limit: int = 200):
    """Return OHLCV rows as (start_ms, open, high, low, close, volume), oldest first."""
    inst_id = _INST_IDS.get(symbol)
    if inst_id is None:
        if not symbol.endswith("USDT"):
            raise ExchangeError(f"unknown symbol {symbol}")
        inst_id = f"{symbol[:-4]}-USDT-SWAP"
    data = _get("/api/v5/market/candles",
                {"instId": inst_id, "bar": BAR[interval], "limit": min(limit, 300)})
    rows = []
    for row in data:
        # [ts, open, high, low, close, vol(контракты), volCcy, volCcyQuote, confirm]
        rows.append((int(row[0]), float(row[1]), float(row[2]), float(row[3]),
                     float(row[4]), float(row[5])))
    return sorted(rows)  # OKX отдаёт от новых к старым; логика ждёт от старых к новым
