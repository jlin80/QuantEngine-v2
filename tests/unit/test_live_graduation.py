"""Criterios de graduación a live: se miden, y no habilitan nada.

Desde la Fase 5 existía la regla "solo tras criterios estadísticos" sin que esos
criterios estuvieran escritos en ningún sitio. Una regla sin umbrales no se
puede incumplir porque no se puede evaluar. Aquí se fijan los umbrales y —sobre
todo— que evaluarlos **no toca el guard anti-live**.
"""

from datetime import UTC, datetime, timedelta

from app.config.settings import Settings
from app.execution.models.enums import ExitReason
from app.production.live.graduation import (
    MAX_FORCED_EXIT_PCT,
    MIN_EXPECTANCY_R,
    MIN_PROFIT_FACTOR,
    MIN_TRADES,
    evaluate_graduation,
)

from tests.unit.ml_helpers import make_trade

_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _trades(
    n: int,
    *,
    r: float = 0.5,
    pnl: float = 1.0,
    regime: str = "trending",
    exit_reason: ExitReason = ExitReason.TAKE_PROFIT,
    span_days: int = 90,
) -> list:
    step = timedelta(days=span_days) / max(1, n)
    return [
        make_trade(
            entry_time=_BASE + step * i,
            exit_time=_BASE + step * i + timedelta(minutes=30),
            r_multiple=r,
            pnl=pnl,
            regime=regime,
            exit_reason=exit_reason,
        )
        for i in range(n)
    ]


def _by_name(report, name):
    return next(c for c in report.criteria if c.name == name)


# ----------------------------------------------------------------------
# Lo que más importa: esto no habilita nada
# ----------------------------------------------------------------------


def test_a_fully_met_report_still_does_not_enable_live():
    """Cumplir los criterios es condición necesaria, nunca suficiente ni automática.

    El criterio de aceptación del bloque es literalmente que no haya ningún
    camino de aquí al guard: `allow_live` sigue en `False` y `resolved_mode()`
    sigue forzando paper después de evaluar.
    """
    trades = _trades(500, r=1.0, pnl=5.0)
    trades += _trades(200, r=0.8, pnl=4.0, regime="ranging")
    trades += _trades(200, r=0.9, pnl=4.0, regime="breakout")

    report = evaluate_graduation(trades)

    assert report.met
    settings = Settings()
    assert settings.execution.resolved_mode() == "paper"
    assert settings.production.allow_live is False


def test_the_report_says_out_loud_that_it_enables_nothing():
    """Un informe que parece una aprobación acabaría usándose como tal."""
    report = evaluate_graduation(_trades(10))

    assert "NO activa live" in report.to_dict()["note"]


# ----------------------------------------------------------------------
# Cada criterio, con su caso de fallo
# ----------------------------------------------------------------------


def test_an_empty_journal_fails_instead_of_dividing_by_zero():
    report = evaluate_graduation([])

    assert not report.met
    assert report.sample == 0


def test_a_small_sample_fails_however_good_it_looks():
    """Con 100 operaciones el error estándar tapa una expectativa modesta."""
    report = evaluate_graduation(_trades(100, r=2.0, pnl=10.0))

    sample = _by_name(report, "sample")
    assert not sample.passed
    assert f"{MIN_TRADES - 100}" in sample.gap


def test_a_break_even_system_does_not_graduate():
    """Exigir >0 aprobaría un sistema que empata — y en real, empatar es perder.

    El paper no cobra swaps ni sufre requotes: el margen del umbral es
    precisamente lo que cubre esa diferencia.
    """
    report = evaluate_graduation(_trades(500, r=0.0, pnl=0.0))

    assert not _by_name(report, "expectancy_r").passed


def test_the_expectancy_gap_is_reported_in_r_not_as_a_verdict():
    """ "Faltan 0.2R por operación" es accionable; "no cumple" no lo es."""
    report = evaluate_graduation(_trades(500, r=-0.1, pnl=-1.0))

    gap = _by_name(report, "expectancy_r").gap
    assert "0.200R" in gap


def test_a_losing_system_fails_the_profit_factor():
    report = evaluate_graduation(_trades(500, r=-0.5, pnl=-2.0))

    criterion = _by_name(report, "profit_factor")
    assert not criterion.passed
    assert float(criterion.actual) < MIN_PROFIT_FACTOR


def test_a_single_regime_never_covers_the_requirement():
    """400 operaciones en un solo régimen miden un régimen con mucho detalle."""
    report = evaluate_graduation(_trades(500, regime="ranging"))

    assert not _by_name(report, "regime_coverage").passed


def test_an_anecdotal_regime_does_not_count_as_covered():
    """Cinco operaciones en 'trending' no son cobertura de 'trending'."""
    trades = _trades(400, regime="ranging") + _trades(5, regime="trending")

    report = evaluate_graduation(trades)

    assert "trending" not in _by_name(report, "regime_coverage").actual


def test_a_short_but_busy_period_fails_the_calendar():
    """El calendario importa aparte de la muestra: 400 operaciones en 3 días
    miden un único momento de mercado, por muchas que sean."""
    report = evaluate_graduation(_trades(500, span_days=3))

    assert not _by_name(report, "days_in_paper").passed


def test_a_system_that_closes_its_own_positions_does_not_graduate():
    """Criterio añadido por lo que dicen los datos reales, no por el enunciado.

    Si el motor cierra la mayoría de posiciones por régimen o por tiempo, sus
    estrategias casi nunca llegan a poner a prueba su tesis: lo que se graduaría
    a real sería la ejecución, no la estrategia. Puede tener expectativa
    positiva y aun así no haber demostrado nada sobre sus señales.
    """
    trades = _trades(500, r=1.0, pnl=5.0, exit_reason=ExitReason.REGIME_CHANGE)

    report = evaluate_graduation(trades)

    criterion = _by_name(report, "forced_exits")
    assert not criterion.passed
    assert float(criterion.actual.rstrip("%")) > MAX_FORCED_EXIT_PCT


def test_thesis_exits_are_the_ones_that_count():
    """Objetivo, stop, trailing y break-even resuelven la tesis; los demás no."""
    for reason in (
        ExitReason.TAKE_PROFIT,
        ExitReason.STOP_LOSS,
        ExitReason.TRAILING_STOP,
        ExitReason.BREAK_EVEN,
    ):
        report = evaluate_graduation(_trades(100, exit_reason=reason))
        assert _by_name(report, "forced_exits").passed, reason


def test_drawdown_is_measured_against_the_equity_peak():
    """Una racha perdedora tras un pico es lo que mide el criterio."""
    trades = _trades(50, pnl=10.0) + _trades(50, pnl=-10.0)

    report = evaluate_graduation(trades, starting_equity=500.0)

    assert float(_by_name(report, "max_drawdown").actual.rstrip("%")) > 0.0


# ----------------------------------------------------------------------
# Que los umbrales documentados sean los del código
# ----------------------------------------------------------------------


def test_the_documented_thresholds_are_the_ones_in_force():
    assert MIN_TRADES == 400
    assert MIN_EXPECTANCY_R == 0.10
    assert MIN_PROFIT_FACTOR == 1.30
    assert MAX_FORCED_EXIT_PCT == 50.0
