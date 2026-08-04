"""Notificaciones del ML por Discord (Fase 7), desacopladas por eventos.

Se suscribe al Event Bus y traduce cada hito del aprendizaje continuo
(entrenamiento iniciado/finalizado, modelo aprobado/rechazado/activado, deriva,
degradación, récord de rendimiento, cambio de pesos, error) a embeds de Discord.
Reutiliza el único canal del proyecto: el ``NotificationService`` de la Fase 1.
El MLEngine no conoce a este servicio: sólo publica eventos.
"""

import logging
from collections import deque
from collections.abc import Callable
from typing import Any

from app.core.events.base import Event
from app.core.events.bus import EventBus, Subscription
from app.core.lifecycle import Service
from app.ml import events as ev
from app.notifications.models import Notification, NotificationLevel
from app.notifications.service import NotificationService

_SOURCE = "ml"


def _training_started(event: ev.ModelTrainingStarted) -> Notification:
    return _note(
        "🧠 Entrenamiento iniciado",
        f"Comienza el entrenamiento (**{event.model_type}**).",
        NotificationLevel.INFO,
        {"Muestras": str(event.dataset_size), "Etiqueta": event.label},
    )


def _training_finished(event: ev.ModelTrainingFinished) -> Notification:
    return _note(
        "🧠 Entrenamiento finalizado",
        f"Modelo **{event.model_type}** entrenado (`{event.model_id}`).",
        NotificationLevel.INFO,
        _metric_fields(event.metrics),
    )


def _model_approved(event: ev.ModelApproved) -> Notification:
    return _note(
        "✅ Nuevo modelo aprobado",
        f"**{event.model_type}** superó la validación (`{event.model_id}`).",
        NotificationLevel.SUCCESS,
        {**_metric_fields(event.metrics), "Motivos": " · ".join(event.reasons[:3]) or "—"},
    )


def _model_rejected(event: ev.ModelRejected) -> Notification:
    return _note(
        "⚠️ Modelo rechazado",
        f"**{event.model_type}** no superó la validación (`{event.model_id}`).",
        NotificationLevel.WARNING,
        {"Motivos": " · ".join(event.reasons[:4]) or "no cumple los mínimos"},
    )


def _model_activated(event: ev.ModelActivated) -> Notification:
    return _note(
        "🔀 Modelo activado",
        f"**{event.model_type}** es el modelo asesor activo (`{event.model_id}`).",
        NotificationLevel.SUCCESS,
        {"Anterior": event.previous_id or "—"},
    )


def _model_rolled_back(event: ev.ModelRolledBack) -> Notification:
    return _note(
        "↩️ Rollback de modelo",
        f"Se restauró el modelo `{event.restored_id}`.",
        NotificationLevel.WARNING,
        {"Desactivado": event.model_id},
    )


def _drift_detected(event: ev.DriftDetected) -> Notification:
    return _note(
        "📉 Deriva detectada",
        f"Deriva de tipo **{event.kind}**: {event.detail or event.metric}.",
        NotificationLevel.WARNING,
        {
            "Métrica": event.metric,
            "Valor": f"{event.value:.3f}",
            "Umbral": f"{event.threshold:.3f}",
            "Acción": event.action,
        },
    )


def _model_degraded(event: ev.ModelDegraded) -> Notification:
    return _note(
        "📉 Modelo degradado",
        f"El modelo `{event.model_id}` cayó por debajo de su línea base.",
        NotificationLevel.WARNING,
        {
            "Métrica": event.metric,
            "Base": f"{event.baseline:.3f}",
            "Actual": f"{event.current:.3f}",
        },
    )


def _record_achieved(event: ev.PerformanceRecordAchieved) -> Notification:
    return _note(
        "🏆 Nuevo récord de rendimiento",
        f"Nuevo máximo de **{event.metric}** ({event.scope}).",
        NotificationLevel.SUCCESS,
        {"Valor": f"{event.value:.4f}"},
    )


