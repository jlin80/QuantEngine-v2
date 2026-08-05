"""Meta Risk Engine (Bloque 12) — el riesgo de la máquina, no del mercado.

Todos los demás bloques miden el mercado o las operaciones. Este mide **la
infraestructura**: CPU, RAM, Redis, bróker, latencia, exchange, API, MT5, Event
Bus, scheduler y cache. Y produce lo mismo que el Bloque 11: un multiplicador de
exposición.

**Por qué merece un motor propio y no una alarma más.** Una VPS al 95% de CPU no
impide operar — impide operar *a tiempo*. El motor sigue decidiendo, las órdenes
siguen saliendo, y la degradación aparece como slippage y como salidas tardías,
que se leen como mala suerte de mercado. El daño es real y la causa es invisible
desde cualquier métrica de trading. Traducirlo a exposición es la forma de que
el sistema se proteja de su propia máquina.

**Composición con el Bloque 11.** El multiplicador de calidad de dato entra aquí
como una señal más, y el resultado final es el **producto** de ambos mundos, no
el mínimo: un feed mediocre en una máquina saturada es peor que cualquiera de
las dos cosas por separado, y quedarse con el mínimo lo negaría.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config.settings import MetaRiskSettings
from app.utils.time import utc_now

# Los componentes que el bloque enumera. Cada uno en 0-1, donde 1 es sano.
COMPONENTS: tuple[str, ...] = (
    "cpu",
    "memory",
    "event_loop",
    "event_bus",
    "redis",
    "broker",
    "mt5",
    "exchange",
    "api",
    "scheduler",
    "cache",
    "data_quality",
)


@dataclass(frozen=True, kw_only=True, slots=True)
class MetaRiskInputs:
    """Estado crudo de la infraestructura.

    Se pasa como estructura, no como servicios, por la misma razón que en el
    Bloque 11: el motor tiene que poder correr en un test sin montar medio
    sistema, y el sistema no debería depender de él para arrancar.

    Attributes:
        cpu_percent: Uso de CPU 0-100.
        memory_percent: Uso de RAM 0-100.
        event_loop_lag_ms: Retraso del bucle de eventos.
        event_bus_queue: Elementos encolados en el bus.
        component_statuses: Estado por componente según el watchdog
            (``healthy`` / ``degraded`` / ``down`` / desconocido).
        data_quality_multiplier: Multiplicador del Bloque 11.
    """

    cpu_percent: float | None = None
    memory_percent: float | None = None
    event_loop_lag_ms: float | None = None
    event_bus_queue: int | None = None
    component_statuses: dict[str, str] = field(default_factory=dict)
    data_quality_multiplier: float = 1.0


@dataclass(frozen=True, kw_only=True, slots=True)
class MetaRiskReport:
    """Salud de la infraestructura y su consecuencia sobre la exposición."""

    generated_at: datetime = field(default_factory=utc_now)
    signals: dict[str, float] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    score: float | None = None
    risk_multiplier: float = 1.0
    degraded: bool = False
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "signals": {k: round(v, 4) for k, v in self.signals.items()},
            "missing": self.missing,
            "score": None if self.score is None else round(self.score, 2),
            "risk_multiplier": round(self.risk_multiplier, 4),
            "degraded": self.degraded,
            "reasons": list(self.reasons),
        }


class MetaRiskEngine:
    """Turn infrastructure health into a single exposure multiplier.

    Args:
        settings: Umbrales, pesos y suelo.
        inputs_provider: Fuente del estado de la infraestructura.
    """

    def __init__(
        self,
        settings: MetaRiskSettings,
        inputs_provider: Callable[[], MetaRiskInputs],
    ) -> None:
        self._settings = settings
        self._inputs = inputs_provider
        self._last: MetaRiskReport | None = None
        self._log = logging.getLogger("app.monitoring.meta_risk")

    def measure(self) -> MetaRiskReport:
        """Take one measurement of the machine's health.

        Returns:
            El informe con el multiplicador ya compuesto.
        """
        data = self._inputs()
        signals: dict[str, float] = {}
        missing: dict[str, str] = {}

        if data.cpu_percent is not None:
            signals["cpu"] = _headroom(
                data.cpu_percent, self._settings.max_cpu_percent, self._settings.comfort_fraction
            )
        else:
            missing["cpu"] = "sin lectura de CPU"
        if data.memory_percent is not None:
            signals["memory"] = _headroom(
                data.memory_percent,
                self._settings.max_memory_percent,
                self._settings.comfort_fraction,
            )
        else:
            missing["memory"] = "sin lectura de memoria"
        if data.event_loop_lag_ms is not None:
            signals["event_loop"] = _headroom(
                data.event_loop_lag_ms,
                self._settings.max_event_loop_lag_ms,
                self._settings.comfort_fraction,
            )
        else:
            missing["event_loop"] = "sin medición del bucle de eventos"
        if data.event_bus_queue is not None:
            signals["event_bus"] = _headroom(
                float(data.event_bus_queue),
                float(self._settings.max_event_bus_queue),
                self._settings.comfort_fraction,
            )
        else:
            missing["event_bus"] = "sin cola del bus medida"

        for component in self._settings.tracked_components:
            status = data.component_statuses.get(component)
            if status is None:
                # Un componente que no reporta no es un componente caído: puede
                # no estar cableado en este despliegue (Redis en local, MT5 en
                # cripto). Penalizarlo apagaría medio sistema por configuración.
                missing[component] = "el watchdog no reporta este componente"
                continue
            signals[component] = self._settings.status_scores.get(status, 0.5)

        signals["data_quality"] = max(0.0, min(1.0, data.data_quality_multiplier))

        score = self._score(signals)
        multiplier, degraded, reasons = self._consequence(score, signals, data)
        report = MetaRiskReport(
            generated_at=utc_now(),
            signals=signals,
            missing=missing,
            score=score,
            risk_multiplier=multiplier,
            degraded=degraded,
            reasons=reasons,
        )
        self._last = report
        return report

    def _score(self, signals: dict[str, float]) -> float | None:
        """Weighted mean of the observable signals only."""
        if not signals:
            return None
        weights = self._settings.weights
        total = sum(float(weights.get(name, 1.0)) for name in signals)
        if total <= 0:
            return None
        weighted = sum(float(weights.get(name, 1.0)) * value for name, value in signals.items())
        return 100.0 * weighted / total

    def _consequence(
        self, score: float | None, signals: dict[str, float], data: MetaRiskInputs
    ) -> tuple[float, bool, tuple[str, ...]]:
        """Compose the final multiplier out of infrastructure and data quality.

        El resultado es el **producto** del multiplicador de infraestructura y
        el de calidad de dato, no el mínimo: un feed mediocre en una máquina
        saturada es peor que cualquiera de las dos cosas por separado, y
        quedarse con el mínimo lo negaría. El suelo se aplica al final, para que
        componer nunca pueda llevar la exposición por debajo de lo permitido.
        """
        floor = self._settings.risk_floor
        if score is None:
            return 1.0, False, ("sin señales observables: no se altera el riesgo",)

        reasons = [
            f"{name} {value:.2f} < mínimo {self._settings.signal_floor}"
            for name, value in sorted(signals.items(), key=lambda item: item[1])
            if value < self._settings.signal_floor
        ]
        threshold = self._settings.degraded_score
        infra = 1.0
        degraded = False
        # `data_quality` ya viene como multiplicador; se saca del score de
        # infraestructura para no contarlo dos veces al multiplicar después.
        infra_signals = {k: v for k, v in signals.items() if k != "data_quality"}
        infra_score = self._score(infra_signals)
        if infra_score is not None and infra_score < threshold:
            infra = floor + (1.0 - floor) * max(0.0, infra_score) / max(threshold, 1e-9)
            degraded = True
            reasons.insert(0, f"infraestructura {infra_score:.1f} < umbral {threshold}")

        for name, critical_floor in self._settings.critical_components.items():
            value = signals.get(name)
            if value is None or value >= critical_floor:
                continue
            degraded = True
            infra = min(infra, floor + (1.0 - floor) * value)
            reasons.insert(0, f"componente crítico {name} {value:.2f} < {critical_floor}")

        multiplier = infra * max(0.0, min(1.0, data.data_quality_multiplier))
        if data.data_quality_multiplier < 1.0:
            degraded = True
        if not degraded:
            return 1.0, False, tuple(reasons) or (f"infraestructura {score:.1f}/100",)
        return round(max(floor, min(1.0, multiplier)), 4), True, tuple(reasons)

    def risk_multiplier(self) -> float:
        """Current exposure multiplier (1.0 si aún no se ha medido nada)."""
        return 1.0 if self._last is None else self._last.risk_multiplier

    def last_report(self) -> MetaRiskReport | None:
        """Informe de la última medición."""
        return self._last

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot (dashboard)."""
        return {
            "enabled": self._settings.enabled,
            "last_report": None if self._last is None else self._last.to_dict(),
        }


def _headroom(value: float, limit: float, comfort: float) -> float:
    """Map a "smaller is better" magnitude into a 0-1 health signal.

    Con una rampa lineal simple (``1 - value/limit``), una CPU al 20% frente a
    un techo del 90% daba una señal de 0.78 — es decir, el motor consideraba
    ligeramente enferma una máquina que está perfectamente. Y como esas señales
    se promedian, la máquina "siempre" parecía a medio gas y la degradación real
    no destacaba sobre el fondo.

    Por eso hay **zona de confort**: por debajo de ``comfort × limit`` la señal
    vale 1.0, y sólo a partir de ahí empieza a caer hasta 0 en el límite. Un
    recurso holgado no es un recurso a medias.
    """
    if limit <= 0:
        return 1.0
    threshold = limit * max(0.0, min(1.0, comfort))
    if value <= threshold:
        return 1.0
    span = limit - threshold
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, (limit - value) / span))
