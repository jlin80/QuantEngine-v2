"""APIs internas de detección/cálculo (fachada sobre el Feature Store).

Nombres estables pedidos por la especificación de la fase. Todas cachean vía
Feature Store: llamarlas N veces en la misma ventana cuesta un solo cálculo.
"""

from typing import Any

from app.analytics.indicators import (
    FairValueGap,
    LiquidityMap,
    OrderBlockZone,
    OrderFlowSnapshot,
    SMCAnalysis,
    StructureAnalysis,
    VolumeProfile,
)
from app.engine.feature_store import FeatureStore
from app.engine.models import MarketContext, Regime


async def detect_liquidity(
    features: FeatureStore, symbol: str, **params: Any
) -> LiquidityMap | None:
    """Mapa de liquidez (pools, stop hunts) del símbolo."""
    result = await features.get_object("liquidity", symbol, **params)
    return result if isinstance(result, LiquidityMap) else None


async def detect_fvg(
    features: FeatureStore, symbol: str, **params: Any
) -> tuple[FairValueGap, ...]:
    """Fair Value Gaps abiertos del símbolo."""
    analysis = await _smc(features, symbol, **params)
    return analysis.fvgs if analysis is not None else ()


async def detect_order_block(
    features: FeatureStore, symbol: str, **params: Any
) -> tuple[OrderBlockZone, ...]:
    """Order blocks recientes del símbolo."""
    analysis = await _smc(features, symbol, **params)
    return analysis.order_blocks if analysis is not None else ()


async def _smc(features: FeatureStore, symbol: str, **params: Any) -> SMCAnalysis | None:
    """Análisis SMC compuesto (cacheado)."""
    result = await features.get_object("smc", symbol, **params)
    return result if isinstance(result, SMCAnalysis) else None


async def calculate_vwap(features: FeatureStore, symbol: str, **params: Any) -> float | None:
    """VWAP de sesión (``anchor='day'|'week'|'month'``)."""
    return await features.get("vwap_session", symbol, **params)


async def calculate_delta(features: FeatureStore, symbol: str, **params: Any) -> float | None:
    """Delta agresor de la ventana reciente."""
    return await features.get("delta", symbol, **params)


async def calculate_cvd(features: FeatureStore, symbol: str, **params: Any) -> float | None:
    """CVD (delta acumulado) de la ventana retenida."""
    return await features.get("cvd", symbol, **params)


async def calculate_volume_profile(
    features: FeatureStore, symbol: str, **params: Any
) -> VolumeProfile | None:
    """Perfil de volumen de la ventana configurada."""
    result = await features.get_object("volume_profile", symbol, **params)
    return result if isinstance(result, VolumeProfile) else None


async def calculate_market_structure(
    features: FeatureStore, symbol: str, **params: Any
) -> StructureAnalysis | None:
    """Estructura de mercado (swings, tendencia, consolidación)."""
    result = await features.get_object("structure", symbol, **params)
    return result if isinstance(result, StructureAnalysis) else None


async def calculate_momentum(features: FeatureStore, symbol: str, **params: Any) -> float | None:
    """Momentum score normalizado por volatilidad."""
    return await features.get("momentum_score", symbol, **params)


async def calculate_atr(features: FeatureStore, symbol: str, **params: Any) -> float | None:
    """ATR clásico de la ventana configurada."""
    return await features.get("atr", symbol, **params)


async def calculate_orderflow(
    features: FeatureStore, symbol: str, **params: Any
) -> OrderFlowSnapshot | None:
    """Lectura compuesta de order flow (delta, CVD, absorción...)."""
    result = await features.get_object("orderflow", symbol, **params)
    return result if isinstance(result, OrderFlowSnapshot) else None


def calculate_regime_score(context: MarketContext, preferred: tuple[str, ...]) -> float:
    """Afinidad 0-1 del régimen actual con los preferidos por una estrategia.

    Sin preferencias declaradas devuelve 0.75 (neutro levemente positivo);
    con régimen desconocido, 0.5.
    """
    if not preferred:
        return 0.75
    if context.regime is None or context.regime.primary is Regime.UNKNOWN:
        return 0.5
    active = {context.regime.primary.value, *(tag.value for tag in context.regime.tags)}
    return 1.0 if active & set(preferred) else 0.4
