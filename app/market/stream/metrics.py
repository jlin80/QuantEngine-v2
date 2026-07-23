"""Métricas del feed: throughput, latencia, reconexiones, errores."""

import time
from collections import deque
from typing import Any


class RateCounter:
    """Contador de eventos por segundo sobre una ventana deslizante.

    Args:
        window_seconds: Tamaño de la ventana de medición.
    """

    def __init__(self, window_seconds: float = 10.0) -> None:
        self._window = window_seconds
        self._events: deque[float] = deque()
        self._total = 0

    def hit(self, count: int = 1) -> None:
        """Record ``count`` events at the current instant."""
        now = time.monotonic()
        for _ in range(count):
            self._events.append(now)
        self._total += count
        self._prune(now)

    def _prune(self, now: float) -> None:
        """Drop events outside the window."""
        cutoff = now - self._window
        while self._events and self._events[0] < cutoff:
            self._events.popleft()

    @property
    def rate(self) -> float:
        """Eventos por segundo dentro de la ventana."""
        self._prune(time.monotonic())
        return len(self._events) / self._window

    @property
    def total(self) -> int:
        """Total acumulado desde el arranque."""
        return self._total


class LatencyTracker:
    """EMA + extremos de una serie de latencias en milisegundos.

    Args:
        alpha: Factor de suavizado de la media exponencial.
    """

    def __init__(self, alpha: float = 0.1) -> None:
        self._alpha = alpha
        self._ema: float | None = None
        self._min: float | None = None
        self._max: float | None = None
        self._samples = 0

    def observe(self, latency_ms: float) -> None:
        """Record one latency sample."""
        self._samples += 1
        self._ema = (
            latency_ms
            if self._ema is None
            else self._alpha * latency_ms + (1 - self._alpha) * self._ema
        )
        self._min = latency_ms if self._min is None else min(self._min, latency_ms)
        self._max = latency_ms if self._max is None else max(self._max, latency_ms)

    def to_dict(self) -> dict[str, float | int | None]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "ema_ms": round(self._ema, 3) if self._ema is not None else None,
            "min_ms": round(self._min, 3) if self._min is not None else None,
            "max_ms": round(self._max, 3) if self._max is not None else None,
            "samples": self._samples,
        }


class FeedMetrics:
    """Métricas agregadas del Data Engine (una instancia global)."""

    def __init__(self) -> None:
        self.ws_messages = RateCounter()
        self.ticks = RateCounter()
        self.data_latency = LatencyTracker()
        self.ping_latency = LatencyTracker()
        self.reconnections = 0
        self.errors = 0
        self.rejected = 0
        self.dropped = 0
        self._per_symbol_ticks: dict[str, int] = {}

    def tick(self, symbol: str) -> None:
        """Record one accepted tick for a symbol."""
        self.ticks.hit()
        self._per_symbol_ticks[symbol] = self._per_symbol_ticks.get(symbol, 0) + 1

    def symbol_ticks(self, symbol: str) -> int:
        """Total accepted ticks for one symbol."""
        return self._per_symbol_ticks.get(symbol, 0)

    def to_dict(self) -> dict[str, Any]:
        """Serialize every metric to a JSON-safe dictionary."""
        return {
            "ws_messages_total": self.ws_messages.total,
            "ws_messages_per_second": round(self.ws_messages.rate, 2),
            "ticks_total": self.ticks.total,
            "ticks_per_second": round(self.ticks.rate, 2),
            "ticks_per_symbol": dict(self._per_symbol_ticks),
            "data_latency": self.data_latency.to_dict(),
            "ping_latency": self.ping_latency.to_dict(),
            "reconnections": self.reconnections,
            "errors": self.errors,
            "rejected": self.rejected,
            "dropped": self.dropped,
        }
