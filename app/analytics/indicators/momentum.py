"""Momentum: ROC, score normalizado, aceleración e impulsos."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.analytics.indicators.atr import atr
from app.market.models import Candle


def rate_of_change(candles: Sequence[Candle], period: int = 10) -> float | None:
    """ROC porcentual del cierre en ``period`` barras."""
    if len(candles) <= period:
        return None
    past = candles[-1 - period].close
    if past <= 0:
        return None
    return (candles[-1].close / past - 1.0) * 100.0


def momentum_score(
    candles: Sequence[Candle], period: int = 10, atr_period: int = 14
) -> float | None:
    """Momentum normalizado por volatilidad, acotado a [-100, 100].

    ``ROC absoluto / (ATR% × period)`` — cuántas "unidades de ruido" se movió
    el precio; el signo indica dirección.
    """
    roc = rate_of_change(candles, period)
    value = atr(candles, atr_period)
    if roc is None or value is None:
        return None
    price = candles[-1].close
    if price <= 0 or value <= 0:
        return None
    atr_pct = value / price * 100.0
    if atr_pct <= 0:
        return None
    raw = roc / (atr_pct * period) * 100.0
    return max(-100.0, min(100.0, raw))


def acceleration(candles: Sequence[Candle], period: int = 10) -> float | None:
    """Aceleración: ROC actual menos ROC del periodo anterior (Δ momentum)."""
    if len(candles) <= 2 * period:
        return None
    current = rate_of_change(candles, period)
    previous = rate_of_change(candles[:-period], period)
    if current is None or previous is None:
        return None
    return current - previous


@dataclass(frozen=True, kw_only=True, slots=True)
class Impulse:
    """Movimiento impulsivo detectado (vela(s) de desplazamiento).

    Attributes:
        direction: ``"up"`` o ``"down"``.
        start_index: Índice de la primera vela del impulso.
        end_index: Índice de la última vela del impulso.
        magnitude: Recorrido del impulso en múltiplos de ATR.
        body_ratio: Cuerpo/rango promedio de las velas del impulso (0-1).
    """

    direction: str
    start_index: int
    end_index: int
    magnitude: float
    body_ratio: float


def detect_impulse(
    candles: Sequence[Candle],
    *,
    max_bars: int = 3,
    min_atr_multiple: float = 1.5,
    min_body_ratio: float = 0.6,
    atr_period: int = 14,
) -> Impulse | None:
    """Detect a displacement move ending at the latest candle.

    Un impulso = 1..max_bars velas consecutivas en la misma dirección cuyo
    recorrido neto supera ``min_atr_multiple`` ATR con cuerpos dominantes.

    Args:
        candles: Velas ordenadas (mínimo ``atr_period + max_bars + 1``).
        max_bars: Máximo de velas que componen el impulso.
        min_atr_multiple: Recorrido mínimo en ATRs.
        min_body_ratio: Cuerpo/rango medio mínimo de las velas.
        atr_period: Periodo del ATR de referencia.

    Returns:
        El impulso más corto que cumpla, o ``None``.
    """
    value = atr(candles, atr_period)
    if value is None or value <= 0 or len(candles) < 2:
        return None
    last_index = len(candles) - 1
    for bars in range(1, max_bars + 1):
        if bars > len(candles):
            break
        window = candles[len(candles) - bars :]
        directions = {("up" if c.close >= c.open else "down") for c in window}
        if len(directions) != 1:
            continue
        direction = directions.pop()
        net = abs(window[-1].close - window[0].open)
        if net < min_atr_multiple * value:
            continue
        bodies: list[float] = []
        for c in window:
            full = c.high - c.low
            bodies.append(abs(c.close - c.open) / full if full > 0 else 0.0)
        body_ratio = sum(bodies) / len(bodies)
        if body_ratio < min_body_ratio:
            continue
        return Impulse(
            direction=direction,
            start_index=last_index - bars + 1,
            end_index=last_index,
            magnitude=net / value,
            body_ratio=body_ratio,
        )
    return None
