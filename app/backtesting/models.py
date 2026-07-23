"""Modelos de datos del laboratorio de backtesting (Fase 6).

Estructuras inmutables y JSON-serializables que describen la configuración de
un backtest y su resultado (curva de equity, operaciones y estadística). Las
operaciones cerradas reutilizan el :class:`~app.execution.models.TradeRecord`
del motor de ejecución — no se inventa un modelo de trade paralelo.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.execution.models import TradeRecord
from app.utils.time import isoformat_utc


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """Un punto de la curva de equity, muestreado por vela.

    Attributes:
        timestamp: Momento (UTC) de la muestra.
        equity: Equity total (balance + PnL flotante).
        balance: Efectivo realizado.
        drawdown_pct: Drawdown respecto al equity pico, en porcentaje.
        open_positions: Nº de posiciones abiertas en ese instante.
    """

    timestamp: datetime
    equity: float
    balance: float
    drawdown_pct: float
    open_positions: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "timestamp": isoformat_utc(self.timestamp),
            "equity": round(self.equity, 6),
            "balance": round(self.balance, 6),
            "drawdown_pct": round(self.drawdown_pct, 4),
            "open_positions": self.open_positions,
        }


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Parámetros de un backtest concreto.

    Attributes:
        symbol: Símbolo a simular.
        timeframe: Timeframe de las velas del dataset (p. ej. ``1m``).
        initial_balance: Balance inicial de la cartera simulada.
        spread_bps: Spread sintético en bps si el dataset solo trae OHLCV.
        label: Etiqueta legible del experimento.
        parameters: Parámetros de estrategia/ejecución aplicados (para el log).
        start: Inicio efectivo del rango simulado (informativo).
        end: Fin efectivo del rango simulado (informativo).
    """

    symbol: str
    timeframe: str = "1m"
    initial_balance: float = 10_000.0
    spread_bps: float = 2.0
    label: str = "backtest"
    parameters: dict[str, Any] = field(default_factory=dict)
    start: datetime | None = None
    end: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "initial_balance": self.initial_balance,
            "spread_bps": self.spread_bps,
            "label": self.label,
            "parameters": self.parameters,
            "start": isoformat_utc(self.start) if self.start else None,
            "end": isoformat_utc(self.end) if self.end else None,
        }


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Resultado completo de un backtest.

    Attributes:
        config: Configuración usada.
        trades: Operaciones cerradas (cronológico de cierre).
        equity_curve: Curva de equity muestreada por vela.
        statistics: Estadística cuantitativa (dict de ``QuantStatistics``).
        bars: Nº de velas procesadas.
        final_equity: Equity al terminar.
        final_balance: Balance al terminar.
    """

    config: BacktestConfig
    trades: list[TradeRecord]
    equity_curve: list[EquityPoint]
    statistics: dict[str, Any]
    bars: int
    final_equity: float
    final_balance: float

    @property
    def net_profit(self) -> float:
        """PnL neto total sobre el balance inicial."""
        return self.final_balance - self.config.initial_balance

    @property
    def return_pct(self) -> float:
        """Rendimiento total en porcentaje sobre el balance inicial."""
        base = self.config.initial_balance
        return (self.net_profit / base * 100.0) if base else 0.0

    def to_dict(self, *, include_trades: bool = True) -> dict[str, Any]:
        """JSON-safe dict.

        Args:
            include_trades: Si ``False``, omite la lista de operaciones (útil
                para resúmenes compactos como los de la optimización).

        Returns:
            Diccionario serializable con configuración, curva y estadística.
        """
        payload: dict[str, Any] = {
            "config": self.config.to_dict(),
            "bars": self.bars,
            "final_equity": round(self.final_equity, 6),
            "final_balance": round(self.final_balance, 6),
            "net_profit": round(self.net_profit, 6),
            "return_pct": round(self.return_pct, 4),
            "statistics": self.statistics,
            "equity_curve": [point.to_dict() for point in self.equity_curve],
        }
        if include_trades:
            payload["trades"] = [trade.to_dict() for trade in self.trades]
        return payload
