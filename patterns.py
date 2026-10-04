"""Распознавание свечных паттернов и фильтры контекста. Без внешних библиотек.

Паттерны: молот, падающая звезда, утренняя звезда, вечерняя звезда.
Сам по себе паттерн ничего не гарантирует, поэтому каждый кандидат проверяется:
  * обязательно: перед паттерном было движение в нужную сторону (тренд, который можно "разворачивать");
  * очки контекста (0..3): у экстремума последних свечей / всплеск объёма / RSI в зоне перепроданности или перекупленности.
Отправляются только кандидаты с достаточным числом очков (Params.min_context).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple, Optional


class Candle(NamedTuple):
    t: int  # время открытия, мс
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass
class Params:
    atr_period: int = 14
    min_range_atr: float = 0.5      # свеча-паттерн не должна быть крошечной (диапазон >= 0.5 ATR)
    trend_candles: int = 6          # за сколько свечей смотрим предшествующее движение
    trend_atr_mult: float = 2.0     # движение должно быть >= 2 ATR
    extreme_window: int = 50        # окно для "у экстремума"
    extreme_tol_atr: float = 0.5    # допуск до экстремума, в ATR
    volume_window: int = 20
    volume_mult: float = 1.5        # объём >= 1.5 x среднего
    rsi_period: int = 14
    rsi_bull: float = 35.0          # для бычьих паттернов RSI <= 35
    rsi_bear: float = 65.0          # для медвежьих RSI >= 65
    min_context: int = 2            # минимум очков контекста из 3


@dataclass
class Signal:
    pattern: str            # hammer / shooting_star / morning_star / evening_star
    direction: str          # bull (возможный рост) / bear (возможное падение)
    index: int              # индекс последней свечи паттерна
    confirmed: bool
    context: dict = field(default_factory=dict)
    score: int = 0
    rsi: Optional[float] = None
    vol_ratio: Optional[float] = None
    invalidated: bool = False
    expired: bool = False
    invalidation: Optional[float] = None   # уровень отмены: минимум формации (рост) / максимум (падение)


# ---------- базовые величины свечи ----------
def body(c: Candle) -> float:
    return abs(c.c - c.o)


def rng(c: Candle) -> float:
    return c.h - c.l


def upper(c: Candle) -> float:
    return c.h - max(c.o, c.c)


def lower(c: Candle) -> float:
    return min(c.o, c.c) - c.l


def is_bull(c: Candle) -> bool:
    return c.c > c.o


def is_bear(c: Candle) -> bool:
    return c.c < c.o


# ---------- индикаторы ----------
def atr(candles: list[Candle], i: int, period: int = 14) -> float:
    start = max(1, i - period + 1)
    trs = []
    for k in range(start, i + 1):
        pc = candles[k - 1].c
        trs.append(max(candles[k].h - candles[k].l, abs(candles[k].h - pc), abs(candles[k].l - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def rsi(candles: list[Candle], i: int, period: int = 14) -> Optional[float]:
    if i < period:
        return None
    gains = losses = 0.0
    for k in range(1, period + 1):
        d = candles[k].c - candles[k - 1].c
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period
    for k in range(period + 1, i + 1):
        d = candles[k].c - candles[k - 1].c
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_g / avg_l)


# ---------- форма паттернов ----------
def is_hammer(c: Candle, a: float, p: Params) -> bool:
    r, b = rng(c), body(c)
    if r <= 0 or r < p.min_range_atr * a:
        return False
    return b <= 0.35 * r and lower(c) >= 2 * b and lower(c) >= 0.6 * r and upper(c) <= 0.15 * r


def is_shooting_star(c: Candle, a: float, p: Params) -> bool:
    r, b = rng(c), body(c)
    if r <= 0 or r < p.min_range_atr * a:
        return False
    return b <= 0.35 * r and upper(c) >= 2 * b and upper(c) >= 0.6 * r and lower(c) <= 0.15 * r


def is_morning_star(c1: Candle, c2: Candle, c3: Candle, a: float) -> bool:
    b1, b2, b3 = body(c1), body(c2), body(c3)
    if rng(c1) <= 0 or rng(c1) < 0.8 * a:
        return False
    return (
        is_bear(c1) and b1 >= 0.6 * rng(c1)                 # большая красная
        and b2 <= 0.35 * b1                                  # маленькое тело-"звезда"
        and max(c2.o, c2.c) <= c1.c + 0.35 * b1              # звезда у низа первой свечи (в крипте гэпов почти нет)
        and is_bull(c3) and c3.c > (c1.o + c1.c) / 2         # зелёная закрылась выше середины первой
        and b3 >= 0.5 * b1
    )


def is_evening_star(c1: Candle, c2: Candle, c3: Candle, a: float) -> bool:
    b1, b2, b3 = body(c1), body(c2), body(c3)
    if rng(c1) <= 0 or rng(c1) < 0.8 * a:
        return False
    return (
        is_bull(c1) and b1 >= 0.6 * rng(c1)
        and b2 <= 0.35 * b1
        and min(c2.o, c2.c) >= c1.c - 0.35 * b1
        and is_bear(c3) and c3.c < (c1.o + c1.c) / 2
        and b3 >= 0.5 * b1
    )


def _shapes_at(candles: list[Candle], e: int, p: Params):
    """Паттерны, у которых последняя свеча имеет индекс e. -> [(имя, направление, индекс первой свечи)]"""
    a = atr(candles, e, p.atr_period)
    if a <= 0:
        return []
    out = []
    c = candles[e]
    if is_hammer(c, a, p):
        out.append(("hammer", "bull", e))
    if is_shooting_star(c, a, p):
        out.append(("shooting_star", "bear", e))
    if e >= 2:
        c1, c2, c3 = candles[e - 2], candles[e - 1], candles[e]
        if is_morning_star(c1, c2, c3, a):
            out.append(("morning_star", "bull", e - 2))
        if is_evening_star(c1, c2, c3, a):
            out.append(("evening_star", "bear", e - 2))
    return out


def _evaluate(candles, name, direction, s, e, p: Params) -> Optional[Signal]:
    a = atr(candles, e, p.atr_period)
    bull = direction == "bull"
    single = s == e
    # 1) обязательное условие: было что разворачивать
    trend_end = s - 1 if single else s     # у звёзд первая (большая) свеча уже часть движения
    start = trend_end - p.trend_candles
    if start < 0:
        return None
    move = candles[trend_end].c - candles[start].c
    if (-move if bull else move) < p.trend_atr_mult * a:
        return None
    # 2) очки контекста
    window = candles[max(0, e - p.extreme_window + 1): e + 1]
    if bull:
        near_extreme = min(x.l for x in candles[s:e + 1]) <= min(x.l for x in window) + p.extreme_tol_atr * a
    else:
        near_extreme = max(x.h for x in candles[s:e + 1]) >= max(x.h for x in window) - p.extreme_tol_atr * a
    prev = candles[max(0, e - p.volume_window): e]
    avg_v = sum(x.v for x in prev) / len(prev) if prev else 0.0
    vol_ratio = candles[e].v / avg_v if avg_v > 0 else None
    vol_ok = vol_ratio is not None and vol_ratio >= p.volume_mult
    rsis = [r for r in (rsi(candles, k, p.rsi_period) for k in range(s, e + 1)) if r is not None]
    rsi_val = None
    rsi_ok = False
    if rsis:
        rsi_val = min(rsis) if bull else max(rsis)
        rsi_ok = rsi_val <= p.rsi_bull if bull else rsi_val >= p.rsi_bear
    ctx = {"near_extreme": near_extreme, "volume": vol_ok, "rsi": rsi_ok}
    formation = candles[s:e + 1]
    level = min(x.l for x in formation) if bull else max(x.h for x in formation)
    return Signal(name, direction, e, False, ctx, sum(ctx.values()), rsi_val, vol_ratio,
                  invalidation=level)


def _is_confirmed(candles, sig: Signal, nxt: Candle) -> bool:
    last = candles[sig.index]
    single = sig.pattern in ("hammer", "shooting_star")
    if sig.direction == "bull":
        ref = last.h if single else last.c
        return nxt.c > ref and (single or is_bull(nxt))
    ref = last.l if single else last.c
    return nxt.c < ref and (single or is_bear(nxt))


def _is_invalidated(candles, sig: Signal, nxt: Candle) -> bool:
    """A close beyond the formation's outer extreme invalidates the setup."""
    if sig.pattern in ("morning_star", "evening_star"):
        formation = candles[max(0, sig.index - 2):sig.index + 1]
    else:
        formation = [candles[sig.index]]
    if sig.direction == "bull":
        return nxt.c < min(c.l for c in formation)
    return nxt.c > max(c.h for c in formation)


def scan(candles: list[Candle], p: Optional[Params] = None) -> list[Signal]:
    """candles: ТОЛЬКО закрытые свечи, от старых к новым.

    Scans recent bars so hourly polling can catch both 30-minute candles that
    closed since the previous run. Existing setups are classified by the next
    closed candle as confirmed, invalidated, or not confirmed.
    """
    p = p or Params()
    n = len(candles)
    if n < 60:
        return []
    found = []
    for e in range(max(59, n - 3), n):
        for name, direction, s in _shapes_at(candles, e, p):
            sig = _evaluate(candles, name, direction, s, e, p)
            if sig is None or sig.score < p.min_context:
                continue
            if e + 1 < n:
                following = candles[e + 1]
                sig.confirmed = _is_confirmed(candles, sig, following)
                sig.invalidated = not sig.confirmed and _is_invalidated(candles, sig, following)
                sig.expired = not sig.confirmed and not sig.invalidated
            found.append(sig)
    return found
