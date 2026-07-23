"""Indicadores escalares por-barra del laboratorio (Fase 10).

Primitivas puras y deterministas sobre series de precios/velas: SMA, EMA, RSI,
ATR, momentum, canales de Donchian, MACD, VWAP rodante y proxies de order flow
(delta y CVD sobre ``buy_volume``/``sell_volume``). Todas devuelven ``None``
cuando no hay datos suficientes, de modo que el compilador de estrategias no
emite señal hasta que el indicador está "caliente".

Son deliberadamente ligeras (escalar por barra) para que el ``DecisionSource``
compilado corra rápido en el motor de backtest; la analítica rica de
``app.analytics`` se reserva para el Feature Store de producción.
"""

import math
from collections.abc import Sequence

from app.market.models import Candle


def sma(values: Sequence[float], period: int) -> float | None:
    """Simple moving average of the last ``period`` values."""
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def ema(values: Sequence[float], period: int) -> float | None:
    """Exponential moving average (seeded with the SMA of the first window)."""
    if period <= 0 or len(values) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    result = seed
    for value in values[period:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def rsi(closes: Sequence[float], period: int) -> float | None:
    """Wilder's Relative Strength Index over ``period`` closes."""
    if period <= 0 or len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        change = closes[i] - closes[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def true_ranges(candles: Sequence[Candle]) -> list[float]:
    """True range series for a candle sequence."""
    out: list[float] = []
    for i, candle in enumerate(candles):
        if i == 0:
            out.append(candle.high - candle.low)
            continue
        prev_close = candles[i - 1].close
        out.append(
            max(
                candle.high - candle.low,
                abs(candle.high - prev_close),
                abs(candle.low - prev_close),
            )
        )
    return out


def atr(candles: Sequence[Candle], period: int) -> float | None:
    """Average True Range over the last ``period`` candles."""
    if period <= 0 or len(candles) < period + 1:
        return None
    trs = true_ranges(candles)
    window = trs[-period:]
    return sum(window) / period


def momentum(closes: Sequence[float], period: int) -> float | None:
    """Fractional price change over ``period`` bars."""
    if period <= 0 or len(closes) < period + 1:
        return None
    base = closes[-period - 1]
    if base == 0.0:
        return None
    return (closes[-1] - base) / base


def donchian_high(candles: Sequence[Candle], period: int) -> float | None:
    """Highest high of the previous ``period`` candles (excluding the last)."""
    if period <= 0 or len(candles) < period + 1:
        return None
    window = candles[-period - 1 : -1]
    return max(c.high for c in window)


def donchian_low(candles: Sequence[Candle], period: int) -> float | None:
    """Lowest low of the previous ``period`` candles (excluding the last)."""
    if period <= 0 or len(candles) < period + 1:
        return None
    window = candles[-period - 1 : -1]
    return min(c.low for c in window)


def macd(closes: Sequence[float], fast: int, slow: int, signal: int) -> tuple[float, float] | None:
    """MACD line and signal line.

    Returns:
        ``(macd_line, signal_line)`` or ``None`` when there is not enough data.
    """
    if fast >= slow or len(closes) < slow + signal:
        return None
    macd_series: list[float] = []
    for end in range(slow, len(closes) + 1):
        window = closes[:end]
        fast_ema = ema(window, fast)
        slow_ema = ema(window, slow)
        if fast_ema is None or slow_ema is None:
            continue
        macd_series.append(fast_ema - slow_ema)
    if len(macd_series) < signal:
        return None
    signal_line = ema(macd_series, signal)
    if signal_line is None:
        return None
    return macd_series[-1], signal_line


def rolling_vwap(candles: Sequence[Candle], period: int) -> float | None:
    """Volume-weighted average price over the last ``period`` candles."""
    if period <= 0 or len(candles) < period:
        return None
    window = candles[-period:]
    total_volume = sum(c.volume for c in window)
    if total_volume <= 0.0:
        return sum((c.high + c.low + c.close) / 3.0 for c in window) / period
    return sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in window) / total_volume


def delta(candle: Candle) -> float:
    """Order-flow delta (aggressive buys minus sells) for a candle."""
    return candle.buy_volume - candle.sell_volume


def cvd(candles: Sequence[Candle], period: int) -> float | None:
    """Cumulative volume delta over the last ``period`` candles."""
    if period <= 0 or len(candles) < period:
        return None
    return sum(delta(c) for c in candles[-period:])


def book_pressure(candle: Candle) -> float:
    """Normalized order-flow imbalance in ``[-1, 1]`` (0 without flow data)."""
    if candle.volume <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, delta(candle) / candle.volume))


def slope(values: Sequence[float], period: int) -> float | None:
    """Average per-bar change of the last ``period`` values (a discrete slope)."""
    if period <= 1 or len(values) < period:
        return None
    window = values[-period:]
    return (window[-1] - window[0]) / (period - 1)


def stdev_window(values: Sequence[float], period: int) -> float | None:
    """Sample standard deviation of the last ``period`` values."""
    if period <= 1 or len(values) < period:
        return None
    window = values[-period:]
    mu = sum(window) / period
    var = sum((v - mu) ** 2 for v in window) / (period - 1)
    return math.sqrt(var)
