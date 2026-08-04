"""El dinero se calcula sobre UNIDADES, nunca sobre lotes.

`quantity` son **lotes** — es lo que entiende el broker— y un lote de oro son
100 onzas (`contract_size=100`). El sizing se arreglo el 2026-07-27 para
convertir unidades a lotes, pero el calculo de dinero se quedo sin arreglar:
seguia multiplicando por `quantity`, asi que en oro el PnL, el nocional y el
riesgo salian **100 veces menores** de lo real.

El bug estuvo latente porque en BTC/ETH/USTEC `contract_size=1` y ahi lotes y
unidades coinciden: sale bien por casualidad. Solo se manifiesta en oro, que
lleva apagado desde el mismo dia del fix parcial.

Lo grave no es el PnL mal escrito en el journal: es que el freno de perdida
diaria y el riesgo por operacion contarian las perdidas de oro 100x mas
pequenas, asi que ninguna salvaguarda lo veria venir.
"""

from datetime import UTC, datetime

import pytest
from app.execution.models import ExitReason, Fill, OrderSide, PositionSide
from app.execution.position_manager import PositionManager

_BASE = datetime(2026, 8, 1, tzinfo=UTC)

GOLD_CONTRACT = 100.0  # 1 lote = 100 onzas
GOLD_PRICE = 4_078.0
MIN_LOT = 0.01  # = 1 onza


def _fill(quantity: float, price: float) -> Fill:
    return Fill(
        request_id="req",
        symbol="XAUUSDM",
        side=OrderSide.BUY,
        quantity=quantity,
        requested_quantity=quantity,
        reference_price=price,
        price=price,
        executed_at=_BASE,
    )


def _open_gold(manager: PositionManager, *, stop: float | None = None):
    return manager.open(
        _fill(MIN_LOT, GOLD_PRICE),
        stop_loss=stop,
        take_profit=None,
        contract_size=GOLD_CONTRACT,
    )


# ----------------------------------------------------------------------
# El caso del bug, con numeros
# ----------------------------------------------------------------------


def test_the_minimum_gold_lot_is_one_ounce_not_one_hundredth():
    """0.01 lotes de oro son 1 onza. Es la unidad minima que existe."""
    position = _open_gold(PositionManager())

    assert position.quantity == pytest.approx(0.01)  # lotes
    assert position.units == pytest.approx(1.0)  # onzas


def test_gold_notional_is_the_price_of_an_ounce():
    """~$4.078, no ~$41. La confusion lotes/unidades daba 100x menos."""
    position = _open_gold(PositionManager())

    assert position.cost_basis == pytest.approx(GOLD_PRICE)
    assert position.cost_basis / GOLD_PRICE == pytest.approx(1.0)


def test_gold_pnl_is_not_understated_by_the_contract_size():
    """El nucleo del bug: 6 $/oz de movimiento son 6 $, no 0.06 $."""
    manager = PositionManager()
    position = _open_gold(manager)

    gross = manager.close(
        position,
        exit_price=GOLD_PRICE + 6.0,
        close_commission=0.0,
        reason=ExitReason.TAKE_PROFIT,
    )

    assert gross == pytest.approx(6.0)
    assert gross != pytest.approx(0.06)


def test_gold_risk_to_the_stop_is_measured_in_money_not_in_lots():
    """El freno de perdida diaria se alimenta de esto.

    Con el bug, una posicion que arriesga 6 $ declaraba 0.06 $, asi que ninguna
    salvaguarda de riesgo la veia venir.
    """
    position = _open_gold(PositionManager(), stop=GOLD_PRICE - 6.0)

    assert position.initial_risk == pytest.approx(6.0)


def test_open_risk_aggregates_in_money_too():
    manager = PositionManager()
    position = _open_gold(manager, stop=GOLD_PRICE - 6.0)
    manager.update_mark(position, GOLD_PRICE)

    assert manager.open_risk() == pytest.approx(6.0)


