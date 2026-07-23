"""Pre-chequeos: condiciones mínimas para que una estrategia analice.

Distintos de los filtros de decisión (Fase 3): estos evitan gastar cómputo
cuando ni siquiera tiene sentido evaluar (datos insuficientes, calidad mala,
spread absurdo). Devuelven la razón para la explicabilidad.
"""

from collections.abc import Sequence

from app.engine.interfaces.strategy import AnalysisContext
from app.market.models import Candle


def precheck(
    ctx: AnalysisContext,
    candles: Sequence[Candle],
    *,
    min_candles: int,
    min_data_quality: float,
    max_spread_bps: float | None,
) -> str | None:
    """Return the blocking reason, or ``None`` if analysis may proceed.

    Args:
        ctx: Contexto de análisis.
        candles: Velas disponibles en el timeframe de la estrategia.
        min_candles: Warmup mínimo.
        min_data_quality: Calidad de dato mínima (0-1).
        max_spread_bps: Spread máximo tolerado (None = usar flag del contexto).

    Returns:
        Razón del bloqueo o ``None``.
    """
    if len(candles) < min_candles:
        return f"datos insuficientes ({len(candles)}/{min_candles} velas)"
    if ctx.context.data_quality < min_data_quality:
        return f"calidad de dato {ctx.context.data_quality:.2f} " f"< mínima {min_data_quality:.2f}"
    spread = ctx.context.spread_bps
    if max_spread_bps is not None and spread is not None and spread > max_spread_bps:
        return f"spread {spread:.2f} bps > máximo {max_spread_bps:.2f}"
    return None
