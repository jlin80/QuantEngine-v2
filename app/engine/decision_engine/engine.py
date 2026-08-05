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
    ConsensusResult,
    Decision,
    DecisionAction,
    Direction,
    FilterResult,
    Regime,
    SignalStatus,
)
from app.engine.rejections import GateResult, RejectionRecord, RejectionStore
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
        rejections: RejectionStore | None = None,
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
        # Registro estructurado de rechazos (Bloque 14). Se cablea aqui y no en
        # el bus porque los resultados de cada filtro y el deficit de cada
        # umbral solo existen en este punto: el evento de decision no los lleva,
        # y reconstruirlos desde fuera seria adivinarlos.
        self._rejections = rejections
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
            # Sin senales no hay oportunidad que rechazar: se cuenta, no se
            # guarda fila (ver `note_no_opportunity`).
            if self._rejections is not None:
                self._rejections.note_no_opportunity()
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
        # Bloque 14: el desglose se arma aqui, con los mismos numeros que
        # produjeron la decision. Se registra tanto si se acepto como si no —
        # sin los aceptados no hay denominador, y "este filtro bloqueo 40 veces"
        # no significa nada sin saber sobre cuantas oportunidades.
        if self._rejections is not None:
            self._rejections.record(
                RejectionRecord(
                    decision_id=decision.decision_id,
                    symbol=symbol,
                    at=now,
                    initial_score=consensus.score,
                    confidence=confidence,
                    direction=consensus.direction.value,
                    gates=self._gates(consensus, confidence, len(active), filter_results),
                    evidence={
                        "context": context.summary(),
                        "confidence_breakdown": breakdown,
                        "strategies": sorted({s.strategy_name for s in active}),
                        "accepted": accepted,
                    },
                )
            )
        status = SignalStatus.ACCEPTED if accepted else SignalStatus.REJECTED
        reason_summary = tuple(rejections) if rejections else ("decisión aceptada",)
        self._signals.consume(symbol, status, reason_summary)
        await self._finish(decision)
        return decision

    def _gates(
        self,
        consensus: ConsensusResult,
        confidence: float,
        active_signals: int,
        filter_results: list[FilterResult],
    ) -> tuple[GateResult, ...]:
        """Build the structured breakdown of every threshold and filter.

        Los umbrales llevan valor y minimo —de ahi sale un deficit medible—; los
        filtros son binarios y se registran como tales. Modelar un filtro como
        si aplicara una penalizacion parcial seria inventarse una aritmetica que
        este motor no tiene.
        """
        settings = self._settings
        gates: list[GateResult] = [
            GateResult(
                name="direction",
                kind="filter",
                passed=consensus.direction is not Direction.NEUTRAL,
                reason=(
                    ""
                    if consensus.direction is not Direction.NEUTRAL
                    else "el consenso no produjo una direccion dominante"
                ),
            ),
            GateResult(
                name="min_signals",
                kind="threshold",
                passed=active_signals >= settings.min_signals,
                value=float(active_signals),
                required=float(settings.min_signals),
                reason=(
                    ""
                    if active_signals >= settings.min_signals
                    else f"senales {active_signals} < minimo {settings.min_signals}"
                ),
            ),
            GateResult(
                name="min_score",
                kind="threshold",
                passed=consensus.score >= settings.min_score,
                value=consensus.score,
                required=settings.min_score,
                reason=(
                    ""
                    if consensus.score >= settings.min_score
                    else f"score {consensus.score:.1f} < minimo {settings.min_score:.0f}"
                ),
            ),
            GateResult(
                name="min_confidence",
                kind="threshold",
                passed=confidence >= settings.min_confidence,
                value=confidence,
                required=settings.min_confidence,
                reason=(
                    ""
                    if confidence >= settings.min_confidence
                    else f"confianza {confidence:.2f} < minima {settings.min_confidence:.2f}"
                ),
            ),
            GateResult(
                name="min_agreement",
                kind="threshold",
                passed=consensus.agreement >= settings.min_agreement,
                value=consensus.agreement,
                required=settings.min_agreement,
                reason=(
                    ""
                    if consensus.agreement >= settings.min_agreement
                    else f"acuerdo {consensus.agreement:.2f} < minimo {settings.min_agreement:.2f}"
                ),
            ),
        ]
        gates.extend(
            GateResult(
                name=result.name,
                kind="filter",
                passed=result.passed,
                reason=result.reason,
            )
            for result in filter_results
        )
        return tuple(gates)

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
