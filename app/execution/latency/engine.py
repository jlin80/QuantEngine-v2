"""Motor de latencia: retardo total y su efecto (deriva) sobre el precio.

En trading real, entre que se decide y se ejecuta el precio se mueve. Aquí se
suma la latencia de red, broker, exchange e interna, se añade jitter aleatorio
y se traduce a una deriva de precio en bps que empeora (o desplaza) el fill.
"""

import random
from dataclasses import dataclass

from app.config.settings import LatencySettings


@dataclass(frozen=True, slots=True)
class LatencyQuote:
    """Latencia simulada y su deriva de precio asociada."""

    total_ms: float
    network_ms: float
    broker_ms: float
    exchange_ms: float
    internal_ms: float
    drift_bps: float

    def to_dict(self) -> dict[str, float]:
        """JSON-safe dict."""
        return {
            "total_ms": self.total_ms,
            "network_ms": self.network_ms,
            "broker_ms": self.broker_ms,
            "exchange_ms": self.exchange_ms,
            "internal_ms": self.internal_ms,
            "drift_bps": self.drift_bps,
        }


class LatencyEngine:
    """Simulate end-to-end execution latency and its price impact.

    Args:
        settings: Latency configuration.
        rng: Fuente aleatoria inyectable (tests deterministas).
    """

    def __init__(self, settings: LatencySettings, rng: random.Random | None = None) -> None:
        self._settings = settings
        self._rng = rng or random.Random()

    def sample(self) -> LatencyQuote:
        """Sample a latency quote for one execution.

        Returns:
            Desglose de latencias y la deriva de precio (bps) que provoca.
        """
        if not self._settings.enabled:
            return LatencyQuote(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        jitter = self._settings.jitter_ms
        network = self._settings.network_ms + self._rng.uniform(0.0, jitter)
        broker = self._settings.broker_ms + self._rng.uniform(0.0, jitter)
        exchange = self._settings.exchange_ms + self._rng.uniform(0.0, jitter)
        internal = self._settings.internal_ms + self._rng.uniform(0.0, jitter)
        total = network + broker + exchange + internal
        drift = total / 100.0 * self._settings.price_drift_bps_per_100ms
        return LatencyQuote(
            total_ms=round(total, 3),
            network_ms=round(network, 3),
            broker_ms=round(broker, 3),
            exchange_ms=round(exchange, 3),
            internal_ms=round(internal, 3),
            drift_bps=round(drift, 4),
        )
