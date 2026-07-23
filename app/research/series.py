"""Series por-barra para el Feature Lab y el Factor Lab (Fase 10).

Cada función recibe una secuencia de velas y devuelve una lista del mismo largo:
el valor en el índice ``i`` se calcula usando **sólo** ``candles[: i + 1]`` (sin
mirar el futuro), y es ``nan`` durante el warmup. El coeficiente de información
las alinea después con el retorno futuro. Reutilizan las primitivas de
``app.research.indicators``.
"""

import math
from collections.abc import Sequence

from app.market.models import Candle
from app.research import indicators as ind

NAN = float("nan")


def _closes(candles: Sequence[Candle]) -> list[float]:
    return [c.close for c in candles]


# ---------------------------------------------------------------------------
# Feature Lab
# ---------------------------------------------------------------------------


def atr_slope(candles: Sequence[Candle], *, period: int = 14, window: int = 5) -> list[float]:
    """Slope of the ATR series (volatility trend)."""
    out: list[float] = []
    atr_hist: list[float] = []
    for i in range(len(candles)):
        atr = ind.atr(candles[: i + 1], period)
        atr_hist.append(atr if atr is not None else NAN)
        finite = [v for v in atr_hist if math.isfinite(v)]
        slope = ind.slope(finite, window) if len(finite) >= window else None
        out.append(slope if slope is not None else NAN)
    return out


def vwap_distance(candles: Sequence[Candle], *, period: int = 30) -> list[float]:
    """Relative distance of price from the rolling VWAP."""
    out: list[float] = []
    for i in range(len(candles)):
        window = candles[: i + 1]
        vwap = ind.rolling_vwap(window, period)
        close = window[-1].close
        out.append((close - vwap) / vwap if vwap and vwap > 0 else NAN)
    return out


def delta_momentum(candles: Sequence[Candle], *, period: int = 10) -> list[float]:
    """Normalized cumulative volume delta over a window."""
    out: list[float] = []
    for i in range(len(candles)):
        window = candles[: i + 1]
        value = ind.cvd(window, period)
        volume = sum(c.volume for c in window[-period:]) if len(window) >= period else 0.0
        out.append(value / volume if value is not None and volume > 0 else NAN)
    return out


def liquidity_score(candles: Sequence[Candle], *, period: int = 20) -> list[float]:
    """Current volume relative to its rolling average (minus one)."""
    out: list[float] = []
    for i in range(len(candles)):
        window = candles[: i + 1]
        avg = ind.sma([c.volume for c in window], period)
        out.append(window[-1].volume / avg - 1.0 if avg and avg > 0 else NAN)
    return out


def trend_score(candles: Sequence[Candle], *, period: int = 20) -> list[float]:
    """Momentum over a window (directional trend strength)."""
    closes = _closes(candles)
    out: list[float] = []
    for i in range(len(candles)):
        value = ind.momentum(closes[: i + 1], period)
        out.append(value if value is not None else NAN)
    return out


def book_pressure_score(candles: Sequence[Candle]) -> list[float]:
    """Order-flow imbalance per bar in ``[-1, 1]``."""
    return [ind.book_pressure(c) if c.volume > 0 else NAN for c in candles]


def microprice(candles: Sequence[Candle]) -> list[float]:
    """Microprice proxy: mid skewed toward the pressured side, relative to mid.

    Sin libro de órdenes se aproxima con el desbalance de flujo (``book_pressure``)
    aplicado sobre el rango de la vela — documentado como proxy, no como el
    microprice real bid/ask.
    """
    out: list[float] = []
    for c in candles:
        mid = (c.high + c.low) / 2.0
        if mid <= 0.0:
            out.append(NAN)
            continue
        skew = ind.book_pressure(c) * (c.high - c.low) / 2.0
        out.append(skew / mid)
    return out


def spread_velocity(candles: Sequence[Candle], *, window: int = 3) -> list[float]:
    """Rate of change of the bar range (a spread/volatility velocity proxy)."""
    ranges = [(c.high - c.low) / c.close if c.close > 0 else NAN for c in candles]
    out: list[float] = []
    for i in range(len(candles)):
        finite = [v for v in ranges[: i + 1] if math.isfinite(v)]
        slope = ind.slope(finite, window) if len(finite) >= window else None
        out.append(slope if slope is not None else NAN)
    return out


