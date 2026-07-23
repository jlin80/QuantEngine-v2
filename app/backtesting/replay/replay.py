"""Replay Engine: reproduce el mercado exactamente como ocurrió (Fase 6).

Controlador de cursor sobre una serie de velas con pausa, reanudación, cambio
de velocidad, avance y retroceso. Sirve tanto al dashboard (reproducción
interactiva) como a la depuración de una estrategia paso a paso. La ejecución en
tiempo real (dormir ``dt / speed`` entre velas) queda como estructura preparada.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.market.models import Candle


class ReplayState(StrEnum):
    """Estados del reproductor."""

    PAUSED = "paused"
    PLAYING = "playing"
    FINISHED = "finished"


@dataclass(slots=True)
class ReplayController:
    """Controllable cursor over a historical candle series.

    Args:
        candles: Serie de velas a reproducir (orden cronológico).
        speed: Multiplicador de velocidad para la reproducción en tiempo real.
    """

    candles: Sequence[Candle]
    speed: float = 1.0
    _cursor: int = -1
    _state: ReplayState = ReplayState.PAUSED

    @property
    def state(self) -> ReplayState:
        """Current replay state."""
        return self._state

    @property
    def cursor(self) -> int:
        """Index of the last emitted candle (-1 before the start)."""
        return self._cursor

    @property
    def current(self) -> Candle | None:
        """The candle at the cursor (``None`` before the first step)."""
        if 0 <= self._cursor < len(self.candles):
            return self.candles[self._cursor]
        return None

    def play(self) -> None:
        """Set the controller to the playing state."""
        if self._cursor + 1 < len(self.candles):
            self._state = ReplayState.PLAYING

    def pause(self) -> None:
        """Pause the controller."""
        if self._state is not ReplayState.FINISHED:
            self._state = ReplayState.PAUSED

    def set_speed(self, speed: float) -> None:
        """Set the real-time replay speed multiplier.

        Args:
            speed: Multiplicador (>0). Valores mayores reproducen más rápido.

        Raises:
            ValueError: Si ``speed`` no es positivo.
        """
        if speed <= 0:
            raise ValueError("speed debe ser positivo")
        self.speed = speed

    def step_forward(self) -> Candle | None:
        """Advance one candle and return it (``None`` at the end)."""
        if self._cursor + 1 >= len(self.candles):
            self._state = ReplayState.FINISHED
            return None
        self._cursor += 1
        if self._cursor + 1 >= len(self.candles):
            self._state = ReplayState.FINISHED
        return self.candles[self._cursor]

    def step_back(self) -> Candle | None:
        """Rewind one candle and return the new current (``None`` before start)."""
        if self._cursor <= 0:
            self._cursor = -1
            self._state = ReplayState.PAUSED
            return None
        self._cursor -= 1
        self._state = ReplayState.PAUSED
        return self.candles[self._cursor]

    def seek(self, index: int) -> Candle | None:
        """Jump the cursor to an explicit index.

        Args:
            index: Índice destino (se acota al rango válido).

        Returns:
            La vela en el índice, o ``None`` si la serie está vacía.
        """
        if not self.candles:
            return None
        self._cursor = max(0, min(index, len(self.candles) - 1))
        self._state = (
            ReplayState.FINISHED if self._cursor + 1 >= len(self.candles) else ReplayState.PAUSED
        )
        return self.candles[self._cursor]

    def reset(self) -> None:
        """Rewind to before the first candle."""
        self._cursor = -1
        self._state = ReplayState.PAUSED

    def status(self) -> dict[str, Any]:
        """Compact status for the dashboard."""
        return {
            "state": self._state.value,
            "cursor": self._cursor,
            "total": len(self.candles),
            "speed": self.speed,
            "progress_pct": round(
                (self._cursor + 1) / len(self.candles) * 100.0 if self.candles else 0.0, 2
            ),
        }
