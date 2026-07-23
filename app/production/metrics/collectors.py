"""Colectores: traducen el estado vivo del motor a métricas Prometheus.

Se ejecutan **en el momento del scrape**, no en un bucle de fondo. Cada
colector es best-effort y está aislado: si un subsistema falla o no está
cableado, se salta y los demás siguen. Un fallo del endpoint de métricas nunca
puede tumbar el motor — sería irónico que el sistema de observabilidad fuese la
causa de la caída.

Convención de nombres: ``quantengine_<subsistema>_<medida>[_unidad]``.
"""

import logging
from typing import Any

from app.cache.service import CacheService
from app.core.container import Container
from app.core.events.bus import EventBus
from app.execution.api import ExecutionCore
from app.market.stream import FeedMetrics
from app.ml.api import MLEngine
from app.monitoring.health import HealthMonitor
from app.notifications.service import NotificationService
from app.production.api import ProductionAPI
from app.production.metrics.registry import MetricRegistry
from app.scheduler.scheduler import AsyncScheduler

_log = logging.getLogger("app.production.metrics")


def collect_all(registry: MetricRegistry, container: Container | None) -> None:
    """Refresh every metric from the live system.

    Args:
        registry: Registry to populate.
        container: DI container, or ``None`` when the engine is not wired.
    """
    if container is None:
        return
    for collector in (
        _collect_health,
        _collect_event_bus,
        _collect_scheduler,
        _collect_notifications,
        _collect_cache,
        _collect_market,
        _collect_execution,
        _collect_ml,
        _collect_production,
    ):
        try:
            collector(registry, container)
        except Exception:
            # Aislamiento por colector: nunca romper el scrape entero.
            _log.exception("Colector de métricas %s falló", collector.__name__)


def _collect_health(registry: MetricRegistry, container: Container) -> None:
    """System vitals from the Health Monitor."""
    if not container.contains(HealthMonitor):
        return
    snapshot = container.resolve(HealthMonitor).last_snapshot
    if snapshot is None:
        return
    registry.gauge("quantengine_cpu_percent", "Uso de CPU del proceso (%).").set(
        snapshot.cpu_percent
    )
    registry.gauge("quantengine_memory_percent", "Uso de memoria (%).").set(snapshot.memory_percent)
    registry.gauge("quantengine_memory_used_mb", "Memoria usada (MB).").set(snapshot.memory_used_mb)
    registry.gauge("quantengine_disk_percent", "Uso de disco (%).").set(snapshot.disk_percent)
    registry.gauge(
        "quantengine_event_loop_lag_ms", "Retraso del bucle de eventos asyncio (ms)."
    ).set(snapshot.event_loop_lag_ms)
    registry.gauge("quantengine_uptime_seconds", "Segundos desde el arranque.").set(
        snapshot.uptime_seconds
    )
    registry.gauge("quantengine_recent_errors", "Errores recientes en el buffer.").set(
        len(snapshot.recent_errors)
    )
    # 1 = healthy, 0.5 = degraded, 0 = unhealthy — así una sola serie sirve
    # para alertar por umbral sin tener que parsear una etiqueta.
    scores = {"healthy": 1.0, "degraded": 0.5, "unhealthy": 0.0}
    registry.gauge(
        "quantengine_health_status",
        "Salud global: 1 healthy, 0.5 degraded, 0 unhealthy.",
    ).set(scores.get(str(snapshot.status), 0.0))

    components = registry.gauge("quantengine_component_up", "Estado por componente (1 = ok).")
    components.clear()
    for name, status in snapshot.components.items():
        components.set(1.0 if status == "ok" else 0.0, {"component": name})


#: Estadísticas del Event Bus que suben y bajan. El resto son acumulativas.
#: La distinción importa: `rate()` sobre un gauge da resultados sin sentido, y
#: Prometheus no puede deducirlo del nombre.
_BUS_GAUGES = frozenset({"queue_size", "subscribers"})


def _collect_event_bus(registry: MetricRegistry, container: Container) -> None:
    """Event Bus throughput and errors."""
    if not container.contains(EventBus):
        return
    stats = container.resolve(EventBus).stats.to_dict()
    for key, value in stats.items():
        if not isinstance(value, int | float):
            continue
        if key in _BUS_GAUGES:
            registry.gauge(f"quantengine_event_bus_{key}", f"Event Bus: {key}.").set(float(value))
        else:
            registry.counter(f"quantengine_event_bus_{key}_total", f"Event Bus: {key}.").set(
                float(value)
            )


def _collect_scheduler(registry: MetricRegistry, container: Container) -> None:
    """Per-job run and error counters."""
    if not container.contains(AsyncScheduler):
        return
    runs = registry.counter("quantengine_scheduler_job_runs_total", "Ejecuciones por job.")
    errors = registry.counter("quantengine_scheduler_job_errors_total", "Errores por job.")
    for job in container.resolve(AsyncScheduler).jobs:
        runs.set(float(job.run_count), {"job": job.name})
        errors.set(float(job.error_count), {"job": job.name})


def _collect_notifications(registry: MetricRegistry, container: Container) -> None:
    """Discord delivery counters."""
    if not container.contains(NotificationService):
        return
    stats = container.resolve(NotificationService).stats
    registry.counter("quantengine_notifications_delivered_total", "Notificaciones entregadas.").set(
        float(stats.get("delivered", 0))
    )
    registry.counter("quantengine_notifications_failed_total", "Notificaciones fallidas.").set(
        float(stats.get("failed", 0))
    )


