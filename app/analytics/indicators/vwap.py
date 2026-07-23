"""VWAP: sesión (día/semana/mes), anclado, bandas σ, pendiente y distancia."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.market.models import Candle, Timeframe

_SESSION_TIMEFRAMES: dict[str, Timeframe] = {
    "day": Timeframe.D1,
    "week": Timeframe.W1,
    "month": Timeframe.MN1,
}


def _typical_price(candle: Candle) -> float:
    """Precio representativo de la vela (su VWAP propio si existe)."""
    if candle.vwap > 0:
        return candle.vwap
    return (candle.high + candle.low + candle.close) / 3.0


def session_anchor(kind: str, moment: datetime) -> datetime:
    """UTC start of the current day/week/month session.

    Args:
        kind: ``"day"``, ``"week"`` o ``"month"``.
        moment: Instante de referencia (aware UTC).

    Returns:
        Inicio del bucket de sesión.

    Raises:
        ValueError: Si ``kind`` no es válido.
    """
    timeframe = _SESSION_TIMEFRAMES.get(kind)
    if timeframe is None:
        raise ValueError(f"Unknown session kind '{kind}'")
    return timeframe.bucket_start(moment)


def anchor_index_at(candles: Sequence[Candle], anchor: datetime) -> int | None:
    """First candle index whose bucket starts at/after ``anchor`` (None si ninguna)."""
    for i, candle in enumerate(candles):
        if candle.start >= anchor:
            return i
    return None


def anchored_vwap(candles: Sequence[Candle], anchor_index: int) -> float | None:
    """VWAP acumulado desde ``anchor_index`` hasta la última vela."""
    if not 0 <= anchor_index < len(candles):
        return None
    notional = 0.0
    volume = 0.0
    for candle in candles[anchor_index:]:
        notional += _typical_price(candle) * candle.volume
        volume += candle.volume
    return notional / volume if volume > 0 else None


@dataclass(frozen=True, kw_only=True, slots=True)
class VWAPBands:
    """VWAP anclado con bandas de desviación estándar ponderada por volumen.

    Attributes:
        value: VWAP actual.
        std: Desviación estándar (ponderada) del precio típico vs VWAP.
        upper_1 / lower_1: Bandas a ±1 multiplicador.
        upper_2 / lower_2: Bandas a ±2 multiplicador.
        slope_pct_per_bar: Pendiente del VWAP (% por barra) en el lookback.
        distance_pct: Distancia porcentual del precio de referencia al VWAP.
        bars: Velas incluidas desde el ancla.
    """

    value: float
    std: float
    upper_1: float
    lower_1: float
    upper_2: float
    lower_2: float
    slope_pct_per_bar: float
    distance_pct: float
    bars: int

    def deviation(self, price: float) -> float:
        """Distancia del precio en desviaciones estándar (0 si std=0)."""
        return (price - self.value) / self.std if self.std > 0 else 0.0


def vwap_bands(
    candles: Sequence[Candle],
    anchor_index: int,
    *,
    band_multipliers: tuple[float, float] = (1.0, 2.0),
    slope_lookback: int = 10,
    reference_price: float | None = None,
) -> VWAPBands | None:
    """Compute the anchored VWAP with σ-bands, slope and distance.

    Args:
        candles: Velas ordenadas.
        anchor_index: Índice del ancla (inclusive).
        band_multipliers: Multiplicadores para las dos bandas.
        slope_lookback: Barras para la pendiente del VWAP.
        reference_price: Precio para ``distance_pct`` (default: último cierre).

    Returns:
        Bandas calculadas o ``None`` sin volumen/datos.
    """
    if not 0 <= anchor_index < len(candles):
        return None
    window = candles[anchor_index:]
    vwap_series: list[float] = []
    notional = 0.0
    volume = 0.0
    weighted_sq = 0.0
    for candle in window:
        typical = _typical_price(candle)
        notional += typical * candle.volume
        volume += candle.volume
        weighted_sq += candle.volume * typical * typical
        if volume > 0:
            vwap_series.append(notional / volume)
    if volume <= 0 or not vwap_series:
        return None
    value = vwap_series[-1]
    variance = max(0.0, weighted_sq / volume - value * value)
    std = variance**0.5
    m1, m2 = band_multipliers
    lookback = min(slope_lookback, len(vwap_series) - 1)
    if lookback > 0 and vwap_series[-1 - lookback] > 0:
        slope = (vwap_series[-1] / vwap_series[-1 - lookback] - 1.0) * 100.0 / lookback
    else:
        slope = 0.0
    price = reference_price if reference_price is not None else window[-1].close
    distance = (price - value) / value * 100.0 if value > 0 else 0.0
    return VWAPBands(
        value=value,
        std=std,
        upper_1=value + m1 * std,
        lower_1=value - m1 * std,
        upper_2=value + m2 * std,
        lower_2=value - m2 * std,
        slope_pct_per_bar=slope,
        distance_pct=distance,
        bars=len(window),
    )
