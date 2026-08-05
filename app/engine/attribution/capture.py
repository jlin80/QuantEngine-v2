"""Captura de factores en el momento de decidir (Bloque 2).

Sin esta foto no hay atribución posible: cuando una operación se cierra, media
hora después, el order flow, el VWAP y el momentum que la acompañaron ya no
existen. Reconstruirlos entonces sería mirar otro mercado.

**Dónde vive la captura y por qué.** Se engancha al Event Bus, no al Decision
Engine. El motor de decisión está en el camino caliente: añadirle lecturas del
Feature Store le sumaría latencia a cada evaluación de cada símbolo, y la
latencia del bucle de decisión es justo lo que no se puede pagar. El precio de
esta elección es un desfase de hasta el TTL del Feature Store (1 s por defecto)
entre la decisión y la foto; queda documentado y medido en ``lag_seconds`` de
cada fila, en vez de fingir que la foto es exactamente simultánea.
"""

import logging
from collections.abc import Callable, Sequence
from typing import Any

from app.core.events.base import Event
from app.core.events.bus import EventBus
from app.core.lifecycle import Service
from app.engine.attribution.models import FactorSnapshot
from app.engine.attribution.store import FactorSnapshotStore
from app.engine.events.events import DecisionGenerated
from app.engine.feature_store import FeatureStore
from app.engine.models import Decision
from app.utils.time import utc_now

DecisionsProvider = Callable[[], Sequence[Decision]]
MLPredictionProvider = Callable[[str], float | None]

# Factores continuos que se leen del Feature Store. Son exactamente los que el
# Bloque 2 enumera y que no viajan ya en la decisión: order flow (delta, CVD,
# book pressure, imbalance), VWAP, momentum y volumen.
_FEATURES: tuple[str, ...] = (
    "delta",
    "cvd",
    "book_pressure",
    "imbalance",
    "vwap_distance_pct",
    "momentum_score",
    "volume_ratio",
    "atr_pct",
    "spread_bps",
)

# Factores continuos que ya trae la decisión: el desglose de confianza.
_BREAKDOWN: tuple[str, ...] = (
    "signal_confidence",
    "agreement",
    "data_quality",
    "liquidity",
    "volatility",
    "confirmation",
    "history",
)


class FactorCapture(Service):
    """Snapshot every decision's factor vector, off the hot path.

    Args:
        features: Feature Store (lecturas cacheadas por TTL).
        decisions_provider: Fuente de las decisiones recientes, para recuperar
            el objeto completo a partir del ``decision_id`` del evento. El
            evento no lo lleva entero a propósito: el bus transporta avisos.
        store: Persistencia append-only de las fotos.
        bus: Event Bus al que suscribirse.
        ml_prediction_provider: Predicción del modelo activo para el símbolo,
            si la capa de ML está cableada. ``None`` deja el factor ``ml`` sin
            observar, que es distinto de observarlo en cero.
    """

    def __init__(
        self,
        features: FeatureStore,
        decisions_provider: DecisionsProvider,
        store: FactorSnapshotStore,
        bus: EventBus | None = None,
        ml_prediction_provider: MLPredictionProvider | None = None,
    ) -> None:
        super().__init__("factor_capture")
        self._features = features
        self._decisions = decisions_provider
        self._store = store
        self._bus = bus
        self._ml = ml_prediction_provider
        self._captured = 0
        self._missed = 0
        self._log = logging.getLogger("app.engine.attribution.capture")

    async def on_decision(self, event: Event) -> None:
        """Capture the factor vector behind one decision.

        Nunca lanza: la atribución es observación, y una observación que tumba
        el bus del motor no vale lo que cuesta.

        Args:
            event: Aviso de decisión publicado por el Decision Engine. Llega
                tipado como ``Event`` porque esa es la firma del bus; el filtro
                por tipo lo hace la suscripción.
        """
        if not isinstance(event, DecisionGenerated):
            return
        try:
            await self._capture(event)
        except Exception:
            self._missed += 1
            self._log.exception("Factor capture failed for decision %s", event.decision_id)

    async def _capture(self, event: DecisionGenerated) -> None:
        """Build and persist the snapshot for one decision."""
        decision = self._find(event.decision_id)
        numeric: dict[str, float | None] = {}
        for name in _FEATURES:
            numeric[name] = await self._features.get(name, event.symbol)
        breakdown = decision.confidence_breakdown if decision is not None else {}
        for name in _BREAKDOWN:
            value = breakdown.get(name)
            numeric[name] = None if value is None else float(value)
        numeric["score"] = event.score
        numeric["confidence"] = event.confidence
        numeric["ml"] = None if self._ml is None else self._ml(event.symbol)

        summary: dict[str, Any] = decision.context_summary if decision is not None else {}
        sessions = summary.get("sessions") or []
        labels = {
            "strategy": event.strategy,
            "strategy_category": event.strategy_category,
            "regime": str(summary.get("regime", "unknown")),
            "volatility": str(summary.get("volatility", "normal")),
            # Una decisión puede caer en varias sesiones solapadas; se
            # concatenan en orden estable para que el bucket sea determinista.
            "session": "+".join(str(s) for s in sessions) if sessions else "none",
        }
        now = utc_now()
        if decision is not None:
            numeric["lag_seconds"] = round((now - decision.timestamp).total_seconds(), 3)
        self._store.record(
            FactorSnapshot(
                decision_id=event.decision_id,
                symbol=event.symbol,
                at=now,
                numeric=numeric,
                labels=labels,
                accepted=event.accepted,
            )
        )
        self._captured += 1

    def _find(self, decision_id: str) -> Decision | None:
        """Locate the full decision behind an event id (newest first)."""
        for decision in reversed(list(self._decisions())):
            if decision.decision_id == decision_id:
                return decision
        return None

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "captured": self._captured,
            "missed": self._missed,
            "store": self._store.status(),
        }

    async def _on_start(self) -> None:
        if self._bus is not None:
            self._bus.subscribe(self.on_decision, DecisionGenerated)

    async def _on_stop(self) -> None:
        self._store.flush()
