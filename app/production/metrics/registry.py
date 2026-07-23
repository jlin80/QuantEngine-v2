"""Registro de métricas propio, sin dependencias externas.

Mismo criterio que el Event Bus y el cache de la Fase 1: el formato de
exposición de Prometheus es texto plano y bien documentado, así que traer
``prometheus_client`` sólo para renderizarlo añadiría una dependencia a cambio
de poco. Aquí caben tres tipos (``Counter``, ``Gauge``, ``Histogram``) en unas
pocas decenas de líneas.

Las métricas se generan **al ser scrapeadas** (modelo pull): los colectores se
ejecutan cuando Prometheus pide ``/metrics``, no en un bucle de fondo. Así la
foto es fresca y no se paga coste cuando nadie mira.
"""

import math
import re
from threading import Lock
from typing import TypeVar

_VALID_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_VALID_LABEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

#: Cortes por defecto de los histogramas, en segundos.
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

LabelValues = tuple[tuple[str, str], ...]

_M = TypeVar("_M", bound="Metric")


def _normalize(labels: dict[str, str] | None) -> LabelValues:
    """Turn a label mapping into a deterministic, hashable key.

    Args:
        labels: Label mapping, or ``None``.

    Returns:
        Sorted tuple of ``(name, value)`` pairs.

    Raises:
        ValueError: If a label name is not a valid Prometheus identifier.
    """
    if not labels:
        return ()
    for name in labels:
        if not _VALID_LABEL.match(name):
            raise ValueError(f"Nombre de etiqueta inválido: {name!r}")
    return tuple(sorted((name, str(value)) for name, value in labels.items()))


