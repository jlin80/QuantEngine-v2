"""Funciones puras sobre series y velas (deterministas, sin estado)."""

from collections.abc import Sequence

from app.market.models import Candle


def clamp01(value: float) -> float:
    """Clamp a value into [0, 1]."""
    return max(0.0, min(1.0, value))


def mean(values: Sequence[float]) -> float | None:
    """Media aritmética (None con secuencia vacía)."""
    return sum(values) / len(values) if values else None


def stdev(values: Sequence[float]) -> float | None:
    """Desviación estándar poblacional (None con < 2 valores)."""
    if len(values) < 2:
        return None
    avg = sum(values) / len(values)
    variance = sum((v - avg) ** 2 for v in values) / len(values)
    return float(variance**0.5)


def zscore(values: Sequence[float]) -> float | None:
    """Z-score del último valor respecto a la serie (None sin dispersión)."""
    if len(values) < 2:
        return None
    sigma = stdev(values)
    avg = mean(values)
    if sigma is None or avg is None or sigma <= 0:
        return None
    return (values[-1] - avg) / sigma


def is_bullish(candle: Candle) -> bool:
    """Vela de cuerpo alcista."""
    return candle.close > candle.open


def is_bearish(candle: Candle) -> bool:
    """Vela de cuerpo bajista."""
    return candle.close < candle.open


def body_ratio(candle: Candle) -> float:
    """Cuerpo / rango total de la vela (0 si rango nulo)."""
    span = candle.high - candle.low
    return abs(candle.close - candle.open) / span if span > 0 else 0.0


def upper_wick_ratio(candle: Candle) -> float:
    """Mecha superior / rango total (0 si rango nulo)."""
    span = candle.high - candle.low
    if span <= 0:
        return 0.0
    return (candle.high - max(candle.open, candle.close)) / span


def lower_wick_ratio(candle: Candle) -> float:
    """Mecha inferior / rango total (0 si rango nulo)."""
    span = candle.high - candle.low
    if span <= 0:
        return 0.0
    return (min(candle.open, candle.close) - candle.low) / span
