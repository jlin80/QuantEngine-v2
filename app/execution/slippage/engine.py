"""Motor de slippage: cuánto peor se ejecuta un fill respecto a la referencia.

El slippage crece con la volatilidad (ATR), con la falta de liquidez, con el
tamaño relativo de la orden y fuera de las sesiones líquidas. Las órdenes stop
pagan una penalización extra (persiguen el mercado). El resultado se expresa en
puntos básicos y siempre penaliza al que ejecuta (nunca mejora el precio).
"""

import random
from dataclasses import dataclass

from app.config.settings import SlippageSettings
from app.execution.models.enums import OrderType


@dataclass(frozen=True, slots=True)
class SlippageContext:
    """Contexto de mercado usado para estimar el slippage.

    Attributes:
        atr_pct: ATR como % del precio (proxy de volatilidad).
        spread_bps: Spread actual en bps.
        available_liquidity: Liquidez disponible (0 = desconocida).
        order_quantity: Cantidad de la orden.
        session: Sesión de mercado activa (``asia``/``europe``/``america``).
        order_type: Tipo de orden.
    """

    atr_pct: float | None = None
    spread_bps: float | None = None
    available_liquidity: float = 0.0
    order_quantity: float = 0.0
    session: str = "off"
    order_type: OrderType = OrderType.MARKET


class SlippageEngine:
    """Estimate execution slippage in basis points.

    Args:
        settings: Slippage model configuration.
        rng: Fuente aleatoria inyectable (tests deterministas).
    """

    def __init__(self, settings: SlippageSettings, rng: random.Random | None = None) -> None:
        self._settings = settings
        self._rng = rng or random.Random()

    def estimate_bps(self, context: SlippageContext) -> float:
        """Estimate slippage in bps for an order in a market context.

        Args:
            context: Datos de mercado y de la orden.

        Returns:
            Slippage en puntos básicos (>= 0), acotado por ``max_bps``.
        """
        model = self._settings.model
        if model == "none":
            return 0.0
        bps = self._settings.base_bps
        if model == "dynamic":
            if context.atr_pct is not None:
                bps += self._settings.volatility_coeff * context.atr_pct
            bps += self._liquidity_penalty(context)
            bps += self._size_penalty(context)
            if context.order_type in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT):
                bps += self._settings.stop_order_extra_bps
            multiplier = self._settings.session_multipliers.get(
                context.session, self._settings.session_multipliers.get("off", 1.0)
            )
            bps *= multiplier
        # Ruido no negativo (nunca mejora el precio): media 1, jitter ±10%.
        bps *= 1.0 + self._rng.uniform(0.0, 0.1)
        return round(min(max(bps, 0.0), self._settings.max_bps), 4)

    def _liquidity_penalty(self, context: SlippageContext) -> float:
        """Penalización por escasa profundidad frente al tamaño de la orden."""
        if context.available_liquidity <= 0 or context.order_quantity <= 0:
            return 0.0
        ratio = context.order_quantity / context.available_liquidity
        return self._settings.liquidity_coeff * min(ratio, 5.0) * 10.0

    def _size_penalty(self, context: SlippageContext) -> float:
        """Penalización por impacto de mercado del propio tamaño."""
        if context.order_quantity <= 0 or context.available_liquidity <= 0:
            return 0.0
        ratio = context.order_quantity / context.available_liquidity
        return self._settings.size_coeff * min(ratio, 5.0) * 10.0
