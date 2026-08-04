"""Portfolio Manager, Position Manager y Order Manager."""

from app.execution.models import (
    ExitReason,
    Fill,
    OrderRequest,
    OrderSide,
    OrderType,
    Position,
    PositionSide,
    RejectReason,
)
from app.execution.order_manager import OrderManager
from app.execution.portfolio_manager import PortfolioManager
from app.execution.position_manager import PositionManager


def _fill(side: OrderSide = OrderSide.BUY, price: float = 100.0, qty: float = 1.0) -> Fill:
    return Fill(
        request_id="r",
        symbol="BTCUSDT",
        side=side,
        quantity=qty,
        requested_quantity=qty,
        reference_price=price,
        price=price,
        commission=0.1,
    )


def test_portfolio_accounting_open_and_close():
    port = PortfolioManager(10_000.0)
    port.on_open_commission(0.1)
    assert port.balance == 10_000.0 - 0.1
    port.on_trade_closed(gross_pnl=50.0, close_commission=0.2)
    # balance = 10000 - 0.1 (apertura) + 50 - 0.2 (cierre)
    assert round(port.balance, 4) == round(10_000.0 - 0.1 + 50.0 - 0.2, 4)
    assert port.total_trades == 1
    assert round(port.realized_pnl, 4) == round(49.7, 4)


def test_portfolio_snapshot_tracks_drawdown_and_exposure():
    port = PortfolioManager(10_000.0)
    pos = Position(
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        quantity=10.0,
        initial_quantity=10.0,
        entry_price=100.0,
    )
    pos.update_mark(95.0)  # -50 flotante
    snap = port.snapshot([pos])
    assert snap.floating_pnl == -50.0
    assert snap.equity == 9_950.0
    assert snap.exposure == 950.0
    assert snap.drawdown_pct > 0.0


def test_position_manager_break_even_then_trailing():
    pm = PositionManager(break_even_r=1.0, trailing_enabled=True, trailing_atr_multiple=2.0)
    pos = pm.open(_fill(price=100.0, qty=1.0), stop_loss=98.0, take_profit=110.0, atr=1.0)
    # +2 (2R) → break-even mueve el stop a la entrada.
    pm.update_mark(pos, 102.0)
    updates = pm.manage(pos, atr=1.0)
    kinds = {u.kind for u in updates}
    assert "break_even" in kinds
    assert pos.stop_loss == 100.0 and pos.break_even_active
    # Sigue subiendo → trailing sube el stop por encima de la entrada.
    pm.update_mark(pos, 106.0)
    pm.manage(pos, atr=1.0)
    assert pos.stop_loss > 100.0 and pos.trailing_active


def test_position_manager_exit_detection():
    pm = PositionManager(break_even_r=0.0, trailing_enabled=False)
    pos = pm.open(_fill(price=100.0), stop_loss=98.0, take_profit=104.0, atr=1.0)
    pm.update_mark(pos, 104.5)
    assert pm.check_exit(pos) is ExitReason.TAKE_PROFIT
    pm.update_mark(pos, 97.0)
    assert pm.check_exit(pos) is ExitReason.STOP_LOSS


def test_position_close_computes_net_pnl():
    pm = PositionManager()
    pos = pm.open(_fill(price=100.0, qty=2.0), stop_loss=98.0, take_profit=104.0)
    gross = pm.close(pos, exit_price=103.0, close_commission=0.1, reason=ExitReason.TAKE_PROFIT)
    assert gross == 6.0  # (103-100)*2
    # neto = bruto - comisiones (0.1 apertura + 0.1 cierre)
    assert round(pos.realized_pnl, 4) == round(6.0 - 0.2, 4)
    assert pm.closed_positions and not pm.open_positions


def test_order_manager_lifecycle_and_oco():
    om = OrderManager()
    a = om.create(OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, quantity=1.0))
    b = om.create(
        OrderRequest(
            symbol="BTCUSDT", side=OrderSide.SELL, quantity=1.0, order_type=OrderType.LIMIT
        )
    )
    om.create_oco([a.order_id, b.order_id], "g1")
    om.apply_fill(a, _fill())
    # Al ejecutarse A, su hermana OCO B se cancela.
    assert b.status.value == "cancelled"
    assert om.status()["filled"] == 1

    c = om.create(OrderRequest(symbol="ETHUSD", side=OrderSide.BUY, quantity=1.0))
    om.reject(c, RejectReason.RISK_BLOCKED)
    assert om.status()["rejected"] == 1


def test_trailing_does_not_tighten_the_initial_stop_on_open():
    """El bug del churn: con ATR bajo frente al piso de stop, el trailing
    apretaba el stop nada mas abrir y liquidaba la posicion por ruido.

    Reproduce el caso real del oro: stop inicial a 6.12 (piso del 0.15%) y
    ATR x2 = 1.40, mucho mas cerca. Sin el gate, `entry - 1.40` "mejoraba"
    el stop y lo movia de 6.12 a 1.40 de distancia sin que el precio se
    hubiera movido.
    """
    pm = PositionManager(break_even_r=1.0, trailing_enabled=True, trailing_atr_multiple=2.0)
    pos = pm.open(_fill(price=4080.0, qty=0.01), stop_loss=4073.88, take_profit=4089.18, atr=0.7)

    pm.update_mark(pos, 4080.0)  # precio sin moverse
    updates = pm.manage(pos, atr=0.7)

    assert updates == []
    assert pos.stop_loss == 4073.88  # intacto
    assert not pos.trailing_active


def test_trailing_starts_only_after_activate_r():
    """El trailing arranca al alcanzar +1R, no antes."""
    pm = PositionManager(
        break_even_r=0.0,
        trailing_enabled=True,
        trailing_atr_multiple=2.0,
        trailing_activate_r=1.0,
    )
    pos = pm.open(_fill(price=100.0, qty=1.0), stop_loss=98.0, take_profit=110.0, atr=0.5)

    pm.update_mark(pos, 101.0)  # +0.5R: todavia no
    assert pm.manage(pos, atr=0.5) == []
    assert pos.stop_loss == 98.0

    pm.update_mark(pos, 102.0)  # +1R: ya si
    pm.manage(pos, atr=0.5)
    assert pos.trailing_active
    assert pos.stop_loss > 98.0


def test_trailing_never_locks_a_loss_once_active():
    """Una vez activo, el trailing solo puede mejorar el stop."""
    pm = PositionManager(
        break_even_r=0.0,
        trailing_enabled=True,
        trailing_atr_multiple=2.0,
        trailing_activate_r=1.0,
    )
    pos = pm.open(_fill(price=100.0, qty=1.0), stop_loss=98.0, take_profit=110.0, atr=0.5)
    pm.update_mark(pos, 104.0)
    pm.manage(pos, atr=0.5)
    best = pos.stop_loss

    pm.update_mark(pos, 103.0)  # retrocede: el stop no debe empeorar
    pm.manage(pos, atr=0.5)

    assert pos.stop_loss == best
