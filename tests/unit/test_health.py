"""Pruebas del Health Monitor."""

from app.config.settings import HealthSettings, WatchdogSettings
from app.core.events.bus import EventBus
from app.monitoring.health import HealthMonitor, HealthStatus
from app.monitoring.watchdog import Watchdog


async def test_snapshot_contains_all_sections():
    bus = EventBus()
    await bus.start()
    watchdog = Watchdog(WatchdogSettings(), bus)
    watchdog.register("dummy")
    monitor = HealthMonitor(HealthSettings(check_interval_seconds=999), bus, watchdog)
    await monitor.start()

    snap = await monitor.snapshot()
    data = snap.to_dict()

    for key in (
        "status",
        "timestamp",
        "uptime_seconds",
        "cpu_percent",
        "memory_percent",
        "disk_percent",
        "event_loop_lag_ms",
        "event_bus",
        "components",
        "recent_errors",
    ):
        assert key in data, f"falta '{key}' en el snapshot"
    assert data["components"] == {"dummy": "ok"}
    assert snap.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)
    assert monitor.last_snapshot is snap

    await monitor.stop()
    await bus.stop()


async def test_evaluate_thresholds():
    bus = EventBus()
    monitor = HealthMonitor(
        HealthSettings(cpu_warn_pct=80, memory_warn_pct=80, disk_warn_pct=90), bus
    )
    evaluate = monitor._evaluate  # acceso deliberado para probar la lógica pura

    assert evaluate(cpu=10, memory_pct=10, disk_pct=10, components={}) is HealthStatus.HEALTHY
    assert evaluate(cpu=95, memory_pct=10, disk_pct=10, components={}) is HealthStatus.DEGRADED
    assert (
        evaluate(cpu=10, memory_pct=10, disk_pct=10, components={"x": "frozen"})
        is HealthStatus.UNHEALTHY
    )