def volatility_expansion(
    candles: Sequence[Candle], *, short: int = 7, long: int = 28
) -> list[float]:
    """Ratio of short-window ATR to long-window ATR (expansion > 0)."""
    out: list[float] = []
    for i in range(len(candles)):
        window = candles[: i + 1]
        atr_s = ind.atr(window, short)
        atr_l = ind.atr(window, long)
        out.append(atr_s / atr_l - 1.0 if atr_s is not None and atr_l and atr_l > 0 else NAN)
    return out


# ---------------------------------------------------------------------------
# Factor Lab (algunos reutilizan las series de arriba)
# ---------------------------------------------------------------------------


def ema_slope(candles: Sequence[Candle], *, period: int = 20, window: int = 5) -> list[float]:
    """Slope of an EMA (trend factor)."""
    closes = _closes(candles)
    ema_hist: list[float] = []
    out: list[float] = []
    for i in range(len(candles)):
        value = ind.ema(closes[: i + 1], period)
        ema_hist.append(value if value is not None else NAN)
        finite = [v for v in ema_hist if math.isfinite(v)]
        slope = ind.slope(finite, window) if len(finite) >= window else None
        out.append(slope if slope is not None else NAN)
    return out


def reversion_z(candles: Sequence[Candle], *, period: int = 20) -> list[float]:
    """Negative z-score of price vs its SMA (mean-reversion factor)."""
    closes = _closes(candles)
    out: list[float] = []
    for i in range(len(candles)):
        window = closes[: i + 1]
        avg = ind.sma(window, period)
        sd = ind.stdev_window(window, period)
        if avg is None or sd is None or sd <= 0:
            out.append(NAN)
            continue
        out.append(-(window[-1] - avg) / sd)  # signo positivo = comprar barato
    return out


def rsi_bias(candles: Sequence[Candle], *, period: int = 14) -> list[float]:
    """Reversion bias from RSI centered at 50 (factor)."""
    closes = _closes(candles)
    out: list[float] = []
    for i in range(len(candles)):
        value = ind.rsi(closes[: i + 1], period)
        out.append((50.0 - value) / 50.0 if value is not None else NAN)
    return out


def volume_zscore(candles: Sequence[Candle], *, period: int = 20) -> list[float]:
    """Z-score of current volume vs its rolling window (liquidity factor)."""
    out: list[float] = []
    for i in range(len(candles)):
        vols = [c.volume for c in candles[: i + 1]]
        avg = ind.sma(vols, period)
        sd = ind.stdev_window(vols, period)
        if avg is None or sd is None or sd <= 0:
            out.append(NAN)
            continue
        out.append((vols[-1] - avg) / sd)
    return out


def atr_pct(candles: Sequence[Candle], *, period: int = 14) -> list[float]:
    """ATR as a fraction of price (volatility factor)."""
    out: list[float] = []
    for i in range(len(candles)):
        window = candles[: i + 1]
        atr = ind.atr(window, period)
        close = window[-1].close
        out.append(atr / close if atr is not None and close > 0 else NAN)
    return out


def hour_of_day(candles: Sequence[Candle]) -> list[float]:
    """Normalized hour of day in ``[-1, 1]`` (temporal factor)."""
    return [(c.start.hour / 23.0) * 2.0 - 1.0 for c in candles]


def cvd_factor(candles: Sequence[Candle], *, period: int = 20) -> list[float]:
    """Normalized cumulative volume delta (volume/order-flow factor)."""
    return delta_momentum(candles, period=period)


def buy_ratio(candles: Sequence[Candle]) -> list[float]:
    """Aggressive-buy share centered at zero (order-flow factor)."""
    out: list[float] = []
    for c in candles:
        out.append(c.buy_volume / c.volume - 0.5 if c.volume > 0 else NAN)
    return out


def trend_x_liquidity(candles: Sequence[Candle]) -> list[float]:
    """Hybrid: trend confirmed by liquidity (product of both series)."""
    trend = trend_score(candles)
    liq = liquidity_score(candles)
    return [
        t * q if math.isfinite(t) and math.isfinite(q) else NAN
        for t, q in zip(trend, liq, strict=True)
    ]


def vwapdist_x_pressure(candles: Sequence[Candle]) -> list[float]:
    """Hybrid: VWAP distance times order-flow pressure."""
    dist = vwap_distance(candles)
    pressure = book_pressure_score(candles)
    return [
        d * p if math.isfinite(d) and math.isfinite(p) else NAN
        for d, p in zip(dist, pressure, strict=True)
    ]
