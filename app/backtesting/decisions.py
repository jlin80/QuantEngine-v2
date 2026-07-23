"""Fuentes de decisión para el backtest (Fase 6).

El motor de backtest es agnóstico de *cómo* se generan las decisiones: recibe
una :class:`DecisionSource`. En producción se conectará el ``QuantCore`` real
(estrategias → señales → Decision Engine) reproducido sobre datos históricos;
para pruebas y benchmarks se usan fuentes deterministas (una función o un cruce
de medias). El contrato de salida es el mismo evento que emite el Decision
Engine en vivo: :class:`DecisionGenerated`.
"""

from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

from app.backtesting.datasets import sma
from app.engine.events import DecisionGenerated
from app.market.models import Candle


@runtime_checkable
class DecisionSource(Protocol):
    """Produce (or withhold) a trading decision for a bar."""

    def decide(
        self, symbol: str, candles: Sequence[Candle], index: int
    ) -> DecisionGenerated | None:
        """Return a decision for bar ``index`` (or ``None`` to stay flat).

        Args:
            symbol: Símbolo simulado.
            candles: Serie completa; solo deben leerse velas ``<= index``.
            index: Índice de la vela recién cerrada.

        Returns:
            Un :class:`DecisionGenerated` de apertura, o ``None``.
        """
        ...

    def reset(self) -> None:
        """Reset any internal state before a fresh run."""
        ...


def open_decision(
    symbol: str,
    action: str,
    *,
    score: float = 1.0,
    confidence: float = 1.0,
    summary: str = "",
) -> DecisionGenerated:
    """Build an accepted open decision event.

    Args:
        symbol: Símbolo.
        action: ``open_long`` u ``open_short``.
        score: Score de la decisión.
        confidence: Confianza de la decisión.
        summary: Explicación textual.

    Returns:
        Evento de decisión aceptada listo para el Execution Engine.
    """
    return DecisionGenerated(
        source="backtest",
        decision_id=f"bt-{symbol}-{action}",
        symbol=symbol,
        action=action,
        accepted=True,
        score=score,
        confidence=confidence,
        summary=summary or f"backtest {action}",
    )


class CallableDecisionSource:
    """Adapt a plain function into a :class:`DecisionSource`.

    Args:
        fn: Función que decide por vela; devuelve un evento o ``None``.
    """

    def __init__(
        self,
        fn: Callable[[str, Sequence[Candle], int], DecisionGenerated | None],
    ) -> None:
        self._fn = fn

    def decide(
        self, symbol: str, candles: Sequence[Candle], index: int
    ) -> DecisionGenerated | None:
        """Delegate to the wrapped function."""
        return self._fn(symbol, candles, index)

    def reset(self) -> None:
        """No internal state to reset."""


class MovingAverageCrossSource:
    """Deterministic MA-cross decision source (tests and benchmarks).

    Emite ``open_long`` cuando la media rápida cruza por encima de la lenta y
    ``open_short`` cuando cruza por debajo. Solo dispara en el cruce, no en cada
    vela, para no depender del veto del Risk Manager.

    Args:
        fast: Periodo de la media rápida.
        slow: Periodo de la media lenta.
        allow_short: Si emite señales cortas (además de largas).
    """

    def __init__(self, *, fast: int = 5, slow: int = 20, allow_short: bool = True) -> None:
        if fast >= slow:
            raise ValueError("fast debe ser menor que slow")
        self._fast = fast
        self._slow = slow
        self._allow_short = allow_short
        self._last_side: str | None = None

    def reset(self) -> None:
        """Forget the last emitted side."""
        self._last_side = None

    def decide(
        self, symbol: str, candles: Sequence[Candle], index: int
    ) -> DecisionGenerated | None:
        """Emit a decision when the fast/slow SMA relationship flips."""
        if index + 1 < self._slow:
            return None
        closes = [c.close for c in candles[: index + 1]]
        fast = sma(closes, self._fast)
        slow = sma(closes, self._slow)
        if fast is None or slow is None:
            return None
        side = "up" if fast > slow else "down"
        if side == self._last_side:
            return None
        self._last_side = side
        if side == "up":
            return open_decision(symbol, "open_long", summary="MA cross up")
        if self._allow_short:
            return open_decision(symbol, "open_short", summary="MA cross down")
        return None
