from __future__ import annotations


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def average(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def pct_change(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    base = values[-period - 1]
    if base == 0:
        return None
    return values[-1] / base - 1.0


def recent_high(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return max(values[-period:])


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def true_range(
    high: float, low: float, prev_close: float | None
) -> float:
    """Single-bar true range. First bar uses high-low only."""
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float | None:
    """Average True Range over `period` bars. Returns None if insufficient data."""
    if len(highs) < period + 1:
        return None
    tr_values = [
        true_range(highs[i], lows[i], closes[i - 1] if i > 0 else None)
        for i in range(len(highs))
    ]
    return sum(tr_values[-period:]) / period
