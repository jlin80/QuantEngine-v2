"""Serialización del estado vivo del motor y su almacén atómico.

Ojo con una trampa: ``Position.to_dict()`` y ``TradeRecord.to_dict()`` existen
desde la Fase 5, pero son vistas **para el dashboard**, no formatos de
round-trip. ``Position.to_dict()`` incluye campos derivados (``unrealized_pnl``,
``notional``, ``holding_seconds``) y omite ``highest_price``/``lowest_price``,
que el trailing stop necesita: rehidratar desde ahí perdería el trailing.

Por eso la recuperación tiene su propia serialización, explícita y simétrica.
``TradeRecord`` sí se puede reconstruir desde su ``to_dict()`` porque todos sus
campos de constructor están presentes.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.execution.models import ExitReason, Position, PositionSide, PositionStatus, TradeRecord
from app.utils.time import utc_now

_log = logging.getLogger("app.production.recovery")


def position_to_state(position: Position) -> dict[str, Any]:
    """Serialize a position losslessly for later rehydration.

    Args:
        position: Open position to capture.

    Returns:
        A JSON-safe mapping accepted by :func:`position_from_state`.
    """
    return {
        "position_id": position.position_id,
        "symbol": position.symbol,
        "side": position.side.value,
        "quantity": position.quantity,
        "initial_quantity": position.initial_quantity,
        # Sin esto, una posicion de oro restaurada tras un reinicio perderia su
        # contract_size y su PnL volveria a calcularse 100x menor.
        "contract_size": position.contract_size,
        "entry_price": position.entry_price,
        "initial_stop": position.initial_stop,
        "stop_loss": position.stop_loss,
        "take_profit": position.take_profit,
        "opened_at": position.opened_at.isoformat(),
        "status": position.status.value,
        "realized_pnl": position.realized_pnl,
        "commission_paid": position.commission_paid,
        "break_even_active": position.break_even_active,
        "trailing_active": position.trailing_active,
        "mark_price": position.mark_price,
        "highest_price": position.highest_price,
        "lowest_price": position.lowest_price,
        "decision_id": position.decision_id,
        # La atribución y las señales de origen tienen que sobrevivir al
        # reinicio: sin ellas la posición restaurada cae al holding global (en
        # vez del suyo por estrategia) y la operación resultante llega al
        # journal sin nada que unir con el evaluador continuo.
        "signal_ids": list(position.signal_ids),
        "strategy": position.strategy,
        "strategy_category": position.strategy_category,
        "regime": position.regime,
        "score": position.score,
        "confidence": position.confidence,
        "entry_reasons": list(position.entry_reasons),
        "metadata": dict(position.metadata),
    }


def position_from_state(data: dict[str, Any]) -> Position:
    """Rebuild a position captured by :func:`position_to_state`.

    Args:
        data: Serialized position.

    Returns:
        The reconstructed position.

    Raises:
        KeyError: If a required field is missing.
        ValueError: If a field cannot be parsed.
    """
    return Position(
        position_id=str(data["position_id"]),
        symbol=str(data["symbol"]),
        side=PositionSide(data["side"]),
        quantity=float(data["quantity"]),
        initial_quantity=float(data["initial_quantity"]),
        contract_size=float(data.get("contract_size", 1.0)),
        entry_price=float(data["entry_price"]),
        initial_stop=_opt_float(data.get("initial_stop")),
        stop_loss=_opt_float(data.get("stop_loss")),
        take_profit=_opt_float(data.get("take_profit")),
        opened_at=datetime.fromisoformat(str(data["opened_at"])),
        status=PositionStatus(data.get("status", PositionStatus.OPEN.value)),
        realized_pnl=float(data.get("realized_pnl", 0.0)),
        commission_paid=float(data.get("commission_paid", 0.0)),
        break_even_active=bool(data.get("break_even_active", False)),
        trailing_active=bool(data.get("trailing_active", False)),
        mark_price=float(data.get("mark_price", 0.0)),
        highest_price=float(data.get("highest_price", 0.0)),
        lowest_price=float(data.get("lowest_price", 0.0)),
        decision_id=data.get("decision_id"),
        signal_ids=tuple(str(s) for s in data.get("signal_ids", ())),
        strategy=str(data.get("strategy", "")),
        strategy_category=str(data.get("strategy_category", "")),
        regime=str(data.get("regime", "unknown")),
        score=float(data.get("score", 0.0)),
        confidence=float(data.get("confidence", 0.0)),
        entry_reasons=tuple(data.get("entry_reasons", ())),
        metadata=dict(data.get("metadata", {})),
    )


def trade_from_dict(data: dict[str, Any]) -> TradeRecord:
    """Rebuild a trade record from a journal line.

    Args:
        data: One parsed JSONL entry of the Trade Journal.

    Returns:
        The reconstructed trade.

    Raises:
        KeyError: If a required field is missing.
        ValueError: If a field cannot be parsed.
    """
    return TradeRecord(
        trade_id=str(data["trade_id"]),
        position_id=str(data["position_id"]),
        symbol=str(data["symbol"]),
        side=PositionSide(data["side"]),
        quantity=float(data["quantity"]),
        contract_size=float(data.get("contract_size", 1.0)),
        entry_time=datetime.fromisoformat(str(data["entry_time"])),
        exit_time=datetime.fromisoformat(str(data["exit_time"])),
        entry_price=float(data["entry_price"]),
        exit_price=float(data["exit_price"]),
        stop_loss=_opt_float(data.get("stop_loss")),
        take_profit=_opt_float(data.get("take_profit")),
        commission=float(data.get("commission", 0.0)),
        slippage_bps=float(data.get("slippage_bps", 0.0)),
        spread_bps=float(data.get("spread_bps", 0.0)),
        pnl=float(data.get("pnl", 0.0)),
        pnl_gross=float(data.get("pnl_gross", 0.0)),
        r_multiple=float(data.get("r_multiple", 0.0)),
        return_pct=float(data.get("return_pct", 0.0)),
        atr=_opt_float(data.get("atr")),
        volatility=str(data.get("volatility", "normal")),
        regime=str(data.get("regime", "unknown")),
        score=float(data.get("score", 0.0)),
        confidence=float(data.get("confidence", 0.0)),
        exit_reason=ExitReason(data.get("exit_reason", ExitReason.MANUAL.value)),
        entry_reasons=tuple(data.get("entry_reasons", ())),
        exit_reasons=tuple(data.get("exit_reasons", ())),
        decision_id=data.get("decision_id"),
        signal_ids=tuple(str(x) for x in data.get("signal_ids", ())),
        context_snapshot=dict(data.get("context_snapshot", {})),
        recorded_at=datetime.fromisoformat(str(data["recorded_at"])),
    )


@dataclass(frozen=True, slots=True)
class EngineState:
    """Everything needed to resume operation after a restart.

    Attributes:
        captured_at: UTC moment of the capture.
        positions: Serialized open positions.
        balance: Portfolio cash balance.
        peak_equity: Running equity peak (drawdown baseline).
        total_trades: Closed trade count.
        wins: Winning trade count.
        losses: Losing trade count.
        commission_paid: Accumulated commission.
        consecutive_losses: Current losing streak (risk state).
        safe_mode_active: Whether Safe Mode was engaged.
        kill_switch_active: Whether the kill switch was engaged.
    """

    captured_at: datetime = field(default_factory=utc_now)
    positions: list[dict[str, Any]] = field(default_factory=list)
    balance: float = 0.0
    peak_equity: float = 0.0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    commission_paid: float = 0.0
    consecutive_losses: int = 0
    safe_mode_active: bool = False
    kill_switch_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "captured_at": self.captured_at.isoformat(),
            "positions": self.positions,
            "balance": self.balance,
            "peak_equity": self.peak_equity,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "commission_paid": self.commission_paid,
            "consecutive_losses": self.consecutive_losses,
            "safe_mode_active": self.safe_mode_active,
            "kill_switch_active": self.kill_switch_active,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EngineState":
        """Rebuild the state from its persisted form.

        Args:
            data: Mapping produced by :meth:`to_dict`.

        Returns:
            The reconstructed state.

        Raises:
            KeyError: If ``captured_at`` is missing.
            ValueError: If a field cannot be parsed.
        """
        return cls(
            captured_at=datetime.fromisoformat(str(data["captured_at"])),
            positions=list(data.get("positions", [])),
            balance=float(data.get("balance", 0.0)),
            peak_equity=float(data.get("peak_equity", 0.0)),
            total_trades=int(data.get("total_trades", 0)),
            wins=int(data.get("wins", 0)),
            losses=int(data.get("losses", 0)),
            commission_paid=float(data.get("commission_paid", 0.0)),
            consecutive_losses=int(data.get("consecutive_losses", 0)),
            safe_mode_active=bool(data.get("safe_mode_active", False)),
            kill_switch_active=bool(data.get("kill_switch_active", False)),
        )

    def age_seconds(self, *, now: datetime | None = None) -> float:
        """Seconds elapsed since the capture."""
        return ((now or utc_now()) - self.captured_at).total_seconds()


class StateSnapshotStore:
    """Atomically persisted engine state.

    La escritura es temporal + ``os.replace`` porque un snapshot a medio
    escribir es peor que ninguno: al reiniciar leeríamos JSON corrupto y
    perderíamos el estado entero. ``os.replace`` es atómico en Windows y POSIX.

    Args:
        path: JSON file backing the snapshot, or ``None`` for in-memory only.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._last: EngineState | None = None

    @property
    def last(self) -> EngineState | None:
        """Last state written or read by this store."""
        return self._last

    def save(self, state: EngineState) -> None:
        """Persist the state atomically (best effort).

        Args:
            state: State to persist.
        """
        self._last = state
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(self._path.suffix + ".tmp")
            temporary.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError as exc:
            _log.warning("No se pudo persistir el snapshot de estado: %r", exc)

    def load(self) -> EngineState | None:
        """Read the persisted state.

        Returns:
            The stored state, or ``None`` if absent or unreadable.
        """
        if self._path is None or not self._path.exists():
            return None
        try:
            state = EngineState.from_dict(json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _log.warning("Snapshot de estado ilegible, se ignora: %r", exc)
            return None
        self._last = state
        return state


def _opt_float(value: Any) -> float | None:
    """Coerce to float, preserving ``None``."""
    return None if value is None else float(value)
