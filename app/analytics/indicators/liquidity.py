"""Liquidity Engine: pools, stop hunts, grabs, rechazo y continuación."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.analytics.indicators.smc import equal_levels
from app.analytics.indicators.structure import SwingPoint, swing_points
from app.market.models import Candle


@dataclass(frozen=True, kw_only=True, slots=True)
class LiquidityPool:
    """Zona con liquidez descansando (stops agrupados).

    Attributes:
        level: Precio del pool.
        kind: ``"high"`` (stops de cortos encima) o ``"low"``.
        strength: Toques/miembros que lo forman (más = más liquidez).
        swept: Ya fue barrido dentro de la ventana.
        internal: Liquidez interna (dentro del rango) o externa.
    """

    level: float
    kind: str
    strength: int
    swept: bool
    internal: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class StopHunt:
    """Barrido de un pool con lectura del resultado.

    Attributes:
        index: Vela del barrido.
        pool: Pool barrido.
        rejection: La vela cerró de vuelta con mecha dominante (rechazo).
        continuation: La vela cerró más allá del pool (ruptura genuina).
        wick_ratio: Mecha del lado barrido / rango de la vela (0-1).
    """

    index: int
    pool: LiquidityPool
    rejection: bool
    continuation: bool
    wick_ratio: float


@dataclass(frozen=True, kw_only=True, slots=True)
class LiquidityMap:
    """Mapa de liquidez de la ventana analizada."""

    pools: tuple[LiquidityPool, ...]
    hunts: tuple[StopHunt, ...]
    swings: tuple[SwingPoint, ...]

    def nearest_pool(self, price: float, kind: str) -> LiquidityPool | None:
        """Pool no barrido más cercano por encima (high) o debajo (low)."""
        candidates = [
            p
            for p in self.pools
            if p.kind == kind
            and not p.swept
            and ((kind == "high" and p.level > price) or (kind == "low" and p.level < price))
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda p: abs(p.level - price))


def analyze_liquidity(
    candles: Sequence[Candle],
    *,
    swing_left: int = 3,
    swing_right: int = 3,
    equal_tolerance_pct: float = 0.05,
    hunt_scan: int = 5,
    rejection_wick_ratio: float = 0.5,
) -> LiquidityMap | None:
    """Build the liquidity map: pools + stop hunts recientes.

    Pools = clusters de máximos/mínimos iguales y los extremos del rango
    (liquidez externa). Un stop hunt = mecha que barre el pool; se clasifica
    como rechazo (cierre de vuelta con mecha dominante) o continuación.

    Args:
        candles: Velas ordenadas.
        swing_left: Velas a la izquierda del fractal de pivotes.
        swing_right: Velas a la derecha del fractal de pivotes.
        equal_tolerance_pct: Tolerancia del cluster de niveles iguales.
        hunt_scan: Velas recientes donde buscar barridos.
        rejection_wick_ratio: Mecha mínima (fracción del rango) para rechazo.

    Returns:
        Mapa de liquidez o ``None`` con datos insuficientes.
    """
    if len(candles) < swing_left + swing_right + 3:
        return None
    swings = swing_points(candles, swing_left, swing_right)
    if not swings:
        return None
    range_high = max(c.high for c in candles)
    range_low = min(c.low for c in candles)

    pools: list[LiquidityPool] = []
    for eq in equal_levels(
        swings, tolerance_pct=equal_tolerance_pct, range_high=range_high, range_low=range_low
    ):
        pools.append(
            LiquidityPool(
                level=eq.level, kind=eq.kind, strength=eq.count, swept=False, internal=eq.internal
            )
        )
    # Extremos del rango = liquidez externa (strength 1 si no hay cluster ahí).
    if not any(p.kind == "high" and abs(p.level - range_high) < 1e-12 for p in pools):
        pools.append(
            LiquidityPool(level=range_high, kind="high", strength=1, swept=False, internal=False)
        )
    if not any(p.kind == "low" and abs(p.level - range_low) < 1e-12 for p in pools):
        pools.append(
            LiquidityPool(level=range_low, kind="low", strength=1, swept=False, internal=False)
        )

    hunts: list[StopHunt] = []
    updated: list[LiquidityPool] = []
    start = max(0, len(candles) - hunt_scan)
    for pool in pools:
        swept = False
        for i in range(start, len(candles)):
            candle = candles[i]
            span = candle.high - candle.low
            if span <= 0:
                continue
            if pool.kind == "high" and candle.high > pool.level:
                # El pool de máximos solo cuenta si existía antes de la vela.
                if pool.level >= candle.high and i == 0:
                    continue
                swept = True
                wick = (candle.high - max(candle.close, candle.open)) / span
                rejection = candle.close < pool.level and wick >= rejection_wick_ratio
                continuation = candle.close > pool.level
                hunts.append(
                    StopHunt(
                        index=i,
                        pool=pool,
                        rejection=rejection,
                        continuation=continuation,
                        wick_ratio=wick,
                    )
                )
                break
            if pool.kind == "low" and candle.low < pool.level:
                swept = True
                wick = (min(candle.close, candle.open) - candle.low) / span
                rejection = candle.close > pool.level and wick >= rejection_wick_ratio
                continuation = candle.close < pool.level
                hunts.append(
                    StopHunt(
                        index=i,
                        pool=pool,
                        rejection=rejection,
                        continuation=continuation,
                        wick_ratio=wick,
                    )
                )
                break
        updated.append(
            LiquidityPool(
                level=pool.level,
                kind=pool.kind,
                strength=pool.strength,
                swept=swept,
                internal=pool.internal,
            )
        )

    return LiquidityMap(pools=tuple(updated), hunts=tuple(hunts), swings=tuple(swings))