def _collect_cache(registry: MetricRegistry, container: Container) -> None:
    """Whether the cache is on Redis or degraded to memory."""
    if not container.contains(CacheService):
        return
    cache = container.resolve(CacheService)
    gauge = registry.gauge(
        "quantengine_cache_backend_active", "Backend de cache activo (1 = en uso)."
    )
    gauge.clear()
    gauge.set(1.0, {"backend": cache.active_backend_name})
    # Redis caído no es un fallo (degrada a memoria por diseño), pero sí es una
    # condición que conviene poder alertar.
    registry.gauge("quantengine_cache_degraded", "1 si el cache cayó al fallback.").set(
        1.0 if cache.degraded else 0.0
    )


def _collect_market(registry: MetricRegistry, container: Container) -> None:
    """Market feed throughput."""
    if not container.contains(FeedMetrics):
        return
    payload = container.resolve(FeedMetrics).to_dict()
    for key, value in _numeric_items(payload):
        registry.gauge(f"quantengine_market_{key}", f"Data Engine: {key}.").set(value)


def _collect_execution(registry: MetricRegistry, container: Container) -> None:
    """Portfolio, positions, risk and broker counters."""
    if not container.contains(ExecutionCore):
        return
    execution = container.resolve(ExecutionCore)
    positions = execution.positions.open_positions
    snapshot = execution.portfolio.snapshot(positions)

    registry.gauge("quantengine_portfolio_balance", "Saldo realizado.").set(snapshot.balance)
    registry.gauge("quantengine_portfolio_equity", "Equity (saldo + flotante).").set(
        snapshot.equity
    )
    registry.gauge("quantengine_portfolio_floating_pnl", "PnL flotante.").set(snapshot.floating_pnl)
    registry.gauge("quantengine_portfolio_drawdown_percent", "Drawdown desde el pico (%).").set(
        snapshot.drawdown_pct
    )
    registry.gauge("quantengine_portfolio_exposure_percent", "Exposición sobre equity (%).").set(
        snapshot.exposure_pct
    )
    registry.gauge("quantengine_open_positions", "Posiciones abiertas.").set(len(positions))
    registry.counter("quantengine_trades_total", "Operaciones cerradas.").set(
        float(snapshot.total_trades)
    )

    risk = execution.risk.status()
    registry.gauge("quantengine_kill_switch_active", "1 si el kill switch está activo.").set(
        1.0 if risk.get("kill_switch") else 0.0
    )
    registry.gauge(
        "quantengine_circuit_breaker_active", "1 si el circuit breaker está disparado."
    ).set(1.0 if risk.get("circuit_breaker") else 0.0)
    consecutive = risk.get("consecutive_losses", 0)
    registry.gauge("quantengine_consecutive_losses", "Racha de pérdidas consecutivas.").set(
        float(consecutive) if isinstance(consecutive, int | float) else 0.0
    )

    broker_stats = execution.engine.broker.stats
    registry.counter("quantengine_broker_executed_total", "Órdenes ejecutadas.").set(
        float(broker_stats.get("executed", 0))
    )
    registry.counter("quantengine_broker_rejected_total", "Órdenes rechazadas.").set(
        float(broker_stats.get("rejected", 0))
    )
    # El nombre del broker como etiqueta deja constancia en la serie de que se
    # estuvo operando en paper — auditoría histórica sin mirar logs.
    broker_gauge = registry.gauge("quantengine_broker_info", "Broker activo (1 = en uso).")
    broker_gauge.clear()
    broker_gauge.set(1.0, {"broker": execution.engine.broker.broker_name})


def _collect_ml(registry: MetricRegistry, container: Container) -> None:
    """Machine Learning status."""
    if not container.contains(MLEngine):
        return
    status = container.resolve(MLEngine).status()
    for key, value in _numeric_items(status):
        registry.gauge(f"quantengine_ml_{key}", f"Machine Learning: {key}.").set(value)
    drift = status.get("drift")
    if isinstance(drift, dict):
        registry.gauge("quantengine_ml_drift_detected", "1 si el ML detecta deriva.").set(
            1.0 if drift.get("has_drift") else 0.0
        )


def _collect_production(registry: MetricRegistry, container: Container) -> None:
    """Live gate, safe mode and kill switch state."""
    if not container.contains(ProductionAPI):
        return
    production = container.resolve(ProductionAPI)
    registry.gauge("quantengine_safe_mode_active", "1 si Safe Mode está activo.").set(
        1.0 if production.safe_mode.active else 0.0
    )
    registry.gauge(
        "quantengine_production_kill_switch_active", "1 si el kill switch global está activo."
    ).set(1.0 if production.kill_switch.active else 0.0)
    registry.gauge(
        "quantengine_live_enabled",
        "1 si el modo resuelto es live. Debe ser 0 mientras la Fase 9 no se abra.",
    ).set(1.0 if production.mode.resolved_mode() == "live" else 0.0)

    report = production.gate.last_report
    if report is not None:
        registry.gauge("quantengine_live_gate_approved", "1 si el Live Gate aprueba.").set(
            1.0 if report.approved else 0.0
        )
        registry.gauge(
            "quantengine_live_gate_failed_criteria", "Criterios del Live Gate sin cumplir."
        ).set(float(report.failed_count))


def _numeric_items(payload: dict[str, Any]) -> list[tuple[str, float]]:
    """Flatten a status dict to its numeric, metric-safe entries.

    Args:
        payload: Arbitrary status mapping.

    Returns:
        ``(name, value)`` pairs for numeric entries with usable names.
    """
    items: list[tuple[str, float]] = []
    for key, value in payload.items():
        if not isinstance(key, str) or not key.replace("_", "").isalnum():
            continue
        if isinstance(value, bool):
            items.append((key, 1.0 if value else 0.0))
        elif isinstance(value, int | float):
            items.append((key, float(value)))
    return items
