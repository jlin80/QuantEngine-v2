"""Métricas Prometheus del motor (registro propio, sin dependencias)."""

from app.production.metrics.exporter import CONTENT_TYPE, registry, render
from app.production.metrics.registry import (
    Counter,
    Gauge,
    Histogram,
    MetricRegistry,
)

__all__ = [
    "CONTENT_TYPE",
    "Counter",
    "Gauge",
    "Histogram",
    "MetricRegistry",
    "registry",
    "render",
]