def test_unrealized_pnl_uses_units():
    position = _open_gold(PositionManager())

    assert position.unrealized_pnl(GOLD_PRICE + 10.0) == pytest.approx(10.0)


# ----------------------------------------------------------------------
# Que no se rompa lo que ya funcionaba
# ----------------------------------------------------------------------


def test_a_contract_size_of_one_is_unchanged():
    """En BTC/ETH/USTEC lotes == unidades: el fix no puede alterarlos.

    Es donde el bug estaba escondido, asi que conviene fijarlo.
    """
    manager = PositionManager()
    position = manager.open(
        Fill(
            request_id="req",
            symbol="BTCUSDM",
            side=OrderSide.BUY,
            quantity=0.01,
            requested_quantity=0.01,
            reference_price=64_963.0,
            price=64_963.0,
            executed_at=_BASE,
        ),
        stop_loss=64_863.0,
        take_profit=None,
        contract_size=1.0,
    )

    assert position.units == pytest.approx(0.01)
    assert position.cost_basis == pytest.approx(649.63)
    assert position.initial_risk == pytest.approx(1.0)


def test_the_default_contract_size_is_one():
    """Quien no lo pase se comporta como antes del fix: sin sorpresas."""
    manager = PositionManager()
    position = manager.open(_fill(0.01, 100.0), stop_loss=None, take_profit=None)

    assert position.contract_size == 1.0
    assert position.units == pytest.approx(0.01)


def test_a_short_position_keeps_its_sign():
    manager = PositionManager()
    position = manager.open(
        Fill(
            request_id="req",
            symbol="XAUUSDM",
            side=OrderSide.SELL,
            quantity=MIN_LOT,
            requested_quantity=MIN_LOT,
            reference_price=GOLD_PRICE,
            price=GOLD_PRICE,
            executed_at=_BASE,
        ),
        stop_loss=None,
        take_profit=None,
        contract_size=GOLD_CONTRACT,
    )

    assert position.side is PositionSide.SHORT
    # El precio baja 6 $/oz: un corto GANA 6 $.
    assert position.unrealized_pnl(GOLD_PRICE - 6.0) == pytest.approx(6.0)


# ----------------------------------------------------------------------
# Que sobreviva al reinicio y al journal
# ----------------------------------------------------------------------


def test_a_restarted_gold_position_keeps_its_contract_size():
    """Sin esto el PnL volveria a calcularse 100x menor tras un reinicio.

    Mismo patron que ya fallo con `strategy`: el snapshot no persistia el campo.
    """
    import json

    from app.production.recovery.snapshot import position_from_state, position_to_state

    position = _open_gold(PositionManager(), stop=GOLD_PRICE - 6.0)

    revived = position_from_state(json.loads(json.dumps(position_to_state(position))))

    assert revived.contract_size == GOLD_CONTRACT
    assert revived.units == pytest.approx(1.0)
    assert revived.initial_risk == pytest.approx(6.0)


def test_an_old_snapshot_without_contract_size_still_loads():
    """El estado guardado antes del fix no lleva la clave."""
    import json

    from app.production.recovery.snapshot import position_from_state, position_to_state

    payload = json.loads(json.dumps(position_to_state(_open_gold(PositionManager()))))
    del payload["contract_size"]

    assert position_from_state(payload).contract_size == 1.0


def test_the_journal_records_the_contract_size_used():
    """Sin el, una operacion antigua no se puede reinterpretar."""
    import json

    from tests.unit.ml_helpers import make_trade

    trade = make_trade(contract_size=GOLD_CONTRACT)
    revived = type(trade).from_dict(json.loads(json.dumps(trade.to_dict())))

    assert revived.contract_size == GOLD_CONTRACT


def test_a_pre_fix_journal_line_defaults_to_one():

    from tests.unit.ml_helpers import make_trade

    payload = make_trade().to_dict()
    del payload["contract_size"]

    assert type(make_trade()).from_dict(payload).contract_size == 1.0
