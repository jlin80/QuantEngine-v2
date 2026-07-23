"""Contrato base de toda estrategia (framework de plugins).

Filosofía: una estrategia no compra ni vende. Analiza el mercado y responde
"según mis reglas existe (o no) una oportunidad, con esta confianza y por
estas razones". La decisión es del Decision Engine.
"""

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from app.core.events.base import Event
from app.engine.models import Cadence, CadenceKind, MarketContext, StrategySignal
from app.market.models import Timeframe

if TYPE_CHECKING:
    from app.engine.feature_store import FeatureStore
    from app.market.services import MarketDataService


@dataclass(kw_only=True, slots=True)
class AnalysisContext:
    """Todo lo que una estrategia recibe para analizar (y nada más).

    Attributes:
        symbol: Símbolo a evaluar.
        fired_at: Momento del disparo (UTC).
        trigger: Qué disparó la evaluación (``tick``/``candle:1m``/``timer``).
        market: API de datos agnóstica del broker (solo lectura).
        features: Feature Store compartido (cálculo único por variable).
        context: Contexto de mercado ya calculado.
        payload: Datos del disparador (p. ej. la vela cerrada), JSON-safe.
        detections: Eventos de detección que la estrategia acumula durante el
            análisis (FVG, order blocks, liquidez...). El Strategy Engine los
            publica al bus después de la evaluación — la estrategia nunca
            toca el bus directamente.
    """

    symbol: str
    fired_at: datetime
    trigger: str
    market: "MarketDataService"
    features: "FeatureStore"
    context: MarketContext
    payload: dict[str, Any] = field(default_factory=dict)
    detections: list[Event] = field(default_factory=list)


class BaseStrategy(abc.ABC):
    """Base class every pluggable strategy must inherit.

    Attributes de clase (los define cada estrategia):
        name: Identificador único.
        version: Versión semántica de la lógica.
        symbols: Símbolos que analiza (vacío = todos los suscritos).
        timeframe: Timeframe principal de análisis.
        cadence: Frecuencia de ejecución.
        default_parameters: Parámetros por defecto (sobreescribibles por config).
    """

    name: str = "unnamed"
    version: str = "1.0"
    symbols: tuple[str, ...] = ()
    timeframe: Timeframe = Timeframe.M1
    cadence: Cadence = Cadence(kind=CadenceKind.EVERY_CANDLE, timeframe=Timeframe.M1)
    default_parameters: ClassVar[dict[str, Any]] = {}

    def __init__(self, parameters: dict[str, Any] | None = None) -> None:
        self.parameters: dict[str, Any] = {**self.default_parameters, **(parameters or {})}
        self._last_signal: StrategySignal | None = None
        self._last_explanation: str = "Sin evaluaciones todavía."

    async def initialize(self, features: "FeatureStore") -> None:  # noqa: B027
        """One-time setup hook (registrar features propias, precargar...).

        Deliberadamente no abstracto: la mayoría de estrategias no necesita
        inicialización y no debe verse obligada a implementarlo.

        Args:
            features: Feature Store compartido.
        """

    @abc.abstractmethod
    async def analyze(self, ctx: AnalysisContext) -> StrategySignal | None:
        """Evaluate the market and return a structured signal (or nothing).

        Args:
            ctx: Contexto de análisis (datos, features, contexto de mercado).

        Returns:
            Una :class:`StrategySignal` completa, o ``None`` si no hay
            oportunidad según las reglas de la estrategia.
        """

    def validate(self, signal: StrategySignal) -> list[str]:
        """Self-check of an emitted signal; empty list = valid.

        El Signal Engine aplica además su propia validación estructural.

        Args:
            signal: Señal recién producida.

        Returns:
            Problemas detectados (vacío si es válida).
        """
        problems: list[str] = []
        if not signal.reasons:
            problems.append("la señal no incluye razones (explicabilidad obligatoria)")
        if not 0.0 <= signal.confidence <= 1.0:
            problems.append(f"confidence fuera de rango: {signal.confidence}")
        if not 0.0 <= signal.score <= 100.0:
            problems.append(f"score fuera de rango: {signal.score}")
        return problems

    def score(self, signal: StrategySignal) -> float:
        """Score 0-100 for a signal (default: el que trae la señal).

        Args:
            signal: Señal a puntuar.

        Returns:
            Puntuación en 0-100.
        """
        return max(0.0, min(100.0, signal.score))

    def explain(self) -> str:
        """Explain the latest evaluation in plain language."""
        if self._last_signal is not None:
            reasons = "; ".join(self._last_signal.reasons)
            return (
                f"[{self.name} v{self.version}] {self._last_signal.symbol} "
                f"{self._last_signal.direction.value} score={self._last_signal.score:.0f} "
                f"confianza={self._last_signal.confidence:.2f}: {reasons}"
            )
        return self._last_explanation

    def record_evaluation(self, signal: StrategySignal | None, note: str | None = None) -> None:
        """Store the latest evaluation for :meth:`explain` (lo llama el engine).

        Args:
            signal: Señal producida (``None`` si no hubo oportunidad).
            note: Explicación cuando no hay señal.
        """
        self._last_signal = signal
        if signal is None and note:
            self._last_explanation = f"[{self.name}] {note}"
