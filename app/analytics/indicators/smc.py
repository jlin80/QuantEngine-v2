"""Smart Money Concepts: sweeps, EQH/EQL, OB, FVG, BOS/CHOCH/MSS, zonas.

Heurísticas explícitas y 100 % parametrizables — ninguna regla rígida
escondida. Todas las detecciones devuelven estructuras tipadas con el índice
de la vela donde ocurren, para que las estrategias razonen sobre recencia.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.analytics.indicators.atr import atr as _atr
from app.analytics.indicators.structure import SwingPoint, swing_points
from app.market.models import Candle


@dataclass(frozen=True, kw_only=True, slots=True)
class FairValueGap:
    """Fair Value Gap (imbalance de 3 velas).

    Attributes:
        index: Índice de la vela central (la del desplazamiento).
        direction: ``"bullish"`` (gap por debajo) o ``"bearish"``.
        top / bottom: Límites del gap.
        filled_pct: Cuánto del gap fue rellenado después (0-1).
    """

    index: int
    direction: str
    top: float
    bottom: float
    filled_pct: float

    @property
    def mid(self) -> float:
        """Punto medio del gap (consecuente encroachment)."""
        return (self.top + self.bottom) / 2.0

    @property
    def open_fraction(self) -> float:
        """Fracción del gap aún sin rellenar."""
        return 1.0 - self.filled_pct


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderBlockZone:
    """Order Block: última vela contraria antes de un desplazamiento.

    Attributes:
        index: Índice de la vela del OB.
        direction: ``"bullish"`` (demanda) o ``"bearish"`` (oferta).
        top / bottom: Zona del OB (cuerpo o rango completo según parámetro).
        mitigated: El precio ya regresó a la zona después de formarse.
        breaker: El precio cerró más allá del lado opuesto (polaridad rota).
    """

    index: int
    direction: str
    top: float
    bottom: float
    mitigated: bool
    breaker: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class Sweep:
    """Barrido de liquidez sobre un swing (mecha más allá, cierre de vuelta).

    Attributes:
        index: Índice de la vela que barre.
        kind: ``"high"`` (barre máximos) o ``"low"`` (barre mínimos).
        level: Nivel barrido.
        reclaimed: La vela cerró de vuelta dentro (sweep clásico).
    """

    index: int
    kind: str
    level: float
    reclaimed: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class EqualLevels:
    """Cluster de máximos o mínimos iguales (pool de liquidez).

    Attributes:
        kind: ``"high"`` o ``"low"``.
        level: Precio medio del cluster.
        count: Cuántos swings lo componen.
        internal: Dentro del rango vigente (liquidez interna) o en el
            extremo/afuera (externa).
    """

    kind: str
    level: float
    count: int
    internal: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class StructureBreak:
    """Ruptura estructural.

    Attributes:
        index: Índice de la vela que rompe (cierre más allá del swing).
        kind: ``"BOS"`` (a favor de la tendencia) o ``"CHOCH"`` (en contra).
        direction: ``"up"`` o ``"down"`` (dirección de la ruptura).
        level: Swing roto.
        displacement: Cuerpo de la vela de ruptura en múltiplos de ATR.
        mss: CHOCH con desplazamiento fuerte = Market Structure Shift.
    """

    index: int
    kind: str
    direction: str
    level: float
    displacement: float
    mss: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class PremiumDiscount:
    """Posición del precio dentro del rango operativo (equilibrium = 50 %).

    Attributes:
        range_high / range_low: Extremos del rango de referencia.
        equilibrium: Punto medio.
        position: Fracción 0-1 del precio dentro del rango.
        zone: ``"premium"``, ``"discount"`` o ``"equilibrium"``.
    """

    range_high: float
    range_low: float
    equilibrium: float
    position: float
    zone: str


@dataclass(frozen=True, kw_only=True, slots=True)
class SMCAnalysis:
    """Resultado compuesto del análisis SMC de una ventana de velas."""

    swings: tuple[SwingPoint, ...]
    fvgs: tuple[FairValueGap, ...]
    order_blocks: tuple[OrderBlockZone, ...]
    sweeps: tuple[Sweep, ...]
    equal_levels: tuple[EqualLevels, ...]
    breaks: tuple[StructureBreak, ...]
    premium_discount: PremiumDiscount | None
    inducement: SwingPoint | None
    atr: float


def fair_value_gaps(
    candles: Sequence[Candle], *, min_size_atr: float = 0.3, atr_value: float | None = None
) -> list[FairValueGap]:
    """Detect 3-candle fair value gaps with fill tracking.

    Args:
        candles: Velas ordenadas.
        min_size_atr: Tamaño mínimo del gap en múltiplos de ATR.
        atr_value: ATR precalculado (se calcula si falta).

    Returns:
        Gaps en orden cronológico (los rellenados al 100 % se excluyen).
    """
    value = atr_value if atr_value is not None else _atr(candles)
    if value is None or value <= 0 or len(candles) < 3:
        return []
    gaps: list[FairValueGap] = []
    for i in range(1, len(candles) - 1):
        prev, nxt = candles[i - 1], candles[i + 1]
        # Gap alcista: el low de la vela siguiente queda sobre el high previo.
        if nxt.low > prev.high and (nxt.low - prev.high) >= min_size_atr * value:
            top, bottom = nxt.low, prev.high
            lowest_after = min((c.low for c in candles[i + 2 :]), default=top)
            filled = max(0.0, min(1.0, (top - max(lowest_after, bottom)) / (top - bottom)))
            if lowest_after <= bottom:
                filled = 1.0
            if filled < 1.0:
                gaps.append(
                    FairValueGap(
                        index=i, direction="bullish", top=top, bottom=bottom, filled_pct=filled
                    )
                )
        # Gap bajista: el high de la siguiente queda bajo el low previo.
        if prev.low > nxt.high and (prev.low - nxt.high) >= min_size_atr * value:
            top, bottom = prev.low, nxt.high
            highest_after = max((c.high for c in candles[i + 2 :]), default=bottom)
            filled = max(0.0, min(1.0, (min(highest_after, top) - bottom) / (top - bottom)))
            if highest_after >= top:
                filled = 1.0
            if filled < 1.0:
                gaps.append(
                    FairValueGap(
                        index=i, direction="bearish", top=top, bottom=bottom, filled_pct=filled
                    )
                )
    return gaps


def order_blocks(
    candles: Sequence[Candle],
    *,
    displacement_atr: float = 1.5,
    use_full_range: bool = False,
    atr_value: float | None = None,
    max_blocks: int = 10,
) -> list[OrderBlockZone]:
    """Detect order blocks: última vela contraria antes de un desplazamiento.

    Args:
        candles: Velas ordenadas.
        displacement_atr: Recorrido mínimo (en ATRs) de la vela siguiente.
        use_full_range: Zona = rango completo de la vela (default: cuerpo).
        atr_value: ATR precalculado.
        max_blocks: Máximo de bloques recientes a devolver.

    Returns:
        Bloques en orden cronológico con estado de mitigación/breaker.
    """
    value = atr_value if atr_value is not None else _atr(candles)
    if value is None or value <= 0 or len(candles) < 3:
        return []
    blocks: list[OrderBlockZone] = []
    for i in range(len(candles) - 1):
        candle, nxt = candles[i], candles[i + 1]
        bearish_candle = candle.close < candle.open
        bullish_candle = candle.close > candle.open
        up_move = nxt.close - nxt.open
        down_move = nxt.open - nxt.close
        direction = ""
        if bearish_candle and up_move >= displacement_atr * value:
            direction = "bullish"
        elif bullish_candle and down_move >= displacement_atr * value:
            direction = "bearish"
        if not direction:
            continue
        if use_full_range:
            top, bottom = candle.high, candle.low
        else:
            top, bottom = max(candle.open, candle.close), min(candle.open, candle.close)
        after = candles[i + 2 :]
        mitigated = any(c.low <= top and c.high >= bottom for c in after)
        if direction == "bullish":
            breaker = any(c.close < bottom for c in after)
        else:
            breaker = any(c.close > top for c in after)
        blocks.append(
            OrderBlockZone(
                index=i,
                direction=direction,
                top=top,
                bottom=bottom,
                mitigated=mitigated,
                breaker=breaker,
            )
        )
    return blocks[-max_blocks:]


def liquidity_sweeps(
    candles: Sequence[Candle],
    swings: Sequence[SwingPoint],
    *,
    scan: int = 5,
    tolerance_pct: float = 0.02,
) -> list[Sweep]:
    """Detect sweeps of prior swing levels in the most recent candles.

    Args:
        candles: Velas ordenadas.
        swings: Pivotes precalculados.
        scan: Cuántas velas recientes examinar.
        tolerance_pct: Cuánto debe superar la mecha al nivel (% del nivel).

    Returns:
        Sweeps detectados en orden cronológico.
    """
    sweeps: list[Sweep] = []
    start = max(0, len(candles) - scan)
    for i in range(start, len(candles)):
        candle = candles[i]
        for swing in swings:
            if swing.index >= i:
                continue
            tolerance = swing.price * tolerance_pct / 100.0
            if swing.kind == "high" and candle.high > swing.price + tolerance:
                sweeps.append(
                    Sweep(
                        index=i,
                        kind="high",
                        level=swing.price,
                        reclaimed=candle.close < swing.price,
                    )
                )
            elif swing.kind == "low" and candle.low < swing.price - tolerance:
                sweeps.append(
                    Sweep(
                        index=i,
                        kind="low",
                        level=swing.price,
                        reclaimed=candle.close > swing.price,
                    )
                )
    return sweeps


def equal_levels(
    swings: Sequence[SwingPoint],
    *,
    tolerance_pct: float = 0.05,
    range_high: float | None = None,
    range_low: float | None = None,
) -> list[EqualLevels]:
    """Cluster equal highs / equal lows (liquidez descansando).

    Args:
        swings: Pivotes precalculados.
        tolerance_pct: Distancia máxima entre swings del cluster (% precio).
        range_high: Techo del rango vigente (clasifica interna/externa).
        range_low: Piso del rango vigente (clasifica interna/externa).

    Returns:
        Clusters con al menos 2 swings.
    """
    results: list[EqualLevels] = []
    for kind in ("high", "low"):
        points = sorted(s.price for s in swings if s.kind == kind)
        cluster: list[float] = []
        for price in points:
            if not cluster or abs(price - cluster[-1]) <= cluster[-1] * tolerance_pct / 100.0:
                cluster.append(price)
                continue
            if len(cluster) >= 2:
                results.append(_make_equal(kind, cluster, range_high, range_low))
            cluster = [price]
        if len(cluster) >= 2:
            results.append(_make_equal(kind, cluster, range_high, range_low))
    return results


def _make_equal(
    kind: str, cluster: list[float], range_high: float | None, range_low: float | None
) -> EqualLevels:
    """Build an EqualLevels marking internal/external liquidity."""
    level = sum(cluster) / len(cluster)
    internal = True
    if range_high is not None and range_low is not None:
        band = (range_high - range_low) * 0.1
        internal = range_low + band < level < range_high - band
    return EqualLevels(kind=kind, level=level, count=len(cluster), internal=internal)


def structure_breaks(
    candles: Sequence[Candle],
    swings: Sequence[SwingPoint],
    *,
    mss_displacement_atr: float = 1.5,
    atr_value: float | None = None,
) -> list[StructureBreak]:
    """Detect BOS / CHOCH / MSS chronologically.

    Regla: un cierre por encima del último swing high rompe estructura al
    alza; es BOS si la tendencia vigente era alcista, CHOCH si era bajista.
    Un CHOCH con desplazamiento ≥ ``mss_displacement_atr`` ATRs es un MSS.

    Args:
        candles: Velas ordenadas.
        swings: Pivotes precalculados.
        mss_displacement_atr: Umbral de desplazamiento del MSS.
        atr_value: ATR precalculado.

    Returns:
        Rupturas en orden cronológico.
    """
    value = atr_value if atr_value is not None else _atr(candles)
    if value is None or value <= 0:
        return []
    breaks: list[StructureBreak] = []
    trend = ""
    last_high: SwingPoint | None = None
    last_low: SwingPoint | None = None
    swings_by_index: dict[int, list[SwingPoint]] = {}
    for swing in swings:
        swings_by_index.setdefault(swing.index, []).append(swing)

    for i, candle in enumerate(candles):
        body = abs(candle.close - candle.open)
        displacement = body / value
        if last_high is not None and candle.close > last_high.price and i > last_high.index:
            kind = "BOS" if trend == "up" else ("CHOCH" if trend == "down" else "BOS")
            mss = kind == "CHOCH" and displacement >= mss_displacement_atr
            breaks.append(
                StructureBreak(
                    index=i,
                    kind=kind,
                    direction="up",
                    level=last_high.price,
                    displacement=displacement,
                    mss=mss,
                )
            )
            trend = "up"
            last_high = None  # esperar un nuevo swing para el próximo break
        elif last_low is not None and candle.close < last_low.price and i > last_low.index:
            kind = "BOS" if trend == "down" else ("CHOCH" if trend == "up" else "BOS")
            mss = kind == "CHOCH" and displacement >= mss_displacement_atr
            breaks.append(
                StructureBreak(
                    index=i,
                    kind=kind,
                    direction="down",
                    level=last_low.price,
                    displacement=displacement,
                    mss=mss,
                )
            )
            trend = "down"
            last_low = None
        for swing in swings_by_index.get(i, ()):  # los pivotes se activan al confirmarse
            if swing.kind == "high":
                last_high = swing
            else:
                last_low = swing
    return breaks


def premium_discount(
    candles: Sequence[Candle], swings: Sequence[SwingPoint], *, equilibrium_band: float = 0.1
) -> PremiumDiscount | None:
    """Premium/discount del precio dentro del rango swing-a-swing vigente.

    Args:
        candles: Velas ordenadas.
        swings: Pivotes precalculados.
        equilibrium_band: Banda alrededor del 50 % considerada equilibrium.

    Returns:
        Zona actual o ``None`` sin rango definible.
    """
    highs = [s.price for s in swings if s.kind == "high"]
    lows = [s.price for s in swings if s.kind == "low"]
    if not highs or not lows or not candles:
        return None
    range_high, range_low = max(highs), min(lows)
    if range_high <= range_low:
        return None
    price = candles[-1].close
    position = (price - range_low) / (range_high - range_low)
    if abs(position - 0.5) <= equilibrium_band:
        zone = "equilibrium"
    else:
        zone = "premium" if position > 0.5 else "discount"
    return PremiumDiscount(
        range_high=range_high,
        range_low=range_low,
        equilibrium=(range_high + range_low) / 2.0,
        position=max(0.0, min(1.0, position)),
        zone=zone,
    )


def _inducement(
    swings: Sequence[SwingPoint], breaks: Sequence[StructureBreak]
) -> SwingPoint | None:
    """Inducement: el swing menor contrario formado tras la última ruptura.

    Heurística documentada: tras un BOS/CHOCH alcista, el primer swing low
    posterior es liquidez de inducción (y viceversa).
    """
    if not breaks:
        return None
    last = breaks[-1]
    wanted = "low" if last.direction == "up" else "high"
    candidates = [s for s in swings if s.index > last.index and s.kind == wanted]
    return candidates[0] if candidates else None


def analyze_smc(
    candles: Sequence[Candle],
    *,
    swing_left: int = 3,
    swing_right: int = 3,
    fvg_min_atr: float = 0.3,
    ob_displacement_atr: float = 1.5,
    ob_full_range: bool = False,
    sweep_scan: int = 5,
    sweep_tolerance_pct: float = 0.02,
    equal_tolerance_pct: float = 0.05,
    mss_displacement_atr: float = 1.5,
    atr_period: int = 14,
) -> SMCAnalysis | None:
    """Composite Smart-Money read of a candle window (una sola pasada).

    Returns:
        Análisis completo o ``None`` con datos insuficientes.
    """
    if len(candles) < max(atr_period + 1, swing_left + swing_right + 3):
        return None
    value = _atr(candles, atr_period)
    if value is None or value <= 0:
        return None
    swings = swing_points(candles, swing_left, swing_right)
    breaks = structure_breaks(
        candles, swings, mss_displacement_atr=mss_displacement_atr, atr_value=value
    )
    zone = premium_discount(candles, swings)
    return SMCAnalysis(
        swings=tuple(swings),
        fvgs=tuple(fair_value_gaps(candles, min_size_atr=fvg_min_atr, atr_value=value)),
        order_blocks=tuple(
            order_blocks(
                candles,
                displacement_atr=ob_displacement_atr,
                use_full_range=ob_full_range,
                atr_value=value,
            )
        ),
        sweeps=tuple(
            liquidity_sweeps(candles, swings, scan=sweep_scan, tolerance_pct=sweep_tolerance_pct)
        ),
        equal_levels=tuple(
            equal_levels(
                swings,
                tolerance_pct=equal_tolerance_pct,
                range_high=zone.range_high if zone else None,
                range_low=zone.range_low if zone else None,
            )
        ),
        breaks=tuple(breaks),
        premium_discount=zone,
        inducement=_inducement(swings, breaks),
        atr=value,
    )
