"""Volume Profile: POC, VAH/VAL, HVN/LVN sobre velas (sesión o compuesto).

El volumen de cada vela se distribuye uniformemente en los bins que cruza su
rango — determinista y suficiente sin datos tick-a-tick por nivel.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.market.models import Candle


@dataclass(frozen=True, kw_only=True, slots=True)
class ProfileBin:
    """Un nivel del perfil: banda de precio y volumen acumulado."""

    low: float
    high: float
    volume: float

    @property
    def mid(self) -> float:
        """Precio central del bin."""
        return (self.low + self.high) / 2.0


@dataclass(frozen=True, kw_only=True, slots=True)
class VolumeProfile:
    """Perfil de volumen completo.

    Attributes:
        bins: Bins ordenados de menor a mayor precio.
        poc: Point of Control (precio central del bin con más volumen).
        vah: Value Area High.
        val: Value Area Low.
        hvn: Precios centrales de nodos de alto volumen (máximos locales).
        lvn: Precios centrales de nodos de bajo volumen (mínimos locales).
        total_volume: Volumen total del perfil.
        value_area_pct: Porcentaje de volumen contenido en el value area.
    """

    bins: tuple[ProfileBin, ...]
    poc: float
    vah: float
    val: float
    hvn: tuple[float, ...]
    lvn: tuple[float, ...]
    total_volume: float
    value_area_pct: float

    def position(self, price: float) -> str:
        """Dónde cae un precio respecto al value area."""
        if price > self.vah:
            return "above_value"
        if price < self.val:
            return "below_value"
        return "inside_value"


def volume_profile(
    candles: Sequence[Candle],
    *,
    bins: int = 24,
    value_area_pct: float = 0.70,
    node_ratio: float = 1.5,
) -> VolumeProfile | None:
    """Build the volume profile of a candle window.

    Args:
        candles: Velas de la ventana (sesión o compuesto).
        bins: Número de niveles de precio.
        value_area_pct: Fracción de volumen del value area (típico 70 %).
        node_ratio: Un bin es HVN si supera ``ratio × media_por_bin`` y LVN si
            queda bajo ``media_por_bin / ratio`` (solo extremos locales).

    Returns:
        Perfil calculado, o ``None`` sin velas/volumen/rango.
    """
    if not candles or bins <= 1:
        return None
    low = min(c.low for c in candles)
    high = max(c.high for c in candles)
    if high <= low:
        return None
    step = (high - low) / bins
    volumes = [0.0] * bins

    for candle in candles:
        if candle.volume <= 0:
            continue
        c_low, c_high = candle.low, candle.high
        span = c_high - c_low
        first = min(bins - 1, max(0, int((c_low - low) / step)))
        last = min(bins - 1, max(0, int((c_high - low) / step - 1e-12)))
        if span <= 0 or first == last:
            volumes[first if span <= 0 else last] += candle.volume
            continue
        for i in range(first, last + 1):
            bin_low = low + i * step
            bin_high = bin_low + step
            overlap = min(c_high, bin_high) - max(c_low, bin_low)
            if overlap > 0:
                volumes[i] += candle.volume * (overlap / span)

    total = sum(volumes)
    if total <= 0:
        return None

    profile_bins = tuple(
        ProfileBin(low=low + i * step, high=low + (i + 1) * step, volume=volumes[i])
        for i in range(bins)
    )
    poc_index = max(range(bins), key=lambda i: volumes[i])

    # Value area: expandir desde el POC hacia el lado con más volumen.
    included = {poc_index}
    area_volume = volumes[poc_index]
    lo, hi = poc_index, poc_index
    target = value_area_pct * total
    while area_volume < target and (lo > 0 or hi < bins - 1):
        below = volumes[lo - 1] if lo > 0 else -1.0
        above = volumes[hi + 1] if hi < bins - 1 else -1.0
        if above >= below:
            hi += 1
            included.add(hi)
            area_volume += volumes[hi]
        else:
            lo -= 1
            included.add(lo)
            area_volume += volumes[lo]

    # Línea base = media por bin (la mediana colapsa a 0 con bins vacíos).
    baseline = total / bins
    hvn: list[float] = []
    lvn: list[float] = []
    for i in range(bins):
        left = volumes[i - 1] if i > 0 else -1.0
        right = volumes[i + 1] if i < bins - 1 else -1.0
        center = profile_bins[i].mid
        is_peak = volumes[i] >= left and volumes[i] >= right
        is_valley = volumes[i] <= left and volumes[i] <= right
        if is_peak and volumes[i] >= node_ratio * baseline:
            hvn.append(center)
        if is_valley and volumes[i] <= baseline / node_ratio:
            lvn.append(center)

    return VolumeProfile(
        bins=profile_bins,
        poc=profile_bins[poc_index].mid,
        vah=profile_bins[max(included)].high,
        val=profile_bins[min(included)].low,
        hvn=tuple(hvn),
        lvn=tuple(lvn),
        total_volume=total,
        value_area_pct=area_volume / total,
    )
