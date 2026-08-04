"""Los hitos de capital salen de una fórmula, no de números afirmados.

`docs/architecture.md` documentaba los hitos (~$1k / ~$2-5k / ~$20k) como
umbrales dados. Este módulo fija la derivación: el tope de exposición no puede
bajar de lo que ocupa **una sola posición del lote mínimo**, y de ahí sale todo
lo demás.

Que sea un test y no una hoja de cálculo tiene un motivo: cuando cambie el
bróker, el `contract_size` o el precio de referencia, la tabla documentada y
estos números divergirán y alguien se enterará.
"""

import pytest
from app.execution.models.instrument import InstrumentSpec

# Specs reales de la demo Exness (Notion, 2026-07-27). El precio es el de
# referencia con el que se calculó la tabla documentada: si cambia mucho, la
# tabla hay que rehacerla — que es justo lo que este test hace evidente.
ETH = (InstrumentSpec(symbol="ETHUSDM", contract_size=1.0, volume_min=0.1), 1947.5)
USTEC = (InstrumentSpec(symbol="USTECM", contract_size=1.0, volume_min=0.01), 28_060.0)
BTC = (InstrumentSpec(symbol="BTCUSDM", contract_size=1.0, volume_min=0.01), 64_963.0)
GOLD = (InstrumentSpec(symbol="XAUUSDM", contract_size=100.0, volume_min=0.01), 4_082.81)


def min_notional(spec: InstrumentSpec, price: float) -> float:
    """Nocional de una sola posición del lote mínimo."""
    return spec.notional(spec.volume_min, price)


def min_viable_exposure_pct(spec: InstrumentSpec, price: float, equity: float) -> float:
    """Exposición que ocupa esa posición, en % del equity."""
    return min_notional(spec, price) / equity * 100.0


def equity_for_cap(spec: InstrumentSpec, price: float, cap_pct: float) -> float:
    """Equity necesario para que el lote mínimo quepa bajo un tope dado."""
    return min_notional(spec, price) / (cap_pct / 100.0)


# ----------------------------------------------------------------------
# El nocional del lote mínimo, que es el numerador de todo
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec_price", "expected"),
    [(ETH, 195.0), (USTEC, 280.6), (BTC, 649.6), (GOLD, 4082.8)],
)
def test_the_documented_minimum_notionals_are_reproducible(spec_price, expected):
    spec, price = spec_price

    assert min_notional(spec, price) == pytest.approx(expected, rel=0.01)


def test_gold_is_disabled_by_contract_size_not_by_risk_appetite():
    """`contract_size=100` hace su lote mínimo 6× el de BTC y 21× el de ETH.

    Es el punto de fondo de toda la sección: el tope alto no expresa apetito de
    riesgo, expresa una restricción de granularidad del bróker.
    """
    gold = min_notional(*GOLD)

    assert gold / min_notional(*BTC) == pytest.approx(6.3, rel=0.05)
    assert gold / min_notional(*ETH) == pytest.approx(21.0, rel=0.05)


# ----------------------------------------------------------------------
# Los hitos documentados, derivados
# ----------------------------------------------------------------------


def test_the_20k_milestone_for_gold_is_derived_not_asserted():
    """El "~$20k para el oro" no era intuición: es $4.083 / 0.20 = $20.414."""
    assert equity_for_cap(*GOLD, cap_pct=20.0) == pytest.approx(20_414, rel=0.01)


def test_at_the_current_equity_a_single_gold_position_is_2000_pct():
    """Con ~$200, una posición de oro ocupa 20× el equity. De ahí el toggle OFF."""
    assert min_viable_exposure_pct(*GOLD, equity=200.0) == pytest.approx(2041, rel=0.01)


def test_at_1k_the_crypto_caps_stop_being_a_necessity():
    """Primer hito: ETH y USTEC bajan del 30 %, así que los 2000 % son holgura."""
    assert min_viable_exposure_pct(*ETH, equity=1000.0) < 30.0
    assert min_viable_exposure_pct(*USTEC, equity=1000.0) < 30.0
    # BTC todavía no: sigue siendo el que manda el tope.
    assert min_viable_exposure_pct(*BTC, equity=1000.0) > 50.0


def test_at_5k_every_tradable_symbol_fits_under_a_conventional_cap():
    """Segundo hito: los tres símbolos operables caben bajo un tope del 20 %."""
    for spec, price in (ETH, USTEC, BTC):
        assert min_viable_exposure_pct(spec, price, equity=5000.0) < 20.0


def test_gold_still_does_not_fit_at_5k():
    """Y sigue sin caber: 82 % del equity en una sola posición."""
    assert min_viable_exposure_pct(*GOLD, equity=5000.0) > 50.0


# ----------------------------------------------------------------------
# La propiedad general que hace envejecer los topes
# ----------------------------------------------------------------------


def test_the_required_cap_falls_as_the_account_grows():
    """El numerador lo fija el bróker; el denominador crece con la cuenta.

    Por eso el tope no deja de ser necesario poco a poco: deja de serlo en un
    punto concreto y calculable, y hasta que alguien lo recalcula sigue
    autorizando exposición que ya no hace falta.
    """
    equities = [200.0, 1000.0, 5000.0, 20_000.0]
    required = [min_viable_exposure_pct(*BTC, equity=e) for e in equities]

    assert required == sorted(required, reverse=True)
    assert required[0] / required[-1] == pytest.approx(100.0, rel=0.01)
