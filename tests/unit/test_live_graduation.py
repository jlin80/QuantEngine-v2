"""Criterios de graduación a live: se miden, y no habilitan nada.

Desde la Fase 5 existía la regla "solo tras criterios estadísticos" sin que esos
criterios estuvieran escritos en ningún sitio. Una regla sin umbrales no se
puede incumplir porque no se puede evaluar. Aquí se fijan los umbrales y —sobre
todo— que evaluarlos **no toca el guard anti-live**.
"""

from datetime import UTC, datetime, timedelta

from app.config.settings import Settings
from app.execution.models.enums import ExitReason
from app.execution.models.trades import TradeRecord
from app.production.live.graduation import (
    MAX_FORCED_EXIT_PCT,
    MIN_EXPECTANCY_R,
    MIN_PROFIT_FACTOR,
    MIN_TRADES,
    GraduationCriterion,
    GraduationReport,
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
) -> list[TradeRecord]:
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


def _by_name(report: GraduationReport, name: str) -> GraduationCriterion:
    return next(c for c in report.criteria if c.name == name)


# ----------------------------------------------------------------------
# Lo que más importa: esto no habilita nada
# ----------------------------------------------------------------------


def test_a_fully_met_report_still_does_not_enable_live() -> None:
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


def test_the_report_says_out_loud_that_it_enables_nothing() -> None:
    """Un informe que parece una aprobación acabaría usándose como tal."""
    report = evaluate_graduation(_trades(10))

    assert "NO activa live" in report.to_dict()["note"]


# ----------------------------------------------------------------------
# Cada criterio, con su caso de fallo
# ----------------------------------------------------------------------


def test_an_empty_journal_fails_instead_of_dividing_by_zero() -> None:
    report = evaluate_graduation([])

    assert not report.met
    assert report.sample == 0


def test_a_small_sample_fails_however_good_it_looks() -> None:
    """Con 100 operaciones el error estándar tapa una expectativa modesta."""
    report = evaluate_graduation(_trades(100, r=2.0, pnl=10.0))

    sample = _by_name(report, "sample")
    assert not sample.passed
    assert f"{MIN_TRADES - 100}" in sample.gap


def test_a_break_even_system_does_not_graduate() -> None:
    """Exigir >0 aprobaría un sistema que empata — y en real, empatar es perder.

    El paper no cobra swaps ni sufre requotes: el margen del umbral es
    precisamente lo que cubre esa diferencia.
    """
    report = evaluate_graduation(_trades(500, r=0.0, pnl=0.0))

    assert not _by_name(report, "expectancy_r").passed


def test_the_expectancy_gap_is_reported_in_r_not_as_a_verdict() -> None:
    """ "Faltan 0.2R por operación" es accionable; "no cumple" no lo es."""
    report = evaluate_graduation(_trades(500, r=-0.1, pnl=-1.0))

    gap = _by_name(report, "expectancy_r").gap
    assert "0.200R" in gap


def test_a_losing_system_fails_the_profit_factor() -> None:
    report = evaluate_graduation(_trades(500, r=-0.5, pnl=-2.0))

    criterion = _by_name(report, "profit_factor")
    assert not criterion.passed
    assert float(criterion.actual) < MIN_PROFIT_FACTOR


def test_a_single_regime_never_covers_the_requirement() -> None:
    """400 operaciones en un solo régimen miden un régimen con mucho detalle."""
    report = evaluate_graduation(_trades(500, regime="ranging"))

    assert not _by_name(report, "regime_coverage").passed


def test_an_anecdotal_regime_does_not_count_as_covered() -> None:
    """Cinco operaciones en 'trending' no son cobertura de 'trending'."""
    trades = _trades(400, regime="ranging") + _trades(5, regime="trending")

    report = evaluate_graduation(trades)

    assert "trending" not in _by_name(report, "regime_coverage").actual


def test_a_short_but_busy_period_fails_the_calendar() -> None:
    """El calendario importa aparte de la muestra: 400 operaciones en 3 días
    miden un único momento de mercado, por muchas que sean."""
    report = evaluate_graduation(_trades(500, span_days=3))

    assert not _by_name(report, "days_in_paper").passed


def test_a_system_that_closes_its_own_positions_does_not_graduate() -> None:
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


def test_thesis_exits_are_the_ones_that_count() -> None:
    """Objetivo, stop, trailing y break-even resuelven la tesis; los demás no."""
    for reason in (
        ExitReason.TAKE_PROFIT,
        ExitReason.STOP_LOSS,
        ExitReason.TRAILING_STOP,
        ExitReason.BREAK_EVEN,
    ):
        report = evaluate_graduation(_trades(100, exit_reason=reason))
        assert _by_name(report, "forced_exits").passed, reason


