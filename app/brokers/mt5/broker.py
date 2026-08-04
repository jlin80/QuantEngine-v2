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
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

from app.brokers.mt5.connection import MT5Connection
from app.execution.models import (
    BrokerExecution,
    BrokerPosition,
    Fill,
    InstrumentSpec,
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


def _mt5_comment(reason: str | None) -> str:
    """Sanitize an order comment for MT5's ``order_send``.

    MT5 sólo acepta ASCII imprimible y ~31 caracteres en el campo ``comment``;
    acentos o símbolos (é, ñ, —, ≤...) provocan ``order_send`` → ``[-2] Invalid
    "comment" argument`` y la orden nunca llega al broker.
    """
    text = reason or "quantengine"
    ascii_only = text.encode("ascii", "ignore").decode("ascii")
    # Exness/MT5 rechaza puntuación (paréntesis, ':', '%'...) en el comentario:
    # se conserva sólo alfanumérico y espacio, que el servidor acepta siempre.
    cleaned = "".join(ch if ch.isalnum() or ch == " " else " " for ch in ascii_only)
    cleaned = " ".join(cleaned.split())  # colapsa espacios repetidos
    return cleaned[:31].strip() or "quantengine"


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
            "comment": _mt5_comment(request.reason),
            "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
        }

        if request.reduce_only:
            # Cuenta hedging (Exness): una orden opuesta normal ABRE una posición
            # nueva en vez de cerrar la existente. Hay que referenciar el ticket
            # de la posición contraria con "position" para que MT5 la cierre.
            ticket = self._matching_position_ticket(mt5, real_symbol, is_buy)
            if ticket is None:
                _log.error(
                    "reduce_only sin posición %s abierta en %s; no se envía cierre",
                    "SELL" if is_buy else "BUY",
                    real_symbol,
                )
                return self._reject(RejectReason.BROKER_REJECTED)
            payload["position"] = ticket
        else:
            if request.stop_loss is not None:
                payload["sl"] = float(request.stop_loss)
            if request.take_profit is not None:
                payload["tp"] = float(request.take_profit)

        result = self._send_with_fallbacks(mt5, real_symbol, payload)
        return self._interpret(result, request, ticker, volume, mt5)

    def account_balance(self) -> float | None:
        """Balance realizado real de la cuenta MT5 (o ``None`` si no disponible).

        Lo consume el Execution Engine para sincronizar el Portfolio Manager con
        la caja real del broker: en demo las órdenes son reales, así que la
        contabilidad interna debe reflejar la cuenta, no un saldo simulado.
        """
        if not self._conn.connected:
            return None
        info = self._conn.account_info()
        if info is None:
            return None
        balance = getattr(info, "balance", None)
        return float(balance) if balance is not None else None

    def open_position_tickets(self, symbol: str) -> set[int]:
        """Tickets de las posiciones realmente abiertas en MT5 para ``symbol``.

        Usado por la reconciliación del Execution Engine para detectar
        posiciones que el bot cree abiertas pero que ya no existen en la
        cuenta real (p. ej. porque se cerraron a mano en el terminal/Exness).
        """
        if not self._conn.connected:
            return set()
        real_symbol = self._conn.resolve_symbol(symbol)
        positions = self._conn.mt5.positions_get(symbol=real_symbol)
        if not positions:
            return set()
        return {int(p.ticket) for p in positions}

    def open_broker_positions(self) -> list[BrokerPosition]:
        """Todas las posiciones vivas en la cuenta, con su detalle.

        La consume la adopción de arranque del Execution Engine: tras un reinicio
        el Position Manager arranca vacío y, sin esto, el motor ignora lo que ya
        está abierto (no le hace trailing ni break-even, no lo cuenta para la
        exposición y abre duplicados saltándose ``max_positions_per_symbol``).
        """
        if not self._conn.connected:
            return []
        positions = self._conn.mt5.positions_get()
        if not positions:
            return []
        buy_type = int(getattr(self._conn.mt5, "POSITION_TYPE_BUY", 0))
        adopted: list[BrokerPosition] = []
        for p in positions:
            if int(getattr(p, "magic", self._magic)) != self._magic:
                # Sólo se adoptan las posiciones de este motor; las abiertas a
                # mano por el operador se dejan en paz.
                continue
            raw_time = getattr(p, "time", None)
            opened_at = datetime.fromtimestamp(int(raw_time), tz=UTC) if raw_time else None
            adopted.append(
                BrokerPosition(
                    ticket=int(p.ticket),
                    symbol=str(p.symbol).upper(),
                    is_long=int(getattr(p, "type", buy_type)) == buy_type,
                    volume=float(p.volume),
                    price_open=float(p.price_open),
                    stop_loss=float(p.sl) or None,
                    take_profit=float(p.tp) or None,
                    opened_at=opened_at,
                )
            )
        return adopted

    def _matching_position_ticket(
        self, mt5: ModuleType, symbol: str, closing_is_buy: bool
    ) -> int | None:
        """Ticket de la posición abierta que esta orden de cierre debe saldar.

        Un cierre BUY salda una posición SELL (y viceversa): se busca la
        posición contraria más antigua del símbolo para no cerrar la que no
        corresponde cuando hay varias abiertas.
        """
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return None
        sell_type = getattr(mt5, "POSITION_TYPE_SELL", 1)
        buy_type = getattr(mt5, "POSITION_TYPE_BUY", 0)
        target_type = int(sell_type if closing_is_buy else buy_type)
        matching = [p for p in positions if int(getattr(p, "type", -1)) == target_type]
        if not matching:
            return None
        matching.sort(key=lambda p: int(getattr(p, "time", 0)))
        return int(matching[0].ticket)

    def _send_with_fallbacks(self, mt5: ModuleType, symbol: str, payload: dict[str, Any]) -> Any:
        """``order_send`` probando modos de llenado válidos (y sin comentario).

        Exness rechaza algunos ``type_filling`` con retcode 10030 (Unsupported
        filling mode) y los comentarios no alfanuméricos con ``[-2]``. Se prueban
        los modos soportados por el símbolo y, si el comentario molesta, se
        reintenta sin él, antes de dar la orden por rechazada.
        """
        invalid_fill = int(getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030))
        result: Any = None
        with self._conn.lock:
            for mode in self._filling_modes(mt5, symbol):
                payload["type_filling"] = mode
                result = mt5.order_send(payload)
                if result is None:
                    code, _ = self._last_error(mt5)
                    if code == -2 and "comment" in payload:
                        _log.warning("order_send [-2] con comment; reintento sin comment")
                        payload.pop("comment", None)
                        result = mt5.order_send(payload)
                if result is None:
                    return None
                if int(getattr(result, "retcode", -1)) != invalid_fill:
                    return result  # éxito o rechazo por otra causa: no reintentar
                _log.warning("retcode 10030 con type_filling=%s; probando siguiente", mode)
        return result

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
        order_ticket = getattr(result, "order", None)
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
            broker_ref=str(order_ticket) if order_ticket else None,
        )
        return BrokerExecution(fill, RejectReason.NONE)

    def instrument_spec(self, symbol: str) -> InstrumentSpec | None:
        """Contrato real del símbolo en el terminal (o ``None`` si no existe).

        Lo consume el Execution Engine para dimensionar en lotes: sin el
        ``trade_contract_size`` el sizing trata 1 lote como 1 unidad y un símbolo
        como XAUUSDm (contract_size=100) se envía 100× sobredimensionado.
        """
        if not self._conn.connected:
            return None
        real_symbol = self._conn.resolve_symbol(symbol)
        info = self._conn.mt5.symbol_info(real_symbol)
        if info is None:
            _log.error("Símbolo desconocido en MT5: %s", real_symbol)
            return None
        return InstrumentSpec(
            symbol=symbol.upper(),
            contract_size=float(getattr(info, "trade_contract_size", 1.0) or 1.0),
            volume_min=float(getattr(info, "volume_min", 0.01) or 0.01),
            volume_step=float(getattr(info, "volume_step", 0.01) or 0.01),
            volume_max=float(getattr(info, "volume_max", 0.0) or 0.0),
        )

    def _normalize_volume(self, mt5: ModuleType, symbol: str, quantity: float) -> float | None:
        """Valida la cantidad (lotes) contra los límites y el paso del símbolo.

        El sizing ya entrega lotes cuantizados al paso del símbolo; aquí sólo se
        comprueban los límites. Una cantidad por debajo de ``volume_min`` se
        **rechaza**: subirla al mínimo (lo que hacía antes) operaba con un riesgo
        muy superior al configurado sin que nada lo reportara.

        Returns:
            El volumen válido, o ``None`` si el símbolo no existe o no cabe.
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            _log.error("Símbolo desconocido en MT5: %s", symbol)
            return None
        vmin = float(getattr(info, "volume_min", 0.01) or 0.01)
        vmax = float(getattr(info, "volume_max", 0.0) or 0.0)
        step = float(getattr(info, "volume_step", 0.01) or 0.01)
        if quantity < vmin - 1e-12:
            _log.warning(
                "volumen %.6f por debajo del mínimo (%.4f) en %s: no se envía la orden",
                quantity,
                vmin,
                symbol,
            )
            return None
        volume = min(vmax, quantity) if vmax > 0 else quantity
        # Cuantiza al paso del símbolo (evita rechazos por volumen inválido).
        volume = round(round(volume / step) * step, 8)
        return max(vmin, volume)

    def _filling_modes(self, mt5: ModuleType, symbol: str) -> list[int]:
        """Modos ``ORDER_FILLING_*`` a probar según el bitmask del símbolo.

        ``symbol_info.filling_mode`` es un BITMASK de modos permitidos
        (``SYMBOL_FILLING_FOK=1``, ``SYMBOL_FILLING_IOC=2``), distinto de las
        constantes de orden ``ORDER_FILLING_*``. Pasar el bitmask crudo como
        ``type_filling`` provoca retcode 10030; aquí se mapea correctamente y se
        devuelven candidatos en orden de preferencia (IOC → FOK → RETURN).
        """
        fok = int(getattr(mt5, "ORDER_FILLING_FOK", 0))
        ioc = int(getattr(mt5, "ORDER_FILLING_IOC", 1))
        ret = int(getattr(mt5, "ORDER_FILLING_RETURN", 2))
        sym_fok = int(getattr(mt5, "SYMBOL_FILLING_FOK", 1))
        sym_ioc = int(getattr(mt5, "SYMBOL_FILLING_IOC", 2))
        info = mt5.symbol_info(symbol)
        allowed = int(getattr(info, "filling_mode", 0)) if info is not None else 0
        preferred: list[int] = []
        if allowed & sym_ioc:
            preferred.append(ioc)
        if allowed & sym_fok:
            preferred.append(fok)
        for mode in (ioc, fok, ret):  # fallback si el bitmask no está disponible
            if mode not in preferred:
                preferred.append(mode)
        return preferred

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
