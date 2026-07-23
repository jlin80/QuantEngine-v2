"""Broker de ejecución MetaTrader 5 (cuenta demo Exness).

Implementa :class:`~app.core.interfaces.broker.ExecutionBroker` enviando órdenes
reales al terminal MT5 con ``order_send``. Se selecciona **sólo** en modo ``demo``
(:data:`~app.execution.models.enums.ExecutionMode.DEMO`); nunca en ``live``.

Convención de cantidad: en modo demo, ``OrderRequest.quantity`` se interpreta como
**lotes** MT5 y se ajusta al ``volume_min``/``volume_max``/``volume_step`` del
símbolo. El resto del sistema (sizing, riesgo) razona en la misma unidad.
"""

from __future__ import annotations

import logging
from types import ModuleType
from typing import Any

from app.brokers.mt5.connection import MT5Connection
from app.execution.models import (
    BrokerExecution,
    Fill,
    OrderRequest,
    OrderSide,
    OrderType,
    RejectReason,
)
from app.execution.slippage import SlippageContext
from app.market.models import Ticker
from app.utils.time import utc_now

_log = logging.getLogger("app.brokers.mt5.broker")

# Retcode de éxito de MT5 (TRADE_RETCODE_DONE). Se usa como fallback si el módulo
# inyectado no lo expone (tests).
_RETCODE_DONE = 10009


