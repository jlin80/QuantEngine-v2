"""Modelos del pronóstico de régimen (Bloque 4).

El detector de la Fase 3 responde *en qué régimen estamos*. Esto responde *qué
va a pasar a continuación*, que es una pregunta distinta y mucho más frágil: la
primera se observa, la segunda se apuesta. De ahí que todo aquí lleve muestra,
confianza y validación pegadas al número — un pronóstico sin su historial de
aciertos es una opinión con decimales.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.time import utc_now

# Los cinco desenlaces que pide el bloque. Son exhaustivos y excluyentes por
# construcción: cada ventana futura se clasifica en uno y sólo uno, o el
# reparto de probabilidad no sumaría 1 y dejaría de ser un pronóstico.
OUTCOMES: tuple[str, ...] = (
    "continuation",
    "reversal",
    "breakout",
    "compression",
    "expansion",
)


@dataclass(frozen=True, kw_only=True, slots=True)
class RegimeForecast:
    """Probabilidad de cada desenlace para el horizonte configurado.

    Attributes:
        symbol: Activo.
        at: Momento del pronóstico.
        condition: Estado desde el que se pronostica (régimen + volatilidad).
            Es la clave de la frecuencia condicional: sin ella el pronóstico
            sería la distribución global, que no informa de nada.
        horizon_bars: Velas hacia adelante que cubre.
        probabilities: Reparto por desenlace (suma 1.0).
        confidence: 0-1, crece con la muestra de la condición. **No** mide si el
            pronóstico es bueno — eso lo mide el Brier score de la validación.
        sample: Observaciones de esta condición que sostienen el reparto.
        observable: Si había muestra para pronosticar.
        reason: Por qué no, si no lo era.
    """

    symbol: str
    at: datetime = field(default_factory=utc_now)
    condition: str = ""
    horizon_bars: int = 0
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    sample: int = 0
    observable: bool = False
    reason: str = ""

    @property
    def most_likely(self) -> str:
        """Desenlace de mayor probabilidad ("" si no es observable)."""
        if not self.probabilities:
            return ""
        return max(self.probabilities.items(), key=lambda item: item[1])[0]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "symbol": self.symbol,
            "at": self.at.isoformat(),
            "condition": self.condition,
            "horizon_bars": self.horizon_bars,
            "probabilities": {k: round(v, 4) for k, v in self.probabilities.items()},
            "most_likely": self.most_likely,
            "confidence": round(self.confidence, 4),
            "sample": self.sample,
            "observable": self.observable,
            "reason": self.reason,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class ForecastScore:
    """Calidad medida de los pronósticos ya resueltos.

    Attributes:
        resolved: Pronósticos con desenlace ya conocido.
        brier: Brier score multiclase (0 = perfecto, 2 = máximo posible).
        baseline_brier: El mismo score del pronóstico trivial —la distribución
            global de desenlaces—. Es la única referencia que dice si el modelo
            aporta algo: un Brier de 0.6 no significa nada hasta saber que el
            trivial saca 0.7.
        skill: ``1 - brier/baseline``. Positivo = mejor que el trivial.
        hit_rate: Fracción de veces que el desenlace más probable acertó.
    """

    resolved: int = 0
    brier: float | None = None
    baseline_brier: float | None = None
    skill: float | None = None
    hit_rate: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "resolved": self.resolved,
            "brier": _round(self.brier),
            "baseline_brier": _round(self.baseline_brier),
            "skill": _round(self.skill),
            "hit_rate": _round(self.hit_rate),
        }


def _round(value: float | None, digits: int = 4) -> float | None:
    """Round without turning ``None`` into a number."""
    return None if value is None else round(value, digits)
