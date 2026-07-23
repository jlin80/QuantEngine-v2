"""Notificaciones del Research Lab por Discord (Fase 10), desacopladas por eventos.

Se suscribe al Event Bus y traduce cada hito del laboratorio (nuevo experimento,
nueva estrategia, optimización, nueva candidata, promoción, rechazo, ranking,
Shadow Mode, error) a embeds de Discord. Reutiliza el ``NotificationService`` del
proyecto. El ``ResearchLab`` no conoce a este servicio: sólo publica eventos.
"""

import logging
from collections import deque
from collections.abc import Callable
from typing import Any

from app.core.events.base import Event
from app.core.events.bus import EventBus, Subscription
from app.core.lifecycle import Service
from app.notifications.models import Notification, NotificationLevel
from app.notifications.service import NotificationService
from app.research import events as ev

_SOURCE = "research"


def _experiment_created(event: ev.ExperimentCreated) -> Notification:
    return _note(
        "🧪 Nuevo experimento",
        f"'{event.label}' (**{event.kind}**).",
        NotificationLevel.INFO,
        {"Hipótesis": event.hypothesis[:200] or "—", "ID": event.experiment_id},
    )


def _experiment_archived(event: ev.ExperimentArchived) -> Notification:
    return _note(
        "🗄️ Experimento archivado",
        f"'{event.label}' archivado (conocimiento retenido).",
        NotificationLevel.INFO,
        {"Conclusiones": event.conclusions[:200] or "—"},
    )


def _strategy_generated(event: ev.StrategyGenerated) -> Notification:
    return _note(
        "🧬 Nuevas estrategias generadas",
        f"Se generaron **{event.count}** estrategias para {event.symbol}.",
        NotificationLevel.INFO,
        {"Ejemplo": event.sample_name or "—"},
    )


def _optimization_completed(event: ev.OptimizationCompleted) -> Notification:
    fields = {"Método": event.method, "Objetivo": event.objective, "Evals": str(event.evaluations)}
    if event.best_score is not None:
        fields["Mejor score"] = f"{event.best_score:.4f}"
    return _note(
        "🎛️ Optimización finalizada", "Optimización completada.", NotificationLevel.INFO, fields
    )


def _feature_validated(event: ev.FeatureValidated) -> Notification:
    level = NotificationLevel.SUCCESS if event.valid else NotificationLevel.WARNING
    verb = "validada" if event.valid else "descartada"
    return _note(
        f"🔬 Feature {verb}",
        f"Feature **{event.feature}** {verb}.",
        level,
        {"IC": f"{event.ic:+.3f}"},
    )


def _factor_research(event: ev.FactorResearchCompleted) -> Notification:
    return _note(
        "📐 Investigación de factores",
        f"Se probaron **{event.tested}** factores; se retuvieron **{event.retained}**.",
        NotificationLevel.INFO,
        {"Mejor factor": event.top_factor or "—"},
    )


def _candidate_qualified(event: ev.CandidateQualified) -> Notification:
    fields = ev.metric_fields(event.metrics, ("profit_factor", "sharpe", "expectancy_r"))
    if event.score is not None:
        fields["Score"] = f"{event.score:.4f}"
    return _note(
        "🌟 Nueva candidata",
        f"**{event.name}** ({event.symbol}) superó el pipeline.",
        NotificationLevel.SUCCESS,
        fields,
    )


def _candidate_failed(event: ev.CandidateFailed) -> Notification:
    return _note(
        "🚫 Estrategia rechazada por el pipeline",
        f"**{event.name}** no superó la validación.",
        NotificationLevel.WARNING,
        {"Motivos": " · ".join(event.reasons[:3]) or "no cumple los mínimos"},
    )


def _shadow_report(event: ev.ShadowReportReady) -> Notification:
    level = NotificationLevel.SUCCESS if event.better else NotificationLevel.INFO
    headline = "supera" if event.better else "no supera"
    return _note(
        "👥 Informe de Shadow Mode",
        f"La challenger **{headline}** a '{event.official}'.",
        level,
        {
            "Efecto (R)": f"{event.effect_r:+.3f}",
            "p": f"{event.p_value:.3f}",
            "Significativo": "sí" if event.significant else "no",
        },
    )


