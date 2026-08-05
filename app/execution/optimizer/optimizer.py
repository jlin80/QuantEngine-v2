"""Execution Optimizer (Bloque 6) — elegir cómo entrar, no sólo si entrar.

Compara las tres formas de entrar (IOC, LIMIT, MARKET) antes de mandar nada, y
elige la de mejor calidad esperada. La comparación es un balance entre dos
costes que tiran en direcciones opuestas:

- **MARKET** paga el spread y el slippage, pero se ejecuta seguro.
- **LIMIT** no paga spread —incluso lo cobra—, pero puede no ejecutarse, y una
  entrada que no ocurre tiene su propio coste: la operación que se pierde.
- **IOC** está en medio: es agresivo como MARKET —cruza el spread— pero cancela
  lo que no encuentra contrapartida, así que su riesgo no es quedarse fuera del
  todo sino quedarse **a medias**.

**Lo que hace útil a este módulo es el coste de no ejecutar.** Sin él, LIMIT
gana siempre —es el que menos cuesta cuando se llena— y el optimizador se
convertiría en una máquina de no operar. Ese coste se expresa en el mismo
lenguaje que el resto (bps sobre el nocional) para que las tres opciones sean
comparables con una sola cifra.
"""

import logging
from dataclasses import dataclass
from typing import Any

from app.config.settings import ExecutionOptimizerSettings
from app.execution.latency.engine import LatencyEngine
from app.execution.models.enums import OrderType
from app.execution.slippage.engine import SlippageContext, SlippageEngine

# Las tres tácticas que compara el bloque. `IOC` no es un `OrderType` del
# dominio —el broker lo expresa como límite con time-in-force— así que aquí es
# una táctica, y la traducción a orden concreta la hace `order_type`.
TACTICS: tuple[str, ...] = ("market", "limit", "ioc")


@dataclass(frozen=True, kw_only=True, slots=True)
class ExecutionContext:
    """Lo que se sabe del mercado y de la orden antes de mandarla.

    Attributes:
        symbol: Activo.
        quantity: Cantidad a ejecutar.
        spread_bps: Spread actual.
        atr_pct: Volatilidad relativa.
        session: Sesión activa.
        available_liquidity: Profundidad estimada (0 = desconocida).
        queue_imbalance: Desequilibrio del libro (Bloque 3), si es observable.
            Empuja la probabilidad de llenado de un límite: con la cola a favor
            el límite se llena; con la cola en contra, el precio se va sin él.
        urgency: 0-1. Cuánto duele no entrar. Con urgencia alta, el coste de no
            ejecutar domina y MARKET gana aunque sea el más caro por bps.
    """

    symbol: str
    quantity: float = 0.0
    spread_bps: float | None = None
    atr_pct: float | None = None
    session: str = "off"
    available_liquidity: float = 0.0
    queue_imbalance: float | None = None
    urgency: float = 0.5


@dataclass(frozen=True, kw_only=True, slots=True)
class TacticQuote:
    """Coste esperado de una táctica concreta.

    Attributes:
        tactic: ``market`` / ``limit`` / ``ioc``.
        order_type: Tipo de orden con el que se materializa.
        expected_slippage_bps: Slippage esperado.
        expected_latency_ms: Latencia esperada de extremo a extremo.
        fill_probability: Probabilidad de ejecutarse (1.0 en market).
        spread_cost_bps: Coste (o ingreso, si es negativo) del spread.
        expected_cost_bps: Coste total esperado, **incluido** el coste de no
            ejecutar ponderado por su probabilidad.
        quality_score: 0-100. Es el coste traducido a una escala legible; sirve
            para ordenar y para auditar, no aporta información nueva.
    """

    tactic: str
    order_type: OrderType
    expected_slippage_bps: float
    expected_latency_ms: float
    fill_probability: float
    spread_cost_bps: float
    expected_cost_bps: float
    quality_score: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "tactic": self.tactic,
            "order_type": self.order_type.value,
            "expected_slippage_bps": round(self.expected_slippage_bps, 4),
            "expected_latency_ms": round(self.expected_latency_ms, 3),
            "fill_probability": round(self.fill_probability, 4),
            "spread_cost_bps": round(self.spread_cost_bps, 4),
            "expected_cost_bps": round(self.expected_cost_bps, 4),
            "quality_score": round(self.quality_score, 2),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class ExecutionPlan:
    """La táctica elegida, con las descartadas y el porqué."""

    symbol: str
    chosen: TacticQuote
    alternatives: tuple[TacticQuote, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "symbol": self.symbol,
            "chosen": self.chosen.to_dict(),
            # Las descartadas viajan siempre: sin ellas, "se eligió MARKET" no
            # se puede auditar — no se sabe por cuánto ganó ni frente a qué.
            "alternatives": [quote.to_dict() for quote in self.alternatives],
            "reason": self.reason,
        }


