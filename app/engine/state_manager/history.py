"""Historial de señales y decisiones + estado runtime del núcleo.

Nada se elimina: toda señal (aceptada, rechazada, expirada, superseded) y
toda decisión quedan en memoria (anillo grande) y se persisten a la base de
datos por lotes; si la DB cae, se derraman a JSONL.

También calcula el factor de rendimiento reciente por estrategia que
alimenta al Confidence Engine y al consenso dinámico.
"""

import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime

from app.engine.models import Decision, SignalRecord, SignalStatus, StrategySignal
from app.utils.time import utc_now


class SignalHistoryStore:
    """In-memory (ring) history of every signal and decision.

    Args:
        memory_limit: Máximo de registros retenidos en memoria por tipo.
        signal_sink: Callback opcional invocado con cada registro resuelto
            (lo usa el bootstrap para encolar la persistencia en el
            :class:`~app.engine.state_manager.writer.HistoryWriter`).
    """

    def __init__(
        self,
        *,
        memory_limit: int = 10_000,
        signal_sink: Callable[[SignalRecord], None] | None = None,
    ) -> None:
        self._signals: deque[SignalRecord] = deque(maxlen=memory_limit)
        self._decisions: deque[Decision] = deque(maxlen=memory_limit)
        self._by_strategy: dict[str, dict[str, int]] = {}
        self._runtime_state: dict[str, float] = {"daily_drawdown_pct": 0.0}
        self._signal_sink = signal_sink
        self._log = logging.getLogger("app.engine.history")

    # ------------------------------------------------------------------
    # Registro
    # ------------------------------------------------------------------

    def record_signal(
        self,
        signal: StrategySignal,
        status: SignalStatus,
        reasons: tuple[str, ...] = (),
        *,
        resolved_at: datetime | None = None,
    ) -> SignalRecord:
        """Append a signal with its lifecycle outcome.

        Args:
            signal: La señal.
            status: Resultado del ciclo de vida.
            reasons: Razones del estado (rechazo/expiración).
            resolved_at: Momento de resolución (default: ahora).

        Returns:
            El registro almacenado.
        """
        record = SignalRecord(
            signal=signal,
            status=status,
            status_reasons=reasons,
            recorded_at=signal.timestamp,
            resolved_at=resolved_at or utc_now(),
        )
        self._signals.append(record)
        counters = self._by_strategy.setdefault(
            signal.strategy_name, {status.value: 0 for status in SignalStatus}
        )
        counters[status.value] = counters.get(status.value, 0) + 1
        if self._signal_sink is not None:
            try:
                self._signal_sink(record)
            except Exception:  # un sink roto no debe bloquear el historial
                self._log.exception("Signal sink failed for %s", signal.signal_id)
        return record

    def record_decision(self, decision: Decision) -> None:
        """Append a decision (aceptada o no)."""
        self._decisions.append(decision)

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------

    def signals(
        self, *, limit: int = 100, status: SignalStatus | None = None
    ) -> list[SignalRecord]:
        """Most recent signal records (newest last)."""
        records = list(self._signals)
        if status is not None:
            records = [r for r in records if r.status is status]
        return records[-limit:]

    def decisions(self, *, limit: int = 100, symbol: str | None = None) -> list[Decision]:
        """Most recent decisions (newest last)."""
        items = list(self._decisions)
        if symbol is not None:
            upper = symbol.upper()
            items = [d for d in items if d.symbol == upper]
        return items[-limit:]

    def strategy_counters(self) -> dict[str, dict[str, int]]:
        """Signal counters per strategy and status."""
        return {name: dict(counters) for name, counters in self._by_strategy.items()}

    def performance_factor(self, strategy: str) -> float:
        """Recent-performance factor 0-1 (0.5 = neutro).

        Sin resultados de trading todavía (no hay ejecución en esta fase), el
        factor aproxima calidad por tasa de aceptación de señales; cuando
        exista PnL real (Fase 5) esta función pasará a usarlo.
        """
        counters = self._by_strategy.get(strategy)
        if not counters:
            return 0.5
        accepted = counters.get(SignalStatus.ACCEPTED.value, 0)
        rejected = counters.get(SignalStatus.REJECTED.value, 0)
        total = accepted + rejected
        if total < 5:
            return 0.5  # muestra insuficiente: neutro
        return max(0.0, min(1.0, accepted / total))

    # ------------------------------------------------------------------
    # Estado runtime (lo leen filtros como drawdown)
    # ------------------------------------------------------------------

    def set_state(self, key: str, value: float) -> None:
        """Set a runtime state value (p. ej. ``daily_drawdown_pct``)."""
        self._runtime_state[key] = value

    def get_state(self, key: str, default: float = 0.0) -> float:
        """Read a runtime state value."""
        return self._runtime_state.get(key, default)

    def status(self) -> dict[str, object]:
        """Diagnostic snapshot."""
        return {
            "signals_in_memory": len(self._signals),
            "decisions_in_memory": len(self._decisions),
            "strategy_counters": self.strategy_counters(),
            "runtime_state": dict(self._runtime_state),
        }
