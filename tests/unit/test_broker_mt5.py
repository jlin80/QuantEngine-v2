"""Pruebas del adaptador de broker MetaTrader 5 (modo demo).

Todo con un módulo ``MetaTrader5`` falso e inyectado: sin terminal real, sin red.
Se valida el mapeo OrderRequest→order_send, la traducción de retcodes, la
cuantización de volumen y el healthcheck.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.brokers.mt5.broker import MT5Broker
from app.brokers.mt5.connection import MT5Connection, MT5ConnectionConfig
from app.execution.models import OrderRequest, OrderSide, OrderType, RejectReason
from app.market.models import Ticker
from app.utils.time import utc_now


class FakeMT5:
    """Doble de prueba del módulo ``MetaTrader5`` con estado inspeccionable."""

    # Constantes que el broker consulta por nombre.
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(
        self,
        *,
        retcode: int = 10009,
        symbol_known: bool = True,
        volume_min: float = 0.01,
        volume_max: float = 100.0,
        volume_step: float = 0.01,
        contract_size: float = 100.0,
        account_ok: bool = True,
        positions: list[Any] | None = None,
    ) -> None:
        self._retcode = retcode
        self._symbol_known = symbol_known
        self._vmin = volume_min
        self._vmax = volume_max
        self._vstep = volume_step
        self._contract_size = contract_size
        self._account_ok = account_ok
        self._positions = positions or []
        self.sent: list[dict[str, Any]] = []
        self.initialized = False

    def positions_get(self, symbol: str | None = None) -> Any:
        if symbol is None:
            return list(self._positions)
        return [p for p in self._positions if p.symbol == symbol]

    def initialize(self, *args: Any, **kwargs: Any) -> bool:
        self.initialized = True
        return True

    def shutdown(self) -> None:
        self.initialized = False

    def last_error(self) -> tuple[int, str]:
        return (0, "ok")

    def account_info(self) -> Any:
        return SimpleNamespace(login=123, balance=1000.0) if self._account_ok else None

    def symbol_info(self, symbol: str) -> Any:
        if not self._symbol_known:
            return None
        return SimpleNamespace(
            volume_min=self._vmin,
            volume_max=self._vmax,
            volume_step=self._vstep,
            trade_contract_size=self._contract_size,
            filling_mode=self.ORDER_FILLING_IOC,
        )

    def order_send(self, payload: dict[str, Any]) -> Any:
        self.sent.append(payload)
        return SimpleNamespace(
            retcode=self._retcode,
            price=payload["price"],
            volume=payload["volume"],
            comment="done" if self._retcode == self.TRADE_RETCODE_DONE else "rejected",
            commission=0.0,
        )


def _ticker(symbol: str = "XAUUSD", bid: float = 2000.0, ask: float = 2000.5) -> Ticker:
    now = utc_now()
    return Ticker(symbol=symbol, provider="mt5", bid=bid, ask=ask, exchange_ts=now, local_ts=now)


def _order(**overrides: Any) -> OrderRequest:
    data: dict[str, Any] = {
        "symbol": "XAUUSD",
        "side": OrderSide.BUY,
        "quantity": 0.10,
        "order_type": OrderType.MARKET,
    }
    data.update(overrides)
    return OrderRequest(**data)


def _connected_broker(fake: FakeMT5) -> MT5Broker:
    conn = MT5Connection(
        MT5ConnectionConfig(login=123, password="x", server="Exness-Demo"), mt5=fake
    )
    conn.connect()
    return MT5Broker(conn)


def test_market_buy_fills_and_maps_payload() -> None:
    fake = FakeMT5()
    broker = _connected_broker(fake)

    result = broker.execute(
        _order(side=OrderSide.BUY, stop_loss=1990.0, take_profit=2020.0), _ticker()
    )

    assert result.accepted
    assert result.fill is not None
    assert result.fill.side is OrderSide.BUY
    assert result.fill.price == pytest.approx(2000.5)  # ask para compra
    assert broker.stats == {"executed": 1, "rejected": 0}

    payload = fake.sent[-1]
    assert payload["type"] == FakeMT5.ORDER_TYPE_BUY
    assert payload["action"] == FakeMT5.TRADE_ACTION_DEAL
    assert payload["price"] == pytest.approx(2000.5)
    assert payload["sl"] == 1990.0
    assert payload["tp"] == 2020.0
    assert payload["symbol"] == "XAUUSD"


def test_market_sell_uses_bid() -> None:
    fake = FakeMT5()
    broker = _connected_broker(fake)

    result = broker.execute(_order(side=OrderSide.SELL), _ticker())

    assert result.accepted
    assert fake.sent[-1]["type"] == FakeMT5.ORDER_TYPE_SELL
    assert fake.sent[-1]["price"] == pytest.approx(2000.0)  # bid para venta


def test_broker_rejects_on_bad_retcode() -> None:
    fake = FakeMT5(retcode=10004)  # requote
    broker = _connected_broker(fake)

    result = broker.execute(_order(), _ticker())

    assert not result.accepted
    assert result.reject_reason is RejectReason.BROKER_REJECTED
    assert broker.stats == {"executed": 0, "rejected": 1}


def test_rejects_when_disconnected() -> None:
    fake = FakeMT5()
    conn = MT5Connection(MT5ConnectionConfig(login=1, password="x", server="s"), mt5=fake)
    broker = MT5Broker(conn)  # sin connect()

    result = broker.execute(_order(), _ticker())

    assert result.reject_reason is RejectReason.BROKER_UNAVAILABLE
    assert not fake.sent


def test_rejects_invalid_quantity() -> None:
    broker = _connected_broker(FakeMT5())
    result = broker.execute(_order(quantity=0.0), _ticker())
    assert result.reject_reason is RejectReason.INVALID_QUANTITY


def test_rejects_unknown_symbol() -> None:
    broker = _connected_broker(FakeMT5(symbol_known=False))
    result = broker.execute(_order(symbol="NOPE"), _ticker(symbol="NOPE"))
    assert result.reject_reason is RejectReason.INVALID_QUANTITY


def test_non_market_order_rejected() -> None:
    broker = _connected_broker(FakeMT5())
    result = broker.execute(_order(order_type=OrderType.LIMIT, limit_price=2000.0), _ticker())
    assert result.reject_reason is RejectReason.BROKER_REJECTED


def test_volume_quantized_to_step() -> None:
    fake = FakeMT5(volume_min=0.01, volume_step=0.01)
    broker = _connected_broker(fake)

    broker.execute(_order(quantity=0.117), _ticker())

    assert fake.sent[-1]["volume"] == pytest.approx(0.12)


def test_volume_below_minimum_is_rejected() -> None:
    """Por debajo del lote mínimo se rechaza; inflar hasta ``volume_min``
    operaría con un riesgo muy superior al que el sizing calculó."""
    fake = FakeMT5(volume_min=0.05, volume_max=1.0)
    broker = _connected_broker(fake)

    execution = broker.execute(_order(quantity=0.001), _ticker())

    assert not execution.accepted
    assert execution.reject_reason is RejectReason.INVALID_QUANTITY
    assert fake.sent == []


def test_volume_clamped_to_max() -> None:
    fake = FakeMT5(volume_min=0.05, volume_max=1.0)
    broker = _connected_broker(fake)

    broker.execute(_order(quantity=5.0), _ticker())

    assert fake.sent[-1]["volume"] == pytest.approx(1.0)


def test_instrument_spec_reads_contract_size() -> None:
    """El spec debe traer el ``trade_contract_size`` real (XAU=100)."""
    fake = FakeMT5(volume_min=0.01, volume_step=0.01, volume_max=200.0)
    broker = _connected_broker(fake)

    spec = broker.instrument_spec("XAUUSD")

    assert spec is not None
    assert spec.contract_size == pytest.approx(100.0)
    assert spec.volume_min == pytest.approx(0.01)
    # 0.5 lotes de oro = 50 onzas, no 0.5.
    assert spec.units(0.5) == pytest.approx(50.0)


def test_healthcheck_reflects_account_info() -> None:
    assert _connected_broker(FakeMT5(account_ok=True)).healthcheck() is True

    fake = FakeMT5(account_ok=False)
    broker = _connected_broker(fake)
    assert broker.healthcheck() is False


def test_broker_name_is_demo() -> None:
    assert _connected_broker(FakeMT5()).broker_name == "mt5_demo"


def test_reduce_only_close_sends_position_ticket() -> None:
    """En hedging, cerrar un BUY existente debe mandar un SELL con ``position``."""
    open_position = SimpleNamespace(
        ticket=581758329, symbol="XAUUSD", type=FakeMT5.POSITION_TYPE_BUY, time=100
    )
    fake = FakeMT5(positions=[open_position])
    broker = _connected_broker(fake)

    result = broker.execute(_order(side=OrderSide.SELL, reduce_only=True), _ticker())

    assert result.accepted
    payload = fake.sent[-1]
    assert payload["position"] == 581758329
    assert "sl" not in payload
    assert "tp" not in payload


def test_reduce_only_without_matching_position_is_rejected() -> None:
    fake = FakeMT5(positions=[])
    broker = _connected_broker(fake)

    result = broker.execute(_order(side=OrderSide.SELL, reduce_only=True), _ticker())

    assert result.reject_reason is RejectReason.BROKER_REJECTED
    assert not fake.sent


def test_open_position_tickets_reflects_live_broker_state() -> None:
    open_position = SimpleNamespace(
        ticket=581758329, symbol="XAUUSD", type=FakeMT5.POSITION_TYPE_BUY, time=100
    )
    fake = FakeMT5(positions=[open_position])
    broker = _connected_broker(fake)

    assert broker.open_position_tickets("XAUUSD") == {581758329}
    assert broker.open_position_tickets("ETHUSD") == set()


def test_open_broker_positions_maps_detail() -> None:
    """La adopción necesita el detalle completo, no sólo el ticket."""
    fake = FakeMT5(
        positions=[
            SimpleNamespace(
                ticket=584945506,
                symbol="USTECm",
                type=0,
                volume=0.01,
                price_open=28073.69,
                sl=28031.58,
                tp=28136.86,
                time=1785270000,
                magic=777_001,
            )
        ]
    )
    broker = _connected_broker(fake)

    live = broker.open_broker_positions()

    assert len(live) == 1
    assert live[0].ticket == 584945506
    assert live[0].symbol == "USTECM"  # normalizado a mayúsculas
    assert live[0].is_long is True
    assert live[0].volume == pytest.approx(0.01)
    assert live[0].stop_loss == pytest.approx(28031.58)


def test_open_broker_positions_ignores_foreign_magic() -> None:
    """Las posiciones abiertas a mano (otro magic) no se adoptan."""
    fake = FakeMT5(
        positions=[
            SimpleNamespace(
                ticket=1,
                symbol="XAUUSDm",
                type=0,
                volume=0.01,
                price_open=4000.0,
                sl=0.0,
                tp=0.0,
                time=1785270000,
                magic=999_999,
            )
        ]
    )
    broker = _connected_broker(fake)

    assert broker.open_broker_positions() == []


def test_open_broker_positions_maps_missing_stops_to_none() -> None:
    """MT5 usa 0.0 para "sin stop"; debe llegar como None, no como precio 0."""
    fake = FakeMT5(
        positions=[
            SimpleNamespace(
                ticket=7,
                symbol="ETHUSDm",
                type=1,
                volume=0.27,
                price_open=1946.24,
                sl=0.0,
                tp=0.0,
                time=1785270000,
                magic=777_001,
            )
        ]
    )
    broker = _connected_broker(fake)

    live = broker.open_broker_positions()

    assert live[0].is_long is False
    assert live[0].stop_loss is None
    assert live[0].take_profit is None
