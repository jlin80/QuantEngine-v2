"""Market Structure: swings, HH/HL/LH/LL, tendencia, consolidación, rupturas.

Todo se deriva de pivotes fractales (swing points) con parámetros
configurables — sin reglas rígidas escondidas en el código.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.market.models import Candle


@dataclass(frozen=True, kw_only=True, slots=True)
class SwingPoint:
    """Pivote fractal confirmado.

    Attributes:
        index: Índice de la vela pivote dentro de la ventana analizada.
        price: Precio del pivote (high o low de la vela).
        kind: ``"high"`` o ``"low"``.
        time: Inicio de la vela pivote (UTC).
        label: Etiqueta estructural (``HH``/``HL``/``LH``/``LL`` o "").
    """

    index: int
    price: float
    kind: str
    time: datetime
    label: str = ""


def swing_points(candles: Sequence[Candle], left: int = 3, right: int = 3) -> list[SwingPoint]:
    """Detect fractal swing highs/lows (pivote = extremo local estricto).

    Args:
        candles: Velas ordenadas de más vieja a más nueva.
        left: Velas a la izquierda que debe dominar.
        right: Velas a la derecha que debe dominar.

    Returns:
        Pivotes en orden cronológico, etiquetados HH/HL/LH/LL.
    """
    raw: list[SwingPoint] = []
    for i in range(left, len(candles) - right):
        candle = candles[i]
        neighborhood = [candles[j] for j in range(i - left, i + right + 1) if j != i]
        if candle.high > max(c.high for c in neighborhood):
            raw.append(SwingPoint(index=i, price=candle.high, kind="high", time=candle.start))
        if candle.low < min(c.low for c in neighborhood):
            raw.append(SwingPoint(index=i, price=candle.low, kind="low", time=candle.start))
    raw.sort(key=lambda p: (p.index, p.kind))

    labeled: list[SwingPoint] = []
    last_high: SwingPoint | None = None
    last_low: SwingPoint | None = None
    for point in raw:
        if point.kind == "high":
            label = "" if last_high is None else ("HH" if point.price > last_high.price else "LH")
            last_high = point
        else:
            label = "" if last_low is None else ("HL" if point.price > last_low.price else "LL")
            last_low = point
        labeled.append(
            SwingPoint(
                index=point.index,
                price=point.price,
                kind=point.kind,
                time=point.time,
                label=label,
            )
        )
    return labeled


@dataclass(frozen=True, kw_only=True, slots=True)
class Breakout:
    """Ruptura del rango previo en la última vela.

    Attributes:
        direction: ``"up"`` o ``"down"``.
        level: Nivel roto (extremo del rango previo).
        valid: Cierre más allá del nivel con volumen suficiente.
        volume_ratio: Volumen de la vela de ruptura vs media previa.
    """

    direction: str
    level: float
    valid: bool
    volume_ratio: float


def detect_breakout(
    candles: Sequence[Candle],
    *,
    lookback: int = 20,
    min_volume_ratio: float = 1.2,
) -> Breakout | None:
    """Detect a breakout of the prior ``lookback``-bar range on the last candle.

    Args:
        candles: Velas ordenadas (mínimo ``lookback + 1``).
        lookback: Barras del rango previo (excluye la última).
        min_volume_ratio: Volumen mínimo relativo para validar la ruptura.

    Returns:
        La ruptura (válida o no), o ``None`` si no hay ruptura.
    """
    if len(candles) < lookback + 1:
        return None
    prior = candles[-lookback - 1 : -1]
    last = candles[-1]
    prior_high = max(c.high for c in prior)
    prior_low = min(c.low for c in prior)
    avg_volume = sum(c.volume for c in prior) / len(prior)
    ratio = last.volume / avg_volume if avg_volume > 0 else 0.0

    if last.close > prior_high:
        return Breakout(
            direction="up", level=prior_high, valid=ratio >= min_volume_ratio, volume_ratio=ratio
        )
    if last.close < prior_low:
        return Breakout(
            direction="down", level=prior_low, valid=ratio >= min_volume_ratio, volume_ratio=ratio
        )
    return None


def fake_breakouts(candles: Sequence[Candle], *, lookback: int = 20, scan: int = 5) -> list[str]:
    """Recent failed breakouts: mecha fuera del rango previo con cierre dentro.

    Args:
        candles: Velas ordenadas.
        lookback: Rango de referencia previo a cada vela examinada.
        scan: Cuántas velas recientes examinar.

    Returns:
        Direcciones de las falsas rupturas detectadas (``"up"``/``"down"``).
    """
    fakes: list[str] = []
    for offset in range(scan):
        end = len(candles) - offset
        if end < lookback + 1:
            break
        window = candles[:end]
        prior = window[-lookback - 1 : -1]
        bar = window[-1]
        prior_high = max(c.high for c in prior)
        prior_low = min(c.low for c in prior)
        if bar.high > prior_high and bar.close <= prior_high:
            fakes.append("up")
        if bar.low < prior_low and bar.close >= prior_low:
            fakes.append("down")
    return fakes


@dataclass(frozen=True, kw_only=True, slots=True)
class StructureAnalysis:
    """Fotografía estructural de una ventana de velas.

    Attributes:
        swings: Pivotes etiquetados.
        trend: ``"up"``, ``"down"`` o ``"sideways"`` según etiquetas recientes.
        consolidation: Rango comprimido en las últimas velas.
        range_high / range_low: Extremos del rango reciente.
        last_swing_high / last_swing_low: Últimos pivotes de cada tipo.
        bias: ``"accumulation"``, ``"distribution"`` o ``""`` (heurística CLV).
    """

    swings: tuple[SwingPoint, ...]
    trend: str
    consolidation: bool
    range_high: float
    range_low: float
    last_swing_high: SwingPoint | None
    last_swing_low: SwingPoint | None
    bias: str


def analyze_structure(
    candles: Sequence[Candle],
    *,
    swing_left: int = 3,
    swing_right: int = 3,
    range_window: int = 20,
    consolidation_band_pct: float = 1.0,
) -> StructureAnalysis | None:
    """Full structural read of a candle window.

    Args:
        candles: Velas ordenadas (mínimo ``range_window``).
        swing_left: Velas a la izquierda del fractal de pivotes.
        swing_right: Velas a la derecha del fractal de pivotes.
        range_window: Velas del rango reciente.
        consolidation_band_pct: Ancho máximo del rango (en % del precio) para
            considerar consolidación.

    Returns:
        Análisis completo o ``None`` con datos insuficientes.
    """
    if len(candles) < max(range_window, swing_left + swing_right + 1):
        return None
    swings = swing_points(candles, swing_left, swing_right)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    recent_labels = [s.label for s in swings[-4:] if s.label]
    ups = sum(1 for label in recent_labels if label in ("HH", "HL"))
    downs = sum(1 for label in recent_labels if label in ("LH", "LL"))
    if len(recent_labels) >= 2 and ups and not downs:
        trend = "up"
    elif len(recent_labels) >= 2 and downs and not ups:
        trend = "down"
    else:
        trend = "sideways"

    window = candles[-range_window:]
    range_high = max(c.high for c in window)
    range_low = min(c.low for c in window)
    mid = (range_high + range_low) / 2.0
    consolidation = mid > 0 and (range_high - range_low) / mid * 100.0 <= consolidation_band_pct

    bias = ""
    if consolidation:
        clv_flow = 0.0
        for candle in window:
            span = candle.high - candle.low
            if span > 0:
                clv = ((candle.close - candle.low) - (candle.high - candle.close)) / span
                clv_flow += clv * candle.volume
        total_volume = sum(c.volume for c in window)
        if total_volume > 0:
            normalized = clv_flow / total_volume
            if normalized > 0.15:
                bias = "accumulation"
            elif normalized < -0.15:
                bias = "distribution"

    return StructureAnalysis(
        swings=tuple(swings),
        trend=trend,
        consolidation=consolidation,
        range_high=range_high,
        range_low=range_low,
        last_swing_high=highs[-1] if highs else None,
        last_swing_low=lows[-1] if lows else None,
        bias=bias,
    )