def _weights_updated(event: ev.StrategyWeightsUpdated) -> Notification:
    top = sorted(event.weights.items(), key=lambda kv: kv[1], reverse=True)[:6]
    return _note(
        "⚖️ Pesos de estrategias actualizados",
        event.reason or "Ajuste dinámico de pesos.",
        NotificationLevel.INFO,
        {name: f"{weight:.2f}" for name, weight in top},
    )


def _meta_decision(event: ev.MetaStrategyDecision) -> Notification:
    return _note(
        "🧭 Decisión del Meta Strategy Manager",
        f"'{event.strategy}': **{event.action}**. {event.detail}",
        NotificationLevel.INFO,
        _metric_fields(event.metrics),
    )


def _training_failed(event: ev.ModelTrainingFailed) -> Notification:
    return _note(
        "❌ Error durante el entrenamiento",
        f"Fallo entrenando **{event.model_type}**.",
        NotificationLevel.ERROR,
        {"Detalle": event.error[:512]},
    )


def _requires_retraining(event: ev.ModelRequiresRetraining) -> Notification:
    """Aviso de que el modelo activo dejó de ser representativo.

    El aviso dice **qué** grupo de reglas cambió: uno que no señala la causa se
    acaba ignorando. Y deja claro que no se ha tocado nada.
    """
    return _note(
        "🔁 El modelo activo requiere reentrenamiento",
        event.detail,
        NotificationLevel.WARNING,
        {
            "Modelo": f"v{event.model_version}" if event.model_version else "—",
            "Motivo": event.reason,
            "Reglas cambiadas": ", ".join(event.changed) or "—",
            "Acción tomada": "ninguna (el ML asesora, no decide)",
        },
    )


_BUILDERS: dict[type[Event], Callable[[Any], Notification]] = {
    ev.ModelTrainingStarted: _training_started,
    ev.ModelTrainingFinished: _training_finished,
    ev.ModelApproved: _model_approved,
    ev.ModelRejected: _model_rejected,
    ev.ModelActivated: _model_activated,
    ev.ModelRolledBack: _model_rolled_back,
    ev.DriftDetected: _drift_detected,
    ev.ModelDegraded: _model_degraded,
    ev.PerformanceRecordAchieved: _record_achieved,
    ev.StrategyWeightsUpdated: _weights_updated,
    ev.MetaStrategyDecision: _meta_decision,
    ev.ModelTrainingFailed: _training_failed,
    ev.ModelRequiresRetraining: _requires_retraining,
}


class MLNotifier(Service):
    """Fan ML events out to Discord as rich embeds.

    Args:
        notifications: Servicio de notificaciones global (canal Discord).
        bus: Event Bus del sistema.
    """

    def __init__(self, notifications: NotificationService, bus: EventBus) -> None:
        super().__init__("ml_notifier")
        self._notifications = notifications
        self._bus = bus
        self._subscriptions: list[Subscription] = []
        self._sent = 0
        self._recent: deque[dict[str, str]] = deque(maxlen=50)
        self._log = logging.getLogger("app.ml.notifier")

    async def _on_start(self) -> None:
        """Subscribe to every ML event with a template."""
        for event_type in _BUILDERS:
            self._subscriptions.append(self._bus.subscribe(self._handle, event_type))

    async def _on_stop(self) -> None:
        """Unsubscribe from the bus."""
        for subscription in self._subscriptions:
            self._bus.unsubscribe(subscription)
        self._subscriptions.clear()

    async def _handle(self, event: Event) -> None:
        """Build and deliver the embed for a supported ML event."""
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
    """Build a notification with the ML source tag."""
    return Notification(title=title, message=message, level=level, fields=fields, source=_SOURCE)


def _metric_fields(metrics: dict[str, float]) -> dict[str, str]:
    """Pick headline metrics for a Discord embed."""
    keys = ("auc", "accuracy", "f1", "cv_auc")
    return {key: f"{metrics[key]:.3f}" for key in keys if key in metrics}
