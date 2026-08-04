"""Decision Engine: el ÚNICO módulo autorizado a decidir.

Recibe las señales activas, evalúa contexto, aplica filtros, construye el
consenso, separa score de confianza y emite UNA decisión estructurada y
siempre explicable. No ejecuta órdenes: eso pertenece a fases futuras.
"""

import logging
from typing import Any

from app.config.settings import QuantConsensusSettings
from app.core.events.bus import EventBus
from app.core.exceptions import EventBusError
from app.engine.confidence import ConfidenceEngine
from app.engine.consensus import ConsensusEngine
from app.engine.events import ConsensusReached, DecisionGenerated, FilterTriggered
from app.engine.filters import FilterChain
from app.engine.market_context import MarketContextEngine
from app.engine.models import (
    Decision,
    DecisionAction,
    Direction,
    Regime,
    SignalStatus,
)
from app.engine.signal_engine import SignalEngine
from app.engine.state_manager import HistoryWriter, SignalHistoryStore
from app.utils.time import utc_now


class DecisionEngine:
    """Turns active signals into one explainable decision per symbol.

    Args:
        signals: Signal Engine (señales activas).
        context_engine: Market Context Engine.
        consensus: Motor de consenso.
        confidence: Confidence Engine.
        filters: Cadena de filtros.
        history: Historial en memoria.
        settings: Umbrales de decisión.
        bus: Event Bus (``None`` en tests puros).
        writer: Persistencia del historial (opcional).
    """

    def __init__(
        self,
        signals: SignalEngine,
        context_engine: MarketContextEngine,
        consensus: ConsensusEngine,
        confidence: ConfidenceEngine,
        filters: FilterChain,
        history: SignalHistoryStore,
        settings: QuantConsensusSettings,
        bus: EventBus | None = None,
        writer: HistoryWriter | None = None,
    ) -> None:
        self._signals = signals
        self._context_engine = context_engine
        self._consensus = consensus
        self._confidence = confidence
        self._filters = filters
        self._history = history
        self._settings = settings
        self._bus = bus
        self._writer = writer
        self._log = logging.getLogger("app.engine.decision")

    async def evaluate(self, symbol: str) -> Decision:
        """Produce the single decision for a symbol right now.

        Args:
            symbol: Símbolo a decidir.

        Returns:
            Decisión estructurada (aceptada o no), siempre con explicación.
        """
        symbol = symbol.upper()
        now = utc_now()
        active = self._signals.active_signals(symbol)
        context = await self._context_engine.build(symbol)
        regime = context.regime.primary.value if context.regime else Regime.UNKNOWN.value

        if not active:
            decision = Decision(
                symbol=symbol,
                timestamp=now,
                action=DecisionAction.STAND_ASIDE,
                accepted=False,
                score=0.0,
                confidence=0.0,
                agreement=0.0,
                explanation=("No hay señales activas para el símbolo.",),
                regime=regime,
                context_summary=context.summary(),
            )
            await self._finish(decision)
            return decision

        consensus = self._consensus.build(symbol, active, context)
        confidence, breakdown = self._confidence.compute(active, consensus, context)
        filter_results = self._filters.evaluate(context, consensus)
        blocking = [r for r in filter_results if not r.passed]
        for result in blocking:
            await self._publish(
                FilterTriggered(
                    source="decision_engine",
                    filter_name=result.name,
                    symbol=symbol,
                    reason=result.reason,
                )
            )
        await self._publish(
            ConsensusReached(
                source="decision_engine",
                symbol=symbol,
                method=consensus.method,
                direction=consensus.direction.value,
                score=consensus.score,
                agreement=consensus.agreement,
                participants=len(consensus.participants),
            )
        )

        explanation: list[str] = [
            f"Consenso ({consensus.method}): {consensus.direction.value} "
            f"con score global {consensus.score:.1f} y acuerdo "
            f"{consensus.agreement * 100:.0f}%.",
            f"Confianza {confidence * 100:.0f}% "
            f"(datos {breakdown.get('data_quality', 0):.2f}, "
            f"liquidez {breakdown.get('liquidity', 0):.2f}, "
            f"volatilidad {breakdown.get('volatility', 0):.2f}).",
            f"Régimen: {regime}. Sesiones: {', '.join(context.sessions) or 'ninguna'}.",
            f"Señales consideradas: {len(active)} "
            f"({', '.join(sorted({s.strategy_name for s in active}))}).",
        ]
        if self._signals.detect_conflict(symbol):
            explanation.append("Conflicto de direcciones entre estrategias detectado.")

        rejections: list[str] = []
        if consensus.direction is Direction.NEUTRAL:
            rejections.append("El consenso no produjo una dirección dominante.")
        if len(active) < self._settings.min_signals:
            rejections.append(
                f"Señales insuficientes: {len(active)} < mínimo " f"{self._settings.min_signals}."
            )
        if consensus.score < self._settings.min_score:
            rejections.append(
                f"Score global {consensus.score:.1f} < mínimo {self._settings.min_score:.0f}."
            )
        if confidence < self._settings.min_confidence:
            rejections.append(
                f"Confianza {confidence * 100:.0f}% < mínima "
                f"{self._settings.min_confidence * 100:.0f}%."
            )
        if consensus.agreement < self._settings.min_agreement:
            rejections.append(
                f"Acuerdo {consensus.agreement * 100:.0f}% < mínimo "
                f"{self._settings.min_agreement * 100:.0f}%."
            )
        for result in blocking:
            rejections.append(f"Filtro '{result.name}': {result.reason}.")

        accepted = not rejections
        if accepted:
            action = (
                DecisionAction.OPEN_LONG
                if consensus.direction is Direction.LONG
                else DecisionAction.OPEN_SHORT
            )
            explanation.append("Todos los umbrales y filtros superados: decisión aceptada.")
        else:
            action = DecisionAction.STAND_ASIDE
            explanation.append("No se opera porque:")
            explanation.extend(f"  - {reason}" for reason in rejections)

        decision = Decision(
            symbol=symbol,
            timestamp=now,
            action=action,
            accepted=accepted,
            score=consensus.score,
            confidence=confidence,
            agreement=consensus.agreement,
            consensus=consensus,
            confidence_breakdown=breakdown,
            signals_considered=tuple(s.signal_id for s in active),
            filters_blocking=tuple(r.name for r in blocking),
            explanation=tuple(explanation),
            regime=regime,
            # La categoría la declara cada estrategia y viaja en la señal; se
            # arrastra a la decisión para que la ejecución pueda resolver el
            # holding mínimo por categoría sin importar `app.strategies`
            # (el motor de ejecución no conoce los plugins, sólo eventos).
            strategy_categories={
                s.strategy_name: str(s.metadata.get("category", ""))
                for s in active
                if s.metadata.get("category")
            },
            context_summary=context.summary(),
        )
        status = SignalStatus.ACCEPTED if accepted else SignalStatus.REJECTED
        reason_summary = tuple(rejections) if rejections else ("decisión aceptada",)
        self._signals.consume(symbol, status, reason_summary)
        await self._finish(decision)
        return decision

    async def _finish(self, decision: Decision) -> None:
        """Record, persist and announce a decision."""
        self._history.record_decision(decision)
        if self._writer is not None:
            self._writer.add_decision(decision)
        await self._publish(
            DecisionGenerated(
                source="decision_engine",
                decision_id=decision.decision_id,
                symbol=decision.symbol,
                action=decision.action.value,
                accepted=decision.accepted,
                score=decision.score,
                confidence=decision.confidence,
                summary=decision.explanation[0] if decision.explanation else "",
                strategy=decision.primary_strategy,
                strategy_category=decision.primary_category,
                signal_ids=decision.signals_considered,
            )
        )
        self._log.info(
            "Decision %s %s (accepted=%s, score=%.1f, conf=%.2f)",
            decision.symbol,
            decision.action.value,
            decision.accepted,
            decision.score,
            decision.confidence,
        )

    async def _publish(self, event: Any) -> None:
        """Publish tolerating a stopped/saturated bus."""
        if self._bus is None:
            return
        try:
            await self._bus.publish(event)
        except EventBusError as exc:
            self._log.warning("Event publish failed: %s", exc)
