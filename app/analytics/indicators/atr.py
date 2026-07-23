"""ATR avanzado: clásico, adaptativo (EMA), pendiente y expansión/compresión."""

import itertools
from collections.abc import Sequence

from app.market.models import Candle


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    """True Range por vela (necesita ≥ 2 velas; la primera no tiene TR).

    Args:
        candles: Velas ordenadas de más vieja a más nueva.

    Returns:
        Lista de TR alineada con ``candles[1:]``.
    """
    ranges: list[float] = []
    for prev, curr in itertools.pairwise(candles):
        ranges.append(
            max(curr.high - curr.low, abs(curr.high - prev.close), abs(curr.low - prev.close))
        )
    return ranges


def atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    """ATR clásico (media simple de los últimos ``period`` TR)."""
    ranges = true_ranges(candles)
    if not ranges:
        return None
    window = ranges[-period:]
    return sum(window) / len(window)


def atr_series(candles: Sequence[Candle], period: int = 14) -> list[float]:
    """Serie de ATR (rolling simple); vacía si no hay TR suficientes."""
    ranges = true_ranges(candles)
    if len(ranges) < period:
        return []
    series: list[float] = []
    acc = sum(ranges[:period])
    series.append(acc / period)
    for i in range(period, len(ranges)):
        acc += ranges[i] - ranges[i - period]
        series.append(acc / period)
    return series


def adaptive_atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    """ATR adaptativo: suavizado exponencial (estilo Wilder) del TR."""
    ranges = true_ranges(candles)
    if len(ranges) < period:
        return None
    alpha = 2.0 / (period + 1)
    value = sum(ranges[:period]) / period
    for tr in ranges[period:]:
        value = alpha * tr + (1 - alpha) * value
    return value


def atr_slope(candles: Sequence[Candle], period: int = 14, lookback: int = 5) -> float | None:
    """Pendiente del ATR como cambio porcentual por barra en ``lookback`` barras.

    Returns:
        ``(atr_now / atr_prev - 1) * 100 / lookback`` o ``None`` sin datos.
    """
    series = atr_series(candles, period)
    if len(series) <= lookback:
        return None
    prev = series[-1 - lookback]
    if prev <= 0:
        return None
    return (series[-1] / prev - 1.0) * 100.0 / lookback


def expansion_ratio(
    candles: Sequence[Candle], short_period: int = 5, long_period: int = 20
) -> float | None:
    """Ratio ATR corto / ATR largo (>1 expansión, <1 compresión)."""
    short = atr(candles, short_period)
    long = atr(candles, long_period)
    if short is None or long is None or long <= 0:
        return None
    return short / long
