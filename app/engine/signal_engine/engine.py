"""Signal Engine: agrupa, valida, deduplica, prioriza y expira señales.

Es la antesala del Decision Engine: solo las señales estructuralmente
válidas y no duplicadas quedan "activas" por símbolo.
"""

import logging
from typing import Any

from app.core.events.bus import EventBus
from app.core.events.events import SignalCreated
from app.core.exceptions import EventBusError
from app.engine.events import SignalExpired, SignalRejected
from app.engine.models import Direction, SignalStatus, StrategySignal
from app.engine.state_manager import SignalHistoryStore
from app.engine.validators import SignalValidator
from app.utils.time import utc_now


class SignalEngine:
    """Live-signal registry per symbol with validation and dedupe.

    Args:
        validator: Validación estructural.
        history: Historial (nada se pierde).
        bus: Event Bus (``None`` en tests puros).
        signal_ttl_seconds: Vida por defecto si la señal no trae expiración.
        dedupe_window_seconds: Ventana de deduplicación por
            (estrategia, símbolo, dirección).
    """

    def __init__(
        self,
        validator: SignalValidator,
        history: SignalHistoryStore,
        bus: EventBus | None = None,
        *,
        signal_ttl_seconds: float = 300.0,
        dedupe_window_seconds: float = 60.0,
    ) -> None:
        self._validator = validator
        self._history = history
        self._bus = bus
        self._ttl = signal_ttl_seconds
        self._dedupe_window = dedupe_window_seconds
        self._active: dict[str, list[StrategySignal]] = {}
        self._log = logging.getLogger("app.engine.signals")

    # ------------------------------------------------------------------
    # Ingreso
    # ------------------------------------------------------------------

    async def submit(self, signal: StrategySignal) -> bool:
        """Validate and admit a signal into the active set.

        Args:
            signal: Señal recién emitida por una estrategia.

        Returns:
            ``True`` si quedó activa; ``False`` si fue rechazada.
        """
        now = utc_now()
        problems = self._validator.validate(signal, now=now)
        if problems:
            self._history.record_signal(
                signal, SignalStatus.REJECTED, tuple(problems), resolved_at=now
            )
            await self._publish(
                SignalRejected(
                    source="signal_engine",
                    strategy=signal.strategy_name,
                    symbol=signal.symbol,
                    reasons="; ".join(problems),
                    stage="validation",
                )
            )
            return False

        self.expire_stale(now=now)
        active = self._active.setdefault(signal.symbol, [])

        duplicate = self._find_duplicate(active, signal)
        if duplicate is not None:
            # La señal nueva reemplaza a la vieja de la misma estrategia y
            # dirección (supersede); la vieja queda en el historial.
            active.remove(duplicate)
            self._history.record_signal(
                duplicate,
                SignalStatus.SUPERSEDED,
                (f"reemplazada por {signal.signal_id}",),
                resolved_at=now,
            )

        active.append(signal)
        active.sort(key=lambda s: s.priority, reverse=True)
        await self._publish(
            SignalCreated(
                source="signal_engine",
                strategy=signal.strategy_name,
                symbol=signal.symbol,
                direction=signal.direction.value,
                confidence=signal.confidence,
                metadata={"signal_id": signal.signal_id, "score": signal.score},
            )
        )
        return True

    def _find_duplicate(
        self, active: list[StrategySignal], signal: StrategySignal
    ) -> StrategySignal | None:
        """Duplicate = misma estrategia, símbolo y dirección, aún vigente."""
        for existing in active:
            if (
                existing.strategy_name == signal.strategy_name
                and existing.direction is signal.direction
                and (signal.timestamp - existing.timestamp).total_seconds() <= self._dedupe_window
            ):
                return existing
        # Fuera de ventana pero sin expirar: también supersede.
        for existing in active:
            if (
                existing.strategy_name == signal.strategy_name
                and existing.direction is signal.direction
            ):
                return existing
        return None

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    def active_signals(self, symbol: str) -> list[StrategySignal]:
        """Active signals for a symbol, priority-ordered (expira primero)."""
        self.expire_stale()
        return list(self._active.get(symbol.upper(), []))

    def symbols_with_signals(self) -> list[str]:
        """Symbols that currently hold active signals."""
        self.expire_stale()
        return sorted(symbol for symbol, signals in self._active.items() if signals)

    def detect_conflict(self, symbol: str) -> bool:
        """Whether active signals disagree in direction for a symbol."""
        directions = {
            s.direction
            for s in self._active.get(symbol.upper(), [])
            if s.direction is not Direction.NEUTRAL
        }
        return len(directions) > 1

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def expire_stale(self, now: Any = None) -> int:
        """Expire signals past their TTL/expiration.

        Returns:
            Cuántas señales expiraron.
        """
        moment = now or utc_now()
        expired = 0
        for symbol, signals in self._active.items():
            keep: list[StrategySignal] = []
            for signal in signals:
                deadline = signal.expiration
                if deadline is None:
                    age = (moment - signal.timestamp).total_seconds()
                    is_expired = age > self._ttl
                else:
                    is_expired = moment >= deadline
                if not is_expired:
                    keep.append(signal)
                    continue
                expired += 1
                record = self._history.record_signal(
                    signal, SignalStatus.EXPIRED, ("TTL alcanzado",), resolved_at=moment
                )
                self._publish_nowait(
                    SignalExpired(
                        source="signal_engine",
                        strategy=signal.strategy_name,
                        symbol=symbol,
                        signal_id=signal.signal_id,
                        lifetime_seconds=record.lifetime_seconds or 0.0,
                    )
                )
            self._active[symbol] = keep
        return expired

    def consume(self, symbol: str, status: SignalStatus, reasons: tuple[str, ...]) -> None:
        """Resolve every active signal of a symbol (tras una decisión).

        Args:
            symbol: Símbolo decidido.
            status: Estado final (ACCEPTED/REJECTED).
            reasons: Razones de la resolución.
        """
        now = utc_now()
        for signal in self._active.pop(symbol.upper(), []):
            self._history.record_signal(signal, status, reasons, resolved_at=now)

    def status(self) -> dict[str, Any]:
        """Diagnostic snapshot."""
        return {
            "active_by_symbol": {
                symbol: len(signals) for symbol, signals in self._active.items() if signals
            },
            "conflicts": [s for s in self._active if self.detect_conflict(s)],
        }

    # ------------------------------------------------------------------
    # Publicación tolerante
    # ------------------------------------------------------------------

    async def _publish(self, event: Any) -> None:
        """Publish tolerating a stopped/saturated bus."""
        if self._bus is None:
            return
        try:
            await self._bus.publish(event)
        except EventBusError as exc:
            self._log.warning("Event publish failed: %s", exc)

    def _publish_nowait(self, event: Any) -> None:
        """Best-effort publish from sync context."""
        if self._bus is None:
            return
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._publish(event))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)


_BACKGROUND: set[Any] = set()