def _escape(value: str) -> str:
    """Escape a label value per the text exposition format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(labels: LabelValues, extra: tuple[str, str] | None = None) -> str:
    """Render a label set as ``{a="1",b="2"}`` (empty string when unlabelled)."""
    pairs = list(labels)
    if extra is not None:
        pairs.append(extra)
    if not pairs:
        return ""
    body = ",".join(f'{name}="{_escape(value)}"' for name, value in pairs)
    return "{" + body + "}"


def _render_value(value: float) -> str:
    """Render a float the way Prometheus expects (incl. +Inf/NaN)."""
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if math.isnan(value):
        return "NaN"
    return repr(float(value))


class Metric:
    """Base class for every metric type.

    Args:
        name: Metric name (Prometheus identifier).
        help_text: One-line description shown in ``# HELP``.

    Raises:
        ValueError: If the name is not a valid Prometheus identifier.
    """

    metric_type = "untyped"

    def __init__(self, name: str, help_text: str) -> None:
        if not _VALID_NAME.match(name):
            raise ValueError(f"Nombre de métrica inválido: {name!r}")
        self.name = name
        self.help_text = help_text
        self._lock = Lock()

    def render(self) -> list[str]:
        """Render this metric as exposition-format lines."""
        raise NotImplementedError


class Counter(Metric):
    """Monotonically increasing counter (requests, errors, executions)."""

    metric_type = "counter"

    def __init__(self, name: str, help_text: str) -> None:
        super().__init__(name, help_text)
        self._values: dict[LabelValues, float] = {}

    def inc(self, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increase the counter.

        Args:
            amount: Increment (must not be negative — a counter never goes down).
            labels: Optional label set.

        Raises:
            ValueError: If ``amount`` is negative.
        """
        if amount < 0:
            raise ValueError("Un contador no puede decrecer")
        key = _normalize(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        """Set the absolute value (for counters read from another system).

        Args:
            value: Absolute counter value observed elsewhere.
            labels: Optional label set.
        """
        key = _normalize(labels)
        with self._lock:
            self._values[key] = float(value)

    def render(self) -> list[str]:
        """Render as exposition-format lines."""
        with self._lock:
            items = sorted(self._values.items())
        return [f"{self.name}{_render_labels(k)} {_render_value(v)}" for k, v in items]


class Gauge(Metric):
    """Value that can go up and down (CPU, equity, open positions)."""

    metric_type = "gauge"

    def __init__(self, name: str, help_text: str) -> None:
        super().__init__(name, help_text)
        self._values: dict[LabelValues, float] = {}

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        """Set the current value.

        Args:
            value: New value.
            labels: Optional label set.
        """
        key = _normalize(labels)
        with self._lock:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Add to the current value (may be negative)."""
        key = _normalize(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def clear(self) -> None:
        """Drop every sample.

        Los colectores lo usan antes de repoblar series con etiquetas
        dinámicas (símbolos, estrategias): si no, una serie desaparecida se
        quedaría congelada en su último valor para siempre.
        """
        with self._lock:
            self._values.clear()

    def render(self) -> list[str]:
        """Render as exposition-format lines."""
        with self._lock:
            items = sorted(self._values.items())
        return [f"{self.name}{_render_labels(k)} {_render_value(v)}" for k, v in items]


class Histogram(Metric):
    """Distribution of observations (latencies, durations).

    Args:
        name: Metric name.
        help_text: Description.
        buckets: Upper bounds, ascending. ``+Inf`` is appended automatically.
    """

    metric_type = "histogram"

    def __init__(
        self, name: str, help_text: str, buckets: tuple[float, ...] = DEFAULT_BUCKETS
    ) -> None:
        super().__init__(name, help_text)
        self._buckets = (*sorted(buckets), math.inf)
        self._counts: dict[LabelValues, list[int]] = {}
        self._sums: dict[LabelValues, float] = {}

    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        """Record one observation.

        Args:
            value: Observed value.
            labels: Optional label set.
        """
        key = _normalize(labels)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * len(self._buckets))
            self._sums[key] = self._sums.get(key, 0.0) + value
            for index, bound in enumerate(self._buckets):
                if value <= bound:
                    counts[index] += 1

    def render(self) -> list[str]:
        """Render buckets, sum and count as exposition-format lines."""
        lines: list[str] = []
        with self._lock:
            items = sorted(self._counts.items())
            sums = dict(self._sums)
        for key, counts in items:
            # `counts` ya es acumulativo: `observe` incrementa todos los buckets
            # cuyo límite superior alcanza el valor.
            for bound, count in zip(self._buckets, counts, strict=True):
                label = _render_labels(key, ("le", _render_value(bound)))
                lines.append(f"{self.name}_bucket{label} {count}")
            lines.append(f"{self.name}_sum{_render_labels(key)} {_render_value(sums[key])}")
            lines.append(f"{self.name}_count{_render_labels(key)} {counts[-1]}")
        return lines


class MetricRegistry:
    """Holds every metric and renders the exposition payload."""

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}
        self._lock = Lock()

    def counter(self, name: str, help_text: str) -> Counter:
        """Get or create a counter.

        Args:
            name: Metric name.
            help_text: Description (used on creation).

        Returns:
            The registered counter.

        Raises:
            ValueError: If the name is already registered with another type.
        """
        return self._get_or_create(name, help_text, Counter)

    def gauge(self, name: str, help_text: str) -> Gauge:
        """Get or create a gauge."""
        return self._get_or_create(name, help_text, Gauge)

    def histogram(
        self, name: str, help_text: str, buckets: tuple[float, ...] = DEFAULT_BUCKETS
    ) -> Histogram:
        """Get or create a histogram."""
        with self._lock:
            existing = self._metrics.get(name)
            if existing is not None:
                if not isinstance(existing, Histogram):
                    raise ValueError(f"{name!r} ya existe como {existing.metric_type}")
                return existing
            created = Histogram(name, help_text, buckets)
            self._metrics[name] = created
            return created

    def _get_or_create(self, name: str, help_text: str, kind: type[_M]) -> _M:
        """Shared get-or-create for counters and gauges.

        Args:
            name: Metric name.
            help_text: Description used when creating it.
            kind: Concrete metric class.

        Returns:
            The registered metric.

        Raises:
            ValueError: If the name exists with a different metric type.
        """
        with self._lock:
            existing = self._metrics.get(name)
            if existing is not None:
                if not isinstance(existing, kind):
                    raise ValueError(f"{name!r} ya existe como {existing.metric_type}")
                return existing
            created = kind(name, help_text)
            self._metrics[name] = created
            return created

    def render(self) -> str:
        """Render every metric in the Prometheus text exposition format.

        Returns:
            The payload, always ending in a newline (the format requires it).
        """
        with self._lock:
            metrics = [self._metrics[name] for name in sorted(self._metrics)]
        lines: list[str] = []
        for metric in metrics:
            samples = metric.render()
            if not samples:
                continue  # una métrica sin muestras no se expone
            lines.append(f"# HELP {metric.name} {metric.help_text}")
            lines.append(f"# TYPE {metric.name} {metric.metric_type}")
            lines.extend(samples)
        return "\n".join(lines) + "\n"
