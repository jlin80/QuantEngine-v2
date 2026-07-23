"""Exportador Prometheus y estructura preparada para OpenTelemetry.

El registro es un singleton de proceso: las métricas son estado global por
naturaleza (Prometheus scrapea *un* proceso) y pasarlo por inyección a cada
punto de instrumentación acabaría contaminando firmas por todo el código.
"""

from app.core.container import Container
from app.production.metrics.collectors import collect_all
from app.production.metrics.registry import MetricRegistry

#: Content-Type que exige el formato de exposición de texto de Prometheus.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

registry = MetricRegistry()
"""Registro de métricas de todo el proceso."""


def render(container: Container | None) -> str:
    """Refresh every collector and render the exposition payload.

    Args:
        container: DI container, or ``None`` when the engine is not wired
            (devuelve un payload válido pero vacío en vez de fallar).

    Returns:
        The Prometheus text exposition payload.
    """
    collect_all(registry, container)
    return registry.render()