class MT5Broker:
    """Venue de ejecución real contra una cuenta demo vía MetaTrader 5.

    Args:
        connection: Conexión gestionada al terminal MT5.
        deviation_points: Desviación máxima de precio tolerada (puntos) en órdenes
            a mercado antes de que el broker rechace por requote.
        magic: Identificador ``magic`` que etiqueta las órdenes de este motor.
    """

    def __init__(
        self,
        connection: MT5Connection,
        *,
        deviation_points: int = 20,
        magic: int = 777_001,
    ) -> None:
        self._conn = connection
        self._deviation = deviation_points
        self._magic = magic
        self._executed = 0
        self._rejected = 0

    @property
    def broker_name(self) -> str:
        """Short broker identifier."""
        return "mt5_demo"

    @property
    def stats(self) -> dict[str, int]:
        """Execution counters."""
        return {"executed": self._executed, "rejected": self._rejected}

    def healthcheck(self) -> bool:
        """Whether the MT5 terminal/account is reachable right now."""
        return self._conn.healthcheck()

    def execute(
        self,
        request: OrderRequest,
        ticker: Ticker,
        context: SlippageContext | None = None,
        *,
        allow_reject: bool = True,
        allow_partial: bool = True,
    ) -> BrokerExecution:
        """Envía la orden al terminal MT5 y traduce el resultado.

        Args:
            request: Intención de operar (``quantity`` = lotes en modo demo).
            ticker: Mejor bid/ask del símbolo (precio de referencia y sanity check).
            context: Contexto de slippage (ignorado: MT5 aplica su propio precio).
            allow_reject: Ignorado; un broker real siempre puede rechazar.
            allow_partial: Ignorado; el llenado lo decide MT5.

        Returns:
            Un :class:`BrokerExecution` con el fill real o el motivo del rechazo.
        """
        if request.quantity <= 0:
            return self._reject(RejectReason.INVALID_QUANTITY)
        if ticker.bid <= 0 or ticker.ask <= 0 or ticker.ask < ticker.bid:
            return self._reject(RejectReason.NO_MARKET_DATA)
        if not self._conn.connected:
            return self._reject(RejectReason.BROKER_UNAVAILABLE)

        mt5 = self._conn.mt5
        if request.order_type is not OrderType.MARKET:
            # De momento sólo órdenes a mercado; el resto se prepara en fases futuras.
            _log.warning("MT5Broker sólo soporta MARKET; recibido %s", request.order_type.value)
            return self._reject(RejectReason.BROKER_REJECTED)

        # El sistema usa el símbolo en mayúsculas; el terminal puede exponerlo con
        # otra caja (XAUUSDm). Se resuelve el nombre real para todas las llamadas.
        real_symbol = self._conn.resolve_symbol(request.symbol)
        volume = self._normalize_volume(mt5, real_symbol, request.quantity)
        if volume is None:
            return self._reject(RejectReason.INVALID_QUANTITY)

        is_buy = request.side is OrderSide.BUY
        price = ticker.ask if is_buy else ticker.bid
        order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL

        payload: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": real_symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": self._deviation,
            "magic": self._magic,
            "comment": (request.reason or "quantengine")[:31],
            "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
            "type_filling": self._filling_mode(mt5, real_symbol),
        }
        if request.stop_loss is not None:
            payload["sl"] = float(request.stop_loss)
        if request.take_profit is not None:
            payload["tp"] = float(request.take_profit)

        with self._conn.lock:
            result = mt5.order_send(payload)

        return self._interpret(result, request, ticker, volume, mt5)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _interpret(
        self,
        result: Any,
        request: OrderRequest,
        ticker: Ticker,
        requested_volume: float,
        mt5: ModuleType,
    ) -> BrokerExecution:
        """Traduce el resultado de ``order_send`` a un :class:`BrokerExecution`."""
        done = int(getattr(mt5, "TRADE_RETCODE_DONE", _RETCODE_DONE))
        if result is None:
            code, message = self._last_error(mt5)
            _log.error("order_send devolvió None: [%s] %s", code, message)
            return self._reject(RejectReason.BROKER_REJECTED)

        retcode = int(getattr(result, "retcode", -1))
        if retcode != done:
            comment = getattr(result, "comment", "")
            _log.warning(
                "MT5 rechazó %s %s vol=%s: retcode=%s %s",
                request.symbol,
                request.side.value,
                requested_volume,
                retcode,
                comment,
            )
            return self._reject(RejectReason.BROKER_REJECTED)

        fill_price = float(getattr(result, "price", 0.0)) or ticker.mid
        filled_volume = float(getattr(result, "volume", requested_volume)) or requested_volume
        self._executed += 1
        fill = Fill(
            request_id=request.request_id,
            symbol=request.symbol,
            side=request.side,
            quantity=filled_volume,
            requested_quantity=requested_volume,
            reference_price=ticker.mid,
            price=fill_price,
            spread_bps=ticker.spread_bps,
            commission=abs(float(getattr(result, "commission", 0.0) or 0.0)),
            liquidity="taker",
            executed_at=utc_now(),
        )
        return BrokerExecution(fill, RejectReason.NONE)

    def _normalize_volume(self, mt5: ModuleType, symbol: str, quantity: float) -> float | None:
        """Ajusta la cantidad (lotes) a los límites y el paso del símbolo.

        Returns:
            El volumen válido más cercano, o ``None`` si el símbolo no existe.
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            _log.error("Símbolo desconocido en MT5: %s", symbol)
            return None
        vmin = float(getattr(info, "volume_min", 0.01) or 0.01)
        vmax = float(getattr(info, "volume_max", 0.0) or 0.0)
        step = float(getattr(info, "volume_step", 0.01) or 0.01)
        volume = max(vmin, quantity)
        if vmax > 0:
            volume = min(vmax, volume)
        # Cuantiza al paso del símbolo (evita rechazos por volumen inválido).
        steps = round(volume / step)
        volume = round(steps * step, 8)
        return max(vmin, volume)

    def _filling_mode(self, mt5: ModuleType, symbol: str) -> int:
        """Elige un modo de llenado compatible con el símbolo (FOK/IOC/RETURN)."""
        info = mt5.symbol_info(symbol)
        default = getattr(mt5, "ORDER_FILLING_IOC", 1)
        if info is None:
            return int(default)
        return int(getattr(info, "filling_mode", default) or default)

    def _reject(self, reason: RejectReason) -> BrokerExecution:
        """Cuenta y devuelve un rechazo."""
        self._rejected += 1
        return BrokerExecution(None, reason)

    def _last_error(self, mt5: ModuleType) -> tuple[int, str]:
        """Return the terminal's ``last_error`` as ``(code, message)``."""
        try:
            err = mt5.last_error()
        except Exception:  # pragma: no cover - defensivo
            return (-1, "last_error no disponible")
        if isinstance(err, (tuple, list)) and len(err) >= 2:
            return int(err[0]), str(err[1])
        return (-1, str(err))
