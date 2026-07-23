"""Position Manager: posiciones abiertas/cerradas y su gestión dinámica.

Controla el ciclo de vida de cada posición: apertura, valoración (mark),
break-even, trailing stop, y detección de salida (stop, objetivo, tiempo).
No ejecuta órdenes: decide *qué* debe pasar; el Execution Engine ejecuta.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from app.execution.models import (
    ExitReason,
    Fill,
    OrderSide,
    Position,
    PositionSide,
    PositionStatus,
)
from app.utils.time import utc_now


@dataclass(frozen=True, slots=True)
class StopUpdate:
    """Un movimiento de stop decidido por la gestión dinámica."""

    kind: str  # break_even | trailing
    previous: float | None
    current: float


class PositionManager:
    """Track and manage the lifecycle of positions.

    Args:
        break_even_r: R en que el stop salta a break-even (0 lo desactiva).
        trailing_enabled: Si el trailing stop está activo.
        trailing_atr_multiple: Distancia del trailing en múltiplos de ATR.
    """

    def __init__(
        self,
        *,
        break_even_r: float = 1.0,
        trailing_enabled: bool = True,
        trailing_atr_multiple: float = 2.0,
    ) -> None:
        self._break_even_r = break_even_r
        self._trailing_enabled = trailing_enabled
        self._trailing_atr_multiple = trailing_atr_multiple
        self._open: dict[str, Position] = {}
        self._closed: list[Position] = []

    def restore(self, positions: Iterable[Position]) -> int:
        """Re-adopt open positions recovered after a restart (Fase 9).

        Args:
            positions: Posiciones abiertas rehidratadas desde el snapshot.

        Returns:
            Cuántas quedaron bajo gestión.
        """
        restored = 0
        for position in positions:
            self._open[position.position_id] = position
            restored += 1
        return restored

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    @property
    def open_positions(self) -> list[Position]:
        """Snapshot of the currently open positions."""
        return list(self._open.values())

    @property
    def closed_positions(self) -> list[Position]:
        """History of closed positions."""
        return list(self._closed)

    def get(self, position_id: str) -> Position | None:
        """Return an open position by id (or ``None``)."""
        return self._open.get(position_id)

    def positions_for(self, symbol: str) -> list[Position]:
        """Open positions on a symbol."""
        symbol = symbol.upper()
        return [p for p in self._open.values() if p.symbol == symbol]

    def has_open(self, symbol: str) -> bool:
        """Whether there is at least one open position on the symbol."""
        return bool(self.positions_for(symbol))

    def total_exposure(self) -> float:
        """Total nominal exposure of open positions."""
        return sum(p.notional for p in self._open.values())

    def symbol_exposure(self, symbol: str) -> float:
        """Nominal exposure on a single symbol."""
        return sum(p.notional for p in self.positions_for(symbol))

    def open_risk(self) -> float:
        """Aggregate currency still at risk to the stops of open positions."""
        total = 0.0
        for p in self._open.values():
            if p.stop_loss is not None:
                total += abs(p.mark_price - p.stop_loss) * p.quantity
        return total

    # ------------------------------------------------------------------
    # Apertura
    # ------------------------------------------------------------------

    def open(
        self,
        fill: Fill,
        *,
        stop_loss: float | None,
        take_profit: float | None,
        decision_id: str | None = None,
        regime: str = "unknown",
        score: float = 0.0,
        confidence: float = 0.0,
        entry_reasons: tuple[str, ...] = (),
        atr: float | None = None,
        volatility: str = "normal",
    ) -> Position:
        """Open a position from an execution fill.

        Args:
            fill: Ejecución de entrada.
            stop_loss: Stop inicial.
            take_profit: Objetivo inicial.
            decision_id: Decisión de origen.
            regime: Régimen al abrir.
            score: Score de la decisión.
            confidence: Confianza de la decisión.
            entry_reasons: Razones de entrada.
            atr: ATR al abrir (para trailing y journal).
            volatility: Estado de volatilidad al abrir.

        Returns:
            La posición recién abierta y registrada.
        """
        side = PositionSide.LONG if fill.side is OrderSide.BUY else PositionSide.SHORT
        position = Position(
            symbol=fill.symbol,
            side=side,
            quantity=fill.quantity,
            initial_quantity=fill.quantity,
            entry_price=fill.price,
            initial_stop=stop_loss,
            stop_loss=stop_loss,
            take_profit=take_profit,
            opened_at=fill.executed_at,
            mark_price=fill.price,
            commission_paid=fill.commission,
            decision_id=decision_id,
            regime=regime,
            score=score,
            confidence=confidence,
            entry_reasons=entry_reasons,
            metadata={
                "atr": atr,
                "volatility": volatility,
                "entry_slippage_bps": fill.slippage_bps,
            },
        )
        self._open[position.position_id] = position
        return position

    # ------------------------------------------------------------------
    # Gestión dinámica
    # ------------------------------------------------------------------

    def update_mark(self, position: Position, mark: float) -> None:
        """Update the mark price (and price extremes) of a position."""
        position.update_mark(mark)

    def manage(self, position: Position, atr: float | None = None) -> list[StopUpdate]:
        """Apply break-even and trailing rules at the current mark.

        Args:
            position: Posición a gestionar (mark ya actualizado).
            atr: ATR actual (necesario para el trailing).

        Returns:
            Lista de movimientos de stop aplicados (para eventos/notificaciones).
        """
        updates: list[StopUpdate] = []
        update = self._apply_break_even(position)
        if update is not None:
            updates.append(update)
        trailing = self._apply_trailing(position, atr)
        if trailing is not None:
            updates.append(trailing)
        return updates

    def _apply_break_even(self, position: Position) -> StopUpdate | None:
        """Move the stop to entry once the position reaches +break_even_r."""
        if self._break_even_r <= 0 or position.break_even_active:
            return None
        if position.risk_per_unit <= 0:
            return None
        if position.r_multiple() < self._break_even_r:
            return None
        previous = position.stop_loss
        position.stop_loss = position.entry_price
        position.break_even_active = True
        return StopUpdate(kind="break_even", previous=previous, current=position.entry_price)

    def _apply_trailing(self, position: Position, atr: float | None) -> StopUpdate | None:
        """Trail the stop by ``trailing_atr_multiple × ATR`` in the favour direction."""
        if not self._trailing_enabled or atr is None or atr <= 0:
            return None
        distance = atr * self._trailing_atr_multiple
        if position.is_long:
            candidate = position.highest_price - distance
            if position.stop_loss is None or candidate > position.stop_loss:
                previous = position.stop_loss
                position.stop_loss = round(candidate, 8)
                position.trailing_active = True
                return StopUpdate("trailing", previous, position.stop_loss)
        else:
            candidate = position.lowest_price + distance
            if position.stop_loss is None or candidate < position.stop_loss:
                previous = position.stop_loss
                position.stop_loss = round(candidate, 8)
                position.trailing_active = True
                return StopUpdate("trailing", previous, position.stop_loss)
        return None

    def check_exit(self, position: Position) -> ExitReason | None:
        """Return the exit reason triggered at the current mark, if any."""
        mark = position.mark_price
        stop = position.stop_loss
        target = position.take_profit
        if position.is_long:
            if stop is not None and mark <= stop:
                return self._stop_reason(position)
            if target is not None and mark >= target:
                return ExitReason.TAKE_PROFIT
        else:
            if stop is not None and mark >= stop:
                return self._stop_reason(position)
            if target is not None and mark <= target:
                return ExitReason.TAKE_PROFIT
        return None

    @staticmethod
    def _stop_reason(position: Position) -> ExitReason:
        """Classify a stop hit as break-even, trailing or plain stop-loss."""
        if position.trailing_active:
            return ExitReason.TRAILING_STOP
        if position.break_even_active:
            return ExitReason.BREAK_EVEN
        return ExitReason.STOP_LOSS

    # ------------------------------------------------------------------
    # Cierre
    # ------------------------------------------------------------------

    def close(
        self,
        position: Position,
        *,
        exit_price: float,
        close_commission: float,
        reason: ExitReason,
        exit_reasons: tuple[str, ...] = (),
    ) -> float:
        """Close a position and move it to history.

        Args:
            position: Posición abierta a cerrar.
            exit_price: Precio de salida ejecutado.
            close_commission: Comisión del cierre.
            reason: Motivo de salida.
            exit_reasons: Explicación textual de la salida.

        Returns:
            El PnL bruto del cierre (sin comisiones).
        """
        gross_pnl = (
            (exit_price - position.entry_price) * position.quantity * position.direction_sign
        )
        position.update_mark(exit_price)
        position.commission_paid += close_commission
        position.realized_pnl = gross_pnl - position.commission_paid
        position.status = PositionStatus.CLOSED
        position.closed_at = utc_now()
        position.exit_reason = reason
        position.exit_reasons = exit_reasons
        self._open.pop(position.position_id, None)
        self._closed.append(position)
        return gross_pnl

    def status(self) -> dict[str, object]:
        """Compact status for diagnostics/dashboard."""
        return {
            "open": len(self._open),
            "closed": len(self._closed),
            "exposure": round(self.total_exposure(), 6),
            "open_risk": round(self.open_risk(), 6),
            "break_even_r": self._break_even_r,
            "trailing_enabled": self._trailing_enabled,
            "trailing_atr_multiple": self._trailing_atr_multiple,
        }
