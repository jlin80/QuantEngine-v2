"""Position Quality Engine (Bloque 7) — un veto independiente del score.

El Decision Engine ya puntúa la oportunidad. Este motor puntúa algo distinto:
**la calidad de la posición que saldría de ella**. Son cosas separadas y por eso
es un motor aparte y no un factor más del score. Una señal excelente en un
mercado sin liquidez, con el coste de entrada comiéndose la mitad de la R
esperada y con el riesgo mal dimensionado, es una mala posición aunque sea una
buena señal.

**Seis dimensiones, todas opcionales.** Setup, ejecución, riesgo, liquidez,
contexto y coste esperado. Cada una entra sólo si es observable; las que no lo
son quedan fuera del promedio en vez de contar como cero. La diferencia importa:
un cero dice "es malo", y la ausencia dice "no se sabe" — y bloquear una
operación por lo segundo es bloquear por ignorancia.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import QuantPositionQualitySettings
from app.engine.models import ConsensusResult, MarketContext, VolatilityState

# Las seis dimensiones del bloque, en el orden en que se leen en el informe.
DIMENSIONS: tuple[str, ...] = (
    "setup",
    "execution",
    "risk",
    "liquidity",
    "context",
    "cost",
)


@dataclass(frozen=True, kw_only=True, slots=True)
class QualityAssessment:
    """Calidad de la posición que saldría de una decisión.

    Attributes:
        symbol: Activo.
        score: 0-100, o ``None`` si no había dimensiones observables.
        components: Cada dimensión observada, en 0-1.
        missing: Dimensiones no observables, con su motivo. Se reportan a
            propósito: un score de 72 sobre dos dimensiones no es el mismo
            número que un 72 sobre seis, y quien lo lea tiene que poder verlo.
        blocked: Si esta calidad veta la operación.
        reasons: Por qué (siempre presente, bloquee o no).
    """

    symbol: str
    score: float | None
    components: dict[str, float] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    blocked: bool = False
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "symbol": self.symbol,
            "score": None if self.score is None else round(self.score, 2),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "missing": self.missing,
            "observed": len(self.components),
            "blocked": self.blocked,
            "reasons": list(self.reasons),
        }


class PositionQualityEngine:
    """Score the position a decision would open, and veto the bad ones.

    Args:
        settings: Pesos, mínimos y umbral de veto.
    """

    def __init__(self, settings: QuantPositionQualitySettings) -> None:
        self._settings = settings
        self._log = logging.getLogger("app.engine.position_quality")

    def assess(
        self,
        context: MarketContext,
        consensus: ConsensusResult,
        *,
        expected_cost_bps: float | None = None,
        expected_r: float | None = None,
        risk_pct: float | None = None,
        max_risk_pct: float | None = None,
    ) -> QualityAssessment:
        """Evaluate the six dimensions and decide whether to block.

        Args:
            context: Contexto de mercado del símbolo.
            consensus: Consenso alcanzado (setup).
            expected_cost_bps: Coste de entrada estimado (Bloque 6).
            expected_r: R esperada de la operación, para poder comparar el
                coste contra lo que se espera ganar. Sin ella el coste se juzga
                contra un techo absoluto, que es peor pero no inventa nada.
            risk_pct: Riesgo propuesto, en % de la cuenta.
            max_risk_pct: Riesgo máximo permitido.

        Returns:
            La evaluación completa, con sus dimensiones ausentes declaradas.
        """
        components: dict[str, float] = {}
        missing: dict[str, str] = {}

        components["setup"] = _clamp01(
            (consensus.score / 100.0) * 0.6 + max(0.0, min(1.0, consensus.agreement)) * 0.4
        )

        if context.data_quality > 0.0:
            components["execution"] = _clamp01(context.data_quality)
        else:
            # Calidad de dato 0 no es "malo": es que nadie la ha medido todavía.
            missing["execution"] = "sin calidad de dato medida"

        if risk_pct is not None and max_risk_pct is not None and max_risk_pct > 0:
            # 1.0 cuando el riesgo propuesto es holgado; 0.0 al llegar al techo.
            components["risk"] = _clamp01(1.0 - risk_pct / max_risk_pct)
        else:
            missing["risk"] = "riesgo propuesto o techo no disponibles"

        liquidity = _liquidity_score(context)
        if liquidity is None:
            missing["liquidity"] = "sin spread ni volumen observables"
        else:
            components["liquidity"] = liquidity

        components["context"] = _context_score(context)

        cost = _cost_score(expected_cost_bps, expected_r, self._settings.max_cost_bps)
        if cost is None:
            missing["cost"] = "coste de entrada no estimado"
        else:
            components["cost"] = cost

        score = self._score(components)
        blocked, reasons = self._verdict(score, components, missing)
        return QualityAssessment(
            symbol=context.symbol,
            score=score,
            components=components,
            missing=missing,
            blocked=blocked,
            reasons=reasons,
        )

    def _score(self, components: dict[str, float]) -> float | None:
        """Weighted mean of the observed dimensions only."""
        weights = self._settings.weights
        total = 0.0
        weighted = 0.0
        for name, value in components.items():
            weight = float(weights.get(name, 1.0))
            total += weight
            weighted += weight * value
        if total <= 0.0:
            return None
        return 100.0 * weighted / total

    def _verdict(
        self,
        score: float | None,
        components: dict[str, float],
        missing: dict[str, str],
    ) -> tuple[bool, tuple[str, ...]]:
        """Decide whether the quality vetoes the trade, and say why.

        **Fail-open por debajo del mínimo de evidencia.** Con menos dimensiones
        observables de las exigidas, no se bloquea: se declara. Bloquear ahí
        sería bloquear por ignorancia, y con el bróker actual —sin libro, sin
        coste estimado en algunos símbolos— eso apagaría el motor sin que
        ningún log dijera nada raro.
        """
        if score is None:
            return False, ("sin dimensiones observables: no se puede juzgar",)
        if len(components) < self._settings.min_dimensions:
            return False, (
                f"sólo {len(components)} de {len(DIMENSIONS)} dimensiones observables "
                f"(mínimo {self._settings.min_dimensions}): no se bloquea por ignorancia",
            )
        reasons: list[str] = []
        for name, value in sorted(components.items(), key=lambda item: item[1]):
            floor = self._settings.dimension_floors.get(name)
            if floor is not None and value < floor:
                reasons.append(f"{name} {value:.2f} < mínimo {floor}")
        if score < self._settings.min_score:
            reasons.insert(0, f"calidad {score:.1f} < mínimo {self._settings.min_score}")
            return True, tuple(reasons)
        if reasons and self._settings.block_on_floor_breach:
            # Una dimensión por los suelos puede vetar aunque la media salve:
            # promediar deja que una liquidez pésima se esconda detrás de un
            # setup excelente, y esa es exactamente la posición que duele.
            return True, tuple(reasons)
        detail = f"calidad {score:.1f}/100 sobre {len(components)} dimensiones"
        # Los avisos que no llegan a veto viajan igual: "paso, pero la liquidez
        # esta al limite" es informacion, y solo se ve si se conserva.
        return False, (*reasons, detail)

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "min_score": self._settings.min_score,
            "min_dimensions": self._settings.min_dimensions,
            "weights": dict(self._settings.weights),
            "dimension_floors": dict(self._settings.dimension_floors),
        }


# ---------------------------------------------------------------------------
# Dimensiones
# ---------------------------------------------------------------------------


def _liquidity_score(context: MarketContext) -> float | None:
    """Liquidity quality from spread and volume sufficiency."""
    if context.spread_bps is None and context.volume_recent is None:
        return None
    value = 1.0
    if context.spread_elevated:
        value -= 0.5
    if not context.volume_sufficient:
        value -= 0.4
    return _clamp01(value)


def _context_score(context: MarketContext) -> float:
    """Contextual fitness: volatility band and news blackout.

    Volatilidad baja penaliza porque sin rango no hay scalp; volatilidad alta
    penaliza porque el stop se ejecuta antes de que la tesis se resuelva. Es la
    misma lectura que ya usa el Confidence Engine, deliberadamente: dos partes
    del sistema que puntúan lo mismo de forma distinta acaban discutiendo.
    """
    value = 1.0
    if context.volatility is VolatilityState.LOW:
        value -= 0.4
    elif context.volatility is VolatilityState.HIGH:
        value -= 0.2
    if context.news_blackout:
        value -= 0.5
    return _clamp01(value)


def _cost_score(
    expected_cost_bps: float | None, expected_r: float | None, max_cost_bps: float
) -> float | None:
    """How much of the expected edge the entry cost eats.

    Con ``expected_r`` disponible se juzga el coste **contra lo que se espera
    ganar**, que es la comparación correcta: 8 bps son baratos para una
    operación de 3 R y carísimos para una de 0.2 R. Sin ella se cae a un techo
    absoluto — peor, pero no se inventa la R que nadie ha estimado.
    """
    if expected_cost_bps is None:
        return None
    if expected_r is not None and expected_r > 0:
        # 100 bps ≈ 1% del nocional; se toma como unidad de R de referencia
        # para poder comparar dos magnitudes que no viven en la misma escala.
        budget = expected_r * 100.0
        return _clamp01(1.0 - expected_cost_bps / budget)
    reference = max(max_cost_bps, 1e-6)
    return _clamp01(1.0 - expected_cost_bps / reference)


def _clamp01(value: float) -> float:
    """Clamp a value into ``[0, 1]``."""
    return max(0.0, min(1.0, value))
