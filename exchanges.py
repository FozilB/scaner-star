"""Read-only public market-data access for Bybit linear USDT perpetuals."""
from __future__ import annotations

import time
from typing import Any

import requests


BASE_URL = "https://api.bybit.com"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "read-only-candle-pattern-scanner/1.0"})


class ExchangeError(Exception):
    """A market-data request failed."""


class Blocked(ExchangeError):
    """Bybit rejected or blocked this request/IP."""


class RateLimited(ExchangeError):
    """Bybit returned a rate-limit response."""


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = SESSION.get(f"{BASE_URL}{path}", params=params, timeout=20)
    except requests.RequestException as error:
        raise ExchangeError(f"network error: {type(error).__name__}") from None
    if response.status_code == 403:
        raise Blocked("HTTP 403; остановил сканирование, чтобы не усугублять ограничение IP")
    if response.status_code == 429:
        raise RateLimited("HTTP 429")
    if response.status_code >= 400:
        raise ExchangeError(f"HTTP {response.status_code}: {response.text[:160]}")
    try:
        payload = response.json()
    except ValueError:
        raise ExchangeError("Bybit returned a non-JSON response") from None
    code = payload.get("retCode")
    if code == 10006:
        raise RateLimited(payload.get("retMsg", "Bybit rate limit"))
    if code != 0:
        raise ExchangeError(f"Bybit {code}: {payload.get('retMsg', 'unknown API error')}")
    return payload.get("result", {})


def get_symbols(quote: str = "USDT", limit: int = 50,
                min_turnover: float = 15_000_000) -> list[str]:
    """Return the most liquid Bybit linear pairs by 24h quote turnover."""
    result = _get("/v5/market/tickers", {"category": "linear"})
    candidates = []
    for item in result.get("list", []):
        symbol = item.get("symbol", "")
        if not symbol.endswith(quote):
            continue
        try:
            turnover = float(item.get("turnover24h", 0))
        except (TypeError, ValueError):
            continue
        if turnover >= min_turnover:
            candidates.append((turnover, symbol))
    return [symbol for turnover, symbol in sorted(candidates, reverse=True)[:limit]]


def get_klines(symbol: str, interval: str, limit: int = 200):
    """Return Bybit OHLCV rows as (start_ms, open, high, low, close, volume)."""
    bybit_interval = {"30m": "30", "1h": "60", "4h": "240"}[interval]
    result = _get("/v5/market/kline", {
        "category": "linear", "symbol": symbol,
        "interval": bybit_interval, "limit": limit,
    })
    rows = []
    for row in result.get("list", []):
        rows.append((int(row[0]), float(row[1]), float(row[2]), float(row[3]),
                     float(row[4]), float(row[5])))
    # Bybit returns reverse chronological order; pattern logic expects oldest first.
    return sorted(rows)