def test_drawdown_is_measured_against_the_equity_peak() -> None:
    """Una racha perdedora tras un pico es lo que mide el criterio."""
    trades = _trades(50, pnl=10.0) + _trades(50, pnl=-10.0)

    report = evaluate_graduation(trades, starting_equity=500.0)

    assert float(_by_name(report, "max_drawdown").actual.rstrip("%")) > 0.0


# ----------------------------------------------------------------------
# Que los umbrales documentados sean los del código
# ----------------------------------------------------------------------


def test_the_documented_thresholds_are_the_ones_in_force() -> None:
    assert MIN_TRADES == 400
    assert MIN_EXPECTANCY_R == 0.10
    assert MIN_PROFIT_FACTOR == 1.30
    assert MAX_FORCED_EXIT_PCT == 50.0


# ----------------------------------------------------------------------
# Intervalo de confianza: lo que la estimacion puntual no ve
# ----------------------------------------------------------------------


def _varied(r_values: list[float], *, span_days: int = 90) -> list[TradeRecord]:
    """Operaciones con R distinto una a una (el helper de arriba usa R fijo)."""
    step = timedelta(days=span_days) / max(1, len(r_values))
    return [
        make_trade(
            entry_time=_BASE + step * i,
            exit_time=_BASE + step * i + timedelta(minutes=30),
            r_multiple=r,
            pnl=r,
            regime="trending",
            exit_reason=ExitReason.TAKE_PROFIT,
        )
        for i, r in enumerate(r_values)
    ]


def test_ci_rejects_an_edge_that_the_point_estimate_would_have_approved() -> None:
    """El caso que justifica el criterio entero.

    Expectativa puntual por encima del liston, pero con una dispersion tan
    grande que el intervalo incluye el cero: con esta muestra, un sistema sin
    ninguna ventaja habria dado este mismo resultado.
    """
    # Media +0.125R, desviacion ~2.9R: el IC al 95% es de +-0.28R aprox.
    values = [3.0 if i % 2 == 0 else -2.75 for i in range(MIN_TRADES)]
    report = evaluate_graduation(_varied(values), resamples=2000)

    assert _by_name(report, "expectancy_r").passed is True  # la puntual pasa
    assert _by_name(report, "expectancy_ci").passed is False  # el IC no
    assert report.met is False


def test_ci_passes_when_the_edge_is_clear() -> None:
    report = evaluate_graduation(_trades(MIN_TRADES, r=0.5), resamples=2000)
    assert _by_name(report, "expectancy_ci").passed is True


# ----------------------------------------------------------------------
# Walk-forward: que la decision sobreviva a datos que no vio
# ----------------------------------------------------------------------


def test_walk_forward_fails_when_the_edge_does_not_survive_out_of_sample() -> None:
    """Todo lo bueno al principio: in-sample promoveria, fuera de muestra pierde."""
    half = MIN_TRADES // 2
    values = [2.0] * half + [-1.0] * half
    report = evaluate_graduation(_varied(values), resamples=500)

    criterion = _by_name(report, "walk_forward")
    assert criterion.passed is False
    assert report.met is False


def test_walk_forward_does_not_count_folds_that_would_never_have_been_promoted() -> None:
    """Un conjunto de seleccion vacio no es una confirmacion.

    Si el in-sample nunca despeja el liston, no hay decision que validar. Contar
    eso como exito premiaria la ausencia de senal -- que es justo lo que midieron
    los dos pliegues de agosto de 2026.
    """
    # Expectativa siempre negativa: el in-sample no promoveria en ningun pliegue.
    report = evaluate_graduation(_trades(MIN_TRADES, r=-0.20), resamples=500)

    criterion = _by_name(report, "walk_forward")
    assert criterion.passed is False
    assert criterion.actual.startswith("0 de ")


def test_walk_forward_confirms_a_consistently_positive_system() -> None:
    report = evaluate_graduation(_trades(MIN_TRADES, r=0.5), resamples=500)
    assert _by_name(report, "walk_forward").passed is True


def test_walk_forward_reports_when_there_is_no_sample_to_split() -> None:
    report = evaluate_graduation(_trades(20, r=0.5), resamples=500)
    criterion = _by_name(report, "walk_forward")
    assert criterion.passed is False
    assert "sin muestra" in criterion.actual


def test_discarded_criterion_is_not_present() -> None:
    """P(exp>0) se descarto por redundante con el IC; que no reaparezca."""
    report = evaluate_graduation(_trades(MIN_TRADES, r=0.5), resamples=500)
    assert not any(c.name in {"p_positive", "prob_positive"} for c in report.criteria)