def _ranking_updated(event: ev.RankingUpdated) -> Notification:
    return _note(
        "📊 Ranking actualizado",
        f"Ranking '{event.segment}' recomputado ({event.size} estrategias).",
        NotificationLevel.INFO,
        {"Líder": event.leader or "—"},
    )


def _strategy_promoted(event: ev.StrategyPromoted) -> Notification:
    return _note(
        "🚀 Estrategia promovida",
        f"**{event.name}** promovida por {event.operator}.",
        NotificationLevel.SUCCESS,
        {"Mejora": f"{event.improvement:+.3f}"},
    )


def _strategy_rejected(event: ev.StrategyRejected) -> Notification:
    return _note(
        "⛔ Promoción rechazada",
        f"**{event.name}** no fue promovida.",
        NotificationLevel.WARNING,
        {"Motivos": " · ".join(event.reasons[:3]) or "—"},
    )


def _research_failed(event: ev.ResearchFailed) -> Notification:
    return _note(
        "❌ Error en el laboratorio",
        f"Fallo en '{event.operation}'.",
        NotificationLevel.ERROR,
        {"Detalle": event.error[:512]},
    )


_BUILDERS: dict[type[Event], Callable[[Any], Notification]] = {
    ev.ExperimentCreated: _experiment_created,
    ev.ExperimentArchived: _experiment_archived,
    ev.StrategyGenerated: _strategy_generated,
    ev.OptimizationCompleted: _optimization_completed,
    ev.FeatureValidated: _feature_validated,
    ev.FactorResearchCompleted: _factor_research,
    ev.CandidateQualified: _candidate_qualified,
    ev.CandidateFailed: _candidate_failed,
    ev.ShadowReportReady: _shadow_report,
    ev.RankingUpdated: _ranking_updated,
    ev.StrategyPromoted: _strategy_promoted,
    ev.StrategyRejected: _strategy_rejected,
    ev.ResearchFailed: _research_failed,
}


class ResearchNotifier(Service):
    """Fan Research Lab events out to Discord as rich embeds.

    Args:
        notifications: Servicio de notificaciones global (canal Discord).
        bus: Event Bus del sistema.
    """

    def __init__(self, notifications: NotificationService, bus: EventBus) -> None:
        super().__init__("research_notifier")
        self._notifications = notifications
        self._bus = bus
        self._subscriptions: list[Subscription] = []
        self._sent = 0
        self._recent: deque[dict[str, str]] = deque(maxlen=50)
        self._log = logging.getLogger("app.research.notifier")

    async def _on_start(self) -> None:
        """Subscribe to every research event with a template."""
        for event_type in _BUILDERS:
            self._subscriptions.append(self._bus.subscribe(self._handle, event_type))

    async def _on_stop(self) -> None:
        """Unsubscribe from the bus."""
        for subscription in self._subscriptions:
            self._bus.unsubscribe(subscription)
        self._subscriptions.clear()

    async def _handle(self, event: Event) -> None:
        """Build and deliver the embed for a supported research event."""
        builder = _BUILDERS.get(type(event))
        if builder is None:
            return
        notification = builder(event)
        try:
            await self._notifications.notify(notification)
            self._sent += 1
            self._recent.append(
                {
                    "title": notification.title,
                    "level": notification.level.value,
                    "timestamp": notification.timestamp.isoformat(),
                }
            )
        except Exception as exc:  # el canal es best-effort; no debe propagar
            self._log.error("Fallo entregando notificación '%s': %r", notification.title, exc)

    @property
    def stats(self) -> dict[str, int]:
        """Delivery counters."""
        return {"sent": self._sent}

    def recent(self, limit: int = 20) -> list[dict[str, str]]:
        """Last delivered notifications (newest last)."""
        return list(self._recent)[-limit:]


def _note(
    title: str, message: str, level: NotificationLevel, fields: dict[str, str]
) -> Notification:
    """Build a notification with the research source tag."""
    return Notification(title=title, message=message, level=level, fields=fields, source=_SOURCE)
