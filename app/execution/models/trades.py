"""Registro de operación cerrada para el Trade Journal.

Cada operación cerrada produce un :class:`TradeRecord` inmutable con todo el
contexto necesario para auditar y analizar: precios, costes, PnL, ATR,
volatilidad, régimen, score, confianza y razones de entrada/salida.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.execution.models.enums import ExitReason, PositionSide
from app.utils.time import utc_now


def _new_id() -> str:
    """Unique trade identifier."""
    return uuid.uuid4().hex


def _iso(moment: datetime | None) -> str | None:
    """ISO-8601 or ``None`` passthrough."""
    return None if moment is None else moment.isoformat()


@dataclass(frozen=True, kw_only=True, slots=True)
class TradeRecord:
    """A fully-closed trade, ready to be journalled.

    Attributes:
        trade_id: UUID de la operación.
        position_id: Posición de origen.
        symbol: Activo.
        side: Dirección (LONG/SHORT).
        quantity: Cantidad operada.
        entry_time: Hora de entrada.
        exit_time: Hora de salida.
        entry_price: Precio de entrada.
        exit_price: Precio de salida.
        stop_loss: Stop de referencia.
        take_profit: Objetivo de referencia.
        commission: Comisión total.
        slippage_bps: Slippage medio en bps.
        spread_bps: Spread medio en bps.
        pnl: PnL neto (tras comisiones).
        pnl_gross: PnL bruto (sin comisiones).
        r_multiple: Resultado en múltiplos de R.
        return_pct: Rendimiento sobre el nocional de entrada.
        atr: ATR al abrir.
        volatility: Estado de volatilidad al abrir.
        regime: Régimen al abrir.
        strategy: Estrategia dominante de la decisión de origen ("" si no
            se pudo atribuir: decisiones antiguas o posiciones adoptadas).
        strategy_category: Categoría de esa estrategia (smc, breakout, …).
        score: Score de la decisión.
        confidence: Confianza de la decisión.
        exit_reason: Motivo de salida.
        entry_reasons: Razones de entrada.
        exit_reasons: Razones de salida.
        decision_id: Decisión de origen.
        context_snapshot: Captura de contexto (estructura preparada).
    """

    trade_id: str = field(default_factory=_new_id)
    position_id: str
    symbol: str
    side: PositionSide
    quantity: float
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    commission: float = 0.0
    slippage_bps: float = 0.0
    spread_bps: float = 0.0
    pnl: float = 0.0
    pnl_gross: float = 0.0
    r_multiple: float = 0.0
    return_pct: float = 0.0
    atr: float | None = None
    volatility: str = "normal"
    regime: str = "unknown"
    strategy: str = ""
    strategy_category: str = ""
    score: float = 0.0
    confidence: float = 0.0
    exit_reason: ExitReason = ExitReason.MANUAL
    entry_reasons: tuple[str, ...] = ()
    exit_reasons: tuple[str, ...] = ()
    decision_id: str | None = None
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    recorded_at: datetime = field(default_factory=utc_now)

    @property
    def duration_seconds(self) -> float:
        """Trade duration in seconds."""
        return (self.exit_time - self.entry_time).total_seconds()

    @property
    def is_win(self) -> bool:
        """Whether the trade closed with a positive net PnL."""
        return self.pnl > 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TradeRecord":
        """Rebuild a trade from one journal line (inverse of :meth:`to_dict`).

        Vive en el modelo para que el Trade Journal pueda releerse a sí mismo
        sin depender de la capa de producción (Fase 9), que puede estar apagada.

        Args:
            data: Una entrada JSONL del Trade Journal ya parseada.

        Returns:
            La operación reconstruida.

        Raises:
            KeyError: Si falta un campo obligatorio.
            ValueError: Si algún campo no se puede convertir.
        """

        def opt(value: Any) -> float | None:
            return None if value is None else float(value)

        return cls(
            trade_id=str(data["trade_id"]),
            position_id=str(data["position_id"]),
            symbol=str(data["symbol"]),
            side=PositionSide(data["side"]),
            quantity=float(data["quantity"]),
            entry_time=datetime.fromisoformat(str(data["entry_time"])),
            exit_time=datetime.fromisoformat(str(data["exit_time"])),
            entry_price=float(data["entry_price"]),
            exit_price=float(data["exit_price"]),
            stop_loss=opt(data.get("stop_loss")),
            take_profit=opt(data.get("take_profit")),
            commission=float(data.get("commission", 0.0)),
            slippage_bps=float(data.get("slippage_bps", 0.0)),
            spread_bps=float(data.get("spread_bps", 0.0)),
            pnl=float(data.get("pnl", 0.0)),
            pnl_gross=float(data.get("pnl_gross", 0.0)),
            r_multiple=float(data.get("r_multiple", 0.0)),
            return_pct=float(data.get("return_pct", 0.0)),
            atr=opt(data.get("atr")),
            volatility=str(data.get("volatility", "normal")),
            regime=str(data.get("regime", "unknown")),
            strategy=str(data.get("strategy", "")),
            strategy_category=str(data.get("strategy_category", "")),
            score=float(data.get("score", 0.0)),
            confidence=float(data.get("confidence", 0.0)),
            exit_reason=ExitReason(data.get("exit_reason", ExitReason.MANUAL.value)),
            entry_reasons=tuple(data.get("entry_reasons", ())),
            exit_reasons=tuple(data.get("exit_reasons", ())),
            decision_id=data.get("decision_id"),
            context_snapshot=dict(data.get("context_snapshot", {})),
            recorded_at=datetime.fromisoformat(str(data["recorded_at"])),
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "trade_id": self.trade_id,
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "entry_time": _iso(self.entry_time),
            "exit_time": _iso(self.exit_time),
            "duration_seconds": self.duration_seconds,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "commission": self.commission,
            "slippage_bps": self.slippage_bps,
            "spread_bps": self.spread_bps,
            "pnl": self.pnl,
            "pnl_gross": self.pnl_gross,
            "r_multiple": self.r_multiple,
            "return_pct": self.return_pct,
            "is_win": self.is_win,
            "atr": self.atr,
            "volatility": self.volatility,
            "regime": self.regime,
            "strategy": self.strategy,
            "strategy_category": self.strategy_category,
            "score": self.score,
            "confidence": self.confidence,
            "exit_reason": self.exit_reason.value,
            "entry_reasons": list(self.entry_reasons),
            "exit_reasons": list(self.exit_reasons),
            "decision_id": self.decision_id,
            "context_snapshot": self.context_snapshot,
            "recorded_at": _iso(self.recorded_at),
        }
