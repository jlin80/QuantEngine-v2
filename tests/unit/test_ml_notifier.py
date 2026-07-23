"""Notificaciones del ML por Discord (canal falso, sin red)."""

import asyncio

from app.core.events.bus import EventBus
from app.ml import events as ev
from app.ml.notifications import MLNotifier
from app.notifications.models import Notification
from app.notifications.service import NotificationService


class _RecordingChannel:
    """Canal de notificaciones que sólo memoriza lo recibido."""

    def __init__(self) -> None:
        self.received: list[Notification] = []

    @property
    def channel_name(self) -> str:
        return "recording"

    async def send(self, notification: Notification) -> None:
        self.received.append(notification)

    async def close(self) -> None:
        return None


async def _wired() -> tuple[EventBus, NotificationService, _RecordingChannel, MLNotifier]:
    bus = EventBus()
    await bus.start()
    channel = _RecordingChannel()
    notifications = NotificationService()
    notifications.register_channel(channel)
    await notifications.start()
    notifier = MLNotifier(notifications, bus)
    await notifier.start()
    return bus, notifications, channel, notifier


async def _teardown(bus, notifications, notifier) -> None:
    await asyncio.sleep(0.05)
    await notifier.stop()
    await notifications.stop()
    await bus.stop()


async def test_ml_lifecycle_events_become_embeds():
    bus, notifications, channel, notifier = await _wired()
    await bus.publish(ev.ModelTrainingStarted(source="ml", model_type="automl", dataset_size=120))
    await bus.publish(
        ev.ModelApproved(
            source="ml",
            model_id="abc123",
            model_type="random_forest",
            metrics={"auc": 0.73, "accuracy": 0.68},
            reasons=("supera los mínimos (AUC 0.730)",),
        )
    )
    await bus.publish(
        ev.ModelActivated(
            source="ml", model_id="abc123", model_type="random_forest", previous_id=None
        )
    )
    await _teardown(bus, notifications, notifier)

    titles = [n.title for n in channel.received]
    assert any("Entrenamiento iniciado" in t for t in titles)
    assert any("aprobado" in t for t in titles)
    assert any("activado" in t for t in titles)
    approved = next(n for n in channel.received if "aprobado" in n.title)
    assert "auc" in approved.fields  # el embed lleva las métricas


async def test_drift_and_meta_events_are_delivered():
    bus, notifications, channel, notifier = await _wired()
    await bus.publish(
        ev.DriftDetected(
            source="ml",
            kind="feature",
            metric="psi:atr_pct",
            value=0.31,
            threshold=0.25,
            action="schedule_retrain",
            detail="La distribución de 'atr_pct' cambió.",
        )
    )
    await bus.publish(
        ev.StrategyWeightsUpdated(
            source="ml", weights={"momentum": 1.75, "meanrev": 0.10}, reason="evaluación periódica"
        )
    )
    await _teardown(bus, notifications, notifier)

    titles = [n.title for n in channel.received]
    assert any("Deriva detectada" in t for t in titles)
    assert any("Pesos" in t for t in titles)
    assert notifier.recent()  # el buffer reciente registró las entregas
    assert notifier.stats["sent"] >= 2


async def test_training_failure_is_reported_as_error():
    bus, notifications, channel, notifier = await _wired()
    await bus.publish(
        ev.ModelTrainingFailed(source="ml", model_type="automl", error="sin candidatos válidos")
    )
    await _teardown(bus, notifications, notifier)

    assert channel.received
    assert channel.received[0].level.value == "error"
