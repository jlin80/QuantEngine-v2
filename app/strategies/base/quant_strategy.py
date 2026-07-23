"""QuantStrategy: la base común de toda la biblioteca de Fase 4.

Aporta el pipeline completo alrededor del análisis concreto de cada
estrategia (que solo implementa :meth:`evaluate`):

    pre-chequeos → evaluate() → confirmaciones → score (5 componentes con
    pesos configurables) → confianza (factores con pesos configurables) →
    señal estructurada con Entry/SL/TP, razones, advertencias y metadata.

Ninguna constante vive en el código: TODO sale de ``parameters`` (base +
``default_parameters`` de la subclase + configuración externa), listo para
el optimizador de fases futuras.
"""

import abc
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, ClassVar

from app.engine.interfaces.strategy import AnalysisContext, BaseStrategy
from app.engine.models import Direction, EntryZone, StrategySignal, VolatilityState
from app.market.models import Candle
from app.strategies.confirmation import ConfirmationEngine, ConfirmationResult
from app.strategies.filters import precheck
from app.strategies.shared.api import calculate_regime_score
from app.strategies.utils import clamp01


@dataclass(kw_only=True, slots=True)
class Assessment:
    """Lo que una estrategia concreta devuelve desde :meth:`evaluate`.

    Attributes:
        direction: Dirección de la oportunidad (NEUTRAL = no hay señal).
        quality: Calidad del setup (0-1): qué tan limpio es el patrón.
        strength: Fortaleza (0-1): magnitud/ímpetu del disparador.
        context_fit: Encaje con el contexto (0-1): régimen/sesión adecuados.
        probability: Probabilidad estimada del escenario (0-1).
        risk: Riesgo del setup (0-1, más = peor).
        reasons: Razones estructuradas (obligatorias para emitir señal).
        warnings: Matices/avisos propios del análisis.
        entry: Zona de entrada propuesta.
        stop_loss: Stop propuesto.
        take_profit: Objetivo propuesto.
        confirmations: Confirmaciones a evaluar (None = las del parámetro).
        metadata: Datos extra JSON-safe para la señal.
    """

    direction: Direction
    quality: float = 0.5
    strength: float = 0.5
    context_fit: float = 0.5
    probability: float = 0.5
    risk: float = 0.5
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    entry: EntryZone | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    confirmations: tuple[str, ...] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class QuantStrategy(BaseStrategy):
    """Base class of the Phase-4 strategy library.

    Las subclases declaran ``category``/``preferred_regimes``/
    ``preferred_volatility`` y sus ``default_parameters``, e implementan
    únicamente :meth:`evaluate`. Todo lo demás (score, confianza,
    confirmaciones, señal, explicación) lo resuelve esta base de forma
    homogénea y configurable.
    """

    category: ClassVar[str] = "general"
    preferred_regimes: ClassVar[tuple[str, ...]] = ()
    preferred_volatility: ClassVar[tuple[str, ...]] = ("normal", "high")

    core_parameters: ClassVar[dict[str, Any]] = {
        "lookback": 120,
        "min_candles": 40,
        "atr_period": 14,
        "signal_ttl_seconds": 300.0,
        "min_signal_score": 55.0,
        "min_data_quality": 0.0,
        "max_spread_bps": None,
        "confirmations": [],
        "score_weights": {
            "quality": 0.25,
            "strength": 0.20,
            "context": 0.20,
            "probability": 0.20,
            "risk": 0.15,
        },
        "confidence_weights": {
            "volume": 0.15,
            "volatility": 0.15,
            "liquidity": 0.15,
            "regime": 0.20,
            "confirmations": 0.25,
            "data_quality": 0.10,
        },
    }

    def __init__(self, parameters: dict[str, Any] | None = None) -> None:
        merged = {**self.core_parameters, **self.default_parameters, **(parameters or {})}
        super().__init__(merged)
        self._skip_reason: str = ""

    def explain(self) -> str:
        """Explicación de la última evaluación (incluye por qué se saltó)."""
        if self._last_signal is None and self._skip_reason:
            return self._skip_reason
        return super().explain()

    def _note(self, reason: str) -> None:
        """Registra la razón específica por la que no se emitió señal."""
        self._skip_reason = f"[{self.name}] {reason}"

    # ------------------------------------------------------------------
    # Acceso tipado a parámetros
    # ------------------------------------------------------------------

    def fparam(self, name: str) -> float:
        """Parámetro numérico como float."""
        value = self.parameters[name]
        return float(value)

    def iparam(self, name: str) -> int:
        """Parámetro numérico como int."""
        value = self.parameters[name]
        return int(value)

    # ------------------------------------------------------------------
    # Pipeline (plantilla)
    # ------------------------------------------------------------------

    async def analyze(self, ctx: AnalysisContext) -> StrategySignal | None:
        """Full analysis pipeline (las subclases NO tocan este método).

        Args:
            ctx: Contexto de análisis provisto por el Strategy Engine.

        Returns:
            Señal estructurada o ``None`` (con explicación registrada).
        """
        candles = ctx.market.get_candles(ctx.symbol, self.timeframe, self.iparam("lookback"))
        raw_max_spread = self.parameters.get("max_spread_bps")
        blocked = precheck(
            ctx,
            candles,
            min_candles=self.iparam("min_candles"),
            min_data_quality=self.fparam("min_data_quality"),
            max_spread_bps=None if raw_max_spread is None else float(raw_max_spread),
        )
        if blocked is not None:
            self._note(blocked)
            return None

        assessment = await self.evaluate(ctx, candles)
        if assessment is None or assessment.direction is Direction.NEUTRAL:
            self._note(f"sin oportunidad ({ctx.trigger})")
            return None

        score = self._score(assessment)
        if score < self.fparam("min_signal_score"):
            self._note(f"setup débil: score {score:.1f} < {self.fparam('min_signal_score'):.0f}")
            return None
        self._skip_reason = ""

        requested = (
            assessment.confirmations
            if assessment.confirmations is not None
            else tuple(str(n) for n in self.parameters.get("confirmations", []))
        )
        confirmations = await ConfirmationEngine.evaluate(ctx, requested, assessment.direction)
        passed = [c for c in confirmations if c.passed]
        missing = [c for c in confirmations if not c.passed]
        confidence = self._confidence(ctx, confirmations)

        reasons = tuple(assessment.reasons) + tuple(
            f"Confirmado mediante {c.name}: {c.detail}." for c in passed
        )
        warnings = tuple(assessment.warnings) + tuple(
            f"Falta confirmación de {c.name}: {c.detail}." for c in missing
        )
        risk_reward = self._risk_reward(assessment)

        return StrategySignal(
            strategy_name=self.name,
            strategy_version=self.version,
            symbol=ctx.symbol,
            timestamp=ctx.fired_at,
            direction=assessment.direction,
            confidence=confidence,
            score=score,
            entry_zone=assessment.entry,
            stop_loss=assessment.stop_loss,
            take_profit=assessment.take_profit,
            risk_reward=risk_reward,
            market_context=ctx.context.summary(),
            reasons=reasons,
            warnings=warnings,
            required_confirmation=bool(missing),
            expiration=ctx.fired_at + timedelta(seconds=self.fparam("signal_ttl_seconds")),
            metadata={
                "category": self.category,
                "trigger": ctx.trigger,
                "score_components": {
                    "quality": round(assessment.quality, 4),
                    "strength": round(assessment.strength, 4),
                    "context": round(assessment.context_fit, 4),
                    "probability": round(assessment.probability, 4),
                    "risk": round(assessment.risk, 4),
                },
                "confirmations": [
                    {"name": c.name, "passed": c.passed, "detail": c.detail} for c in confirmations
                ],
                **assessment.metadata,
            },
        )

    @abc.abstractmethod
    async def evaluate(self, ctx: AnalysisContext, candles: Sequence[Candle]) -> Assessment | None:
        """Core analysis of the concrete strategy.

        Args:
            ctx: Contexto de análisis (features, contexto, detecciones).
            candles: Velas ya obtenidas en el timeframe de la estrategia.

        Returns:
            Un :class:`Assessment` o ``None`` si no hay oportunidad.
        """

    # ------------------------------------------------------------------
    # Score y confianza (pesos 100 % configurables)
    # ------------------------------------------------------------------

    def _score(self, assessment: Assessment) -> float:
        """Score 0-100: promedio ponderado de los 5 componentes."""
        weights = dict(self.parameters["score_weights"])
        components = {
            "quality": clamp01(assessment.quality),
            "strength": clamp01(assessment.strength),
            "context": clamp01(assessment.context_fit),
            "probability": clamp01(assessment.probability),
            "risk": clamp01(1.0 - assessment.risk),
        }
        total = sum(float(w) for w in weights.values())
        if total <= 0:
            return 0.0
        value = sum(components[name] * float(weights[name]) for name in components) / total
        return round(value * 100.0, 2)

    def _confidence(
        self, ctx: AnalysisContext, confirmations: Sequence[ConfirmationResult]
    ) -> float:
        """Confianza 0-1: volumen, volatilidad, liquidez, régimen, confirmaciones."""
        weights = dict(self.parameters["confidence_weights"])
        volatility = ctx.context.volatility
        factors = {
            "volume": 1.0 if ctx.context.volume_sufficient else 0.3,
            "volatility": 1.0 if volatility.value in self.preferred_volatility else 0.6,
            "liquidity": 0.5 if ctx.context.spread_elevated else 1.0,
            "regime": calculate_regime_score(ctx.context, self.preferred_regimes),
            "confirmations": (
                sum(1 for c in confirmations if c.passed) / len(confirmations)
                if confirmations
                else 0.75
            ),
            "data_quality": clamp01(ctx.context.data_quality),
        }
        total = sum(float(w) for w in weights.values())
        if total <= 0:
            return 0.0
        value = sum(factors[name] * float(weights[name]) for name in factors) / total
        return round(clamp01(value), 4)

    @staticmethod
    def _risk_reward(assessment: Assessment) -> float | None:
        """R:R implícito en los niveles propuestos."""
        if assessment.entry is None or assessment.stop_loss is None:
            return None
        if assessment.take_profit is None:
            return None
        entry = (assessment.entry.low + assessment.entry.high) / 2.0
        risk = abs(entry - assessment.stop_loss)
        if risk <= 0:
            return None
        return round(abs(assessment.take_profit - entry) / risk, 2)

    # ------------------------------------------------------------------
    # Helpers para subclases
    # ------------------------------------------------------------------

    async def atr_or_none(self, ctx: AnalysisContext) -> float | None:
        """ATR del timeframe de la estrategia (cacheado en el Feature Store)."""
        return await ctx.features.get(
            "atr",
            ctx.symbol,
            timeframe=self.timeframe.value,
            period=self.iparam("atr_period"),
        )

    def volatility_risk(self, ctx: AnalysisContext, *, base: float = 0.3) -> float:
        """Riesgo base ajustado por volatilidad fuera de la preferida."""
        if ctx.context.volatility.value in self.preferred_volatility:
            return base
        if ctx.context.volatility is VolatilityState.HIGH:
            return clamp01(base + 0.3)
        return clamp01(base + 0.2)