class ExecutionOptimizer:
    """Pick the cheapest way to enter, given what the market looks like now.

    Args:
        settings: Pesos y umbrales de la comparación.
        slippage: Motor de slippage ya existente (Fase 5): se reutiliza en vez
            de duplicar el modelo, para que optimizar y simular no discrepen.
        latency: Motor de latencia ya existente.
    """

    def __init__(
        self,
        settings: ExecutionOptimizerSettings,
        slippage: SlippageEngine,
        latency: LatencyEngine,
    ) -> None:
        self._settings = settings
        self._slippage = slippage
        self._latency = latency
        self._log = logging.getLogger("app.execution.optimizer")

    def quote(self, tactic: str, context: ExecutionContext) -> TacticQuote:
        """Estimate the full expected cost of one tactic.

        Args:
            tactic: Una de :data:`TACTICS`.
            context: Estado del mercado y de la orden.

        Returns:
            La cotización de esa táctica.
        """
        order_type = OrderType.MARKET if tactic == "market" else OrderType.LIMIT
        spread = context.spread_bps if context.spread_bps is not None else 0.0
        latency_quote = self._latency.sample()

        if tactic == "market":
            slippage = self._slippage.estimate_bps(
                SlippageContext(
                    atr_pct=context.atr_pct,
                    spread_bps=context.spread_bps,
                    available_liquidity=context.available_liquidity,
                    order_quantity=context.quantity,
                    session=context.session,
                    order_type=OrderType.MARKET,
                )
            )
            # Cruzar el spread cuesta la mitad de su anchura respecto al medio.
            spread_cost = spread / 2.0
            fill = 1.0
        elif tactic == "limit":
            # Un límite pasivo no sufre slippage por definición: o se llena a su
            # precio o no se llena. Su riesgo es el otro, y está en `fill`.
            slippage = 0.0
            # Y, al reposar, *cobra* medio spread en vez de pagarlo.
            spread_cost = -spread / 2.0
            fill = self._fill_probability(tactic, context)
        else:
            # IOC es agresivo: cruza el spread igual que MARKET, y por eso paga
            # su mitad y sufre slippage. Modelarlo como si cobrara el spread era
            # regalarle la ventaja del pasivo Y la del agresivo a la vez, y con
            # eso ganaba siempre — un optimizador que elige por un error de
            # contabilidad es peor que no tener optimizador.
            slippage = self._slippage.estimate_bps(
                SlippageContext(
                    atr_pct=context.atr_pct,
                    spread_bps=context.spread_bps,
                    available_liquidity=context.available_liquidity,
                    order_quantity=context.quantity,
                    session=context.session,
                    order_type=OrderType.LIMIT,
                )
            )
            spread_cost = spread / 2.0
            fill = self._fill_probability(tactic, context)

        latency_ms = latency_quote.total_ms
        # La latencia se paga siempre: durante ese tiempo el precio se mueve.
        direct_cost = slippage + spread_cost + latency_quote.drift_bps
        # Y aquí está el corazón del bloque: lo que cuesta NO entrar.
        miss_cost = (1.0 - fill) * self._miss_cost_bps(context)
        expected = direct_cost * fill + miss_cost
        return TacticQuote(
            tactic=tactic,
            order_type=order_type,
            expected_slippage_bps=slippage,
            expected_latency_ms=latency_ms,
            fill_probability=fill,
            spread_cost_bps=spread_cost,
            expected_cost_bps=expected,
            quality_score=self._quality(expected),
        )

    def optimize(self, context: ExecutionContext) -> ExecutionPlan:
        """Compare every tactic and pick the cheapest expected cost.

        Args:
            context: Estado del mercado y de la orden.

        Returns:
            El plan, con las alternativas descartadas y el motivo de la
            elección. Los empates los gana **MARKET**: ante coste esperado
            igual, la táctica que sí ejecuta es la que cumple la decisión que
            el motor ya tomó.
        """
        quotes = [self.quote(tactic, context) for tactic in TACTICS]
        quotes.sort(key=lambda q: (q.expected_cost_bps, 0 if q.tactic == "market" else 1))
        chosen = quotes[0]
        runner_up = quotes[1] if len(quotes) > 1 else None
        margin = (
            None if runner_up is None else runner_up.expected_cost_bps - chosen.expected_cost_bps
        )
        reason = f"{chosen.tactic}: coste esperado {chosen.expected_cost_bps:.2f} bps" + (
            f", {margin:.2f} bps mejor que {runner_up.tactic}"
            if runner_up is not None and margin is not None
            else ""
        )
        return ExecutionPlan(
            symbol=context.symbol,
            chosen=chosen,
            alternatives=tuple(quotes[1:]),
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Piezas del modelo
    # ------------------------------------------------------------------

    def _fill_probability(self, tactic: str, context: ExecutionContext) -> float:
        """Probability that a resting order actually gets filled.

        Parte de una base por táctica y la corrige con el desequilibrio del
        libro cuando **es observable** (Bloque 3). Con la cola a favor el
        límite se llena; con la cola en contra, el precio se va sin él. Si no
        hay libro —el caso de MT5— se queda en la base y no se inventa una
        corrección: ese es justo el escenario en el que un optimizador
        demasiado confiado empieza a preferir límites que nunca se llenan.
        """
        base = self._settings.limit_fill_base if tactic == "limit" else self._settings.ioc_fill_base
        if context.queue_imbalance is None:
            return base
        adjusted = base + self._settings.imbalance_coeff * context.queue_imbalance
        return max(0.0, min(1.0, adjusted))

    def _miss_cost_bps(self, context: ExecutionContext) -> float:
        """Cost, in bps, of not entering at all.

        Es una **política**, no una medida: dice cuánto vale la operación que
        se pierde. Se escala con la urgencia para que el mismo motor sirva a
        una salida de emergencia y a una entrada oportunista. Sin este término
        LIMIT ganaría siempre y el optimizador sería una máquina de no operar.
        """
        return self._settings.miss_cost_bps * max(0.0, min(1.0, context.urgency))

    def _quality(self, expected_cost_bps: float) -> float:
        """Translate expected cost into a readable 0-100 score."""
        reference = max(self._settings.quality_reference_bps, 1e-6)
        raw = 100.0 * (1.0 - expected_cost_bps / reference)
        return max(0.0, min(100.0, raw))
