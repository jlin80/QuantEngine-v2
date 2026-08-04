"""Saneamiento del training set del ML (Bloque 4).

La preocupación de fondo: **no queremos que el ML aprenda de los bugs de
ejecución que ya arreglamos**. Un modelo entrenado sobre operaciones con el stop
mal calculado no aprende "esta señal es mala": aprende "esta señal es mala
porque la ejecución la saboteó", y acaba penalizando contextos que sí tenían
edge.

Aquí se fija el contrato de las dos defensas: ponderación/exclusión por era, y
la separación entre la etiqueta de calidad de señal y la de calidad de ejecución.
"""

from datetime import UTC, datetime, timedelta

from app.config.settings import MLDataQualitySettings, MLEraSettings
from app.execution.models import ExitReason
from app.ml.datasets import (
    CLEAN_ERA,
    SIGNAL_LABEL,
    DatasetBuilder,
    era_of,
    era_summary,
    weight_of,
)

from tests.unit.ml_helpers import make_trade

# Fechas documentadas en la bitácora.
PRE_CONTRACT_SIZE = datetime(2026, 7, 20, tzinfo=UTC)  # antes del 27/07
PRE_TRAILING = datetime(2026, 7, 28, tzinfo=UTC)  # entre el 27/07 y el 29/07
CLEAN = datetime(2026, 8, 1, tzinfo=UTC)  # tras todos los fixes


def _quality(**overrides: object) -> MLDataQualitySettings:
    return MLDataQualitySettings(**overrides)  # type: ignore[arg-type]


def _trade(entry: datetime, **overrides: object):
    return make_trade(
        entry_time=entry,
        exit_time=entry + timedelta(minutes=45),
        **overrides,  # type: ignore[arg-type]
    )


# ----------------------------------------------------------------------
# Segmentación por era (punto 1 del bloque)
# ----------------------------------------------------------------------


def test_each_trade_is_assigned_to_its_execution_era():
    settings = _quality()

    assert era_of(_trade(PRE_CONTRACT_SIZE), settings) == "pre_contract_size_y_familias_regimen"
    assert era_of(_trade(PRE_TRAILING), settings) == "pre_trailing_activate_r"
    assert era_of(_trade(CLEAN), settings) == CLEAN_ERA


def test_a_trade_is_classified_by_entry_time_not_exit_time():
    """Una operación abierta antes de un fix corrió bajo las reglas viejas casi
    toda su vida, aunque cerrara después. Es la lectura conservadora."""
    settings = _quality()
    straddling = make_trade(
        entry_time=datetime(2026, 7, 26, 23, 0, tzinfo=UTC),
        exit_time=datetime(2026, 7, 27, 3, 0, tzinfo=UTC),
    )

    assert era_of(straddling, settings) == "pre_contract_size_y_familias_regimen"


def test_the_invalid_measurement_era_is_excluded_outright():
    """Un stop mal calculado no da una muestra floja: da una medición inválida."""
    settings = _quality()

    assert weight_of(_trade(PRE_CONTRACT_SIZE), settings) == 0.0


def test_the_bounded_bias_era_is_kept_with_reduced_weight():
    settings = _quality()

    weight = weight_of(_trade(PRE_TRAILING), settings)

    assert 0.0 < weight < 1.0


def test_clean_history_weighs_full():
    assert weight_of(_trade(CLEAN), _quality()) == 1.0


def test_disabling_the_sanitation_restores_the_previous_behaviour():
    """Se conserva el interruptor para poder medir el efecto del cambio."""
    settings = _quality(enabled=False)

    assert weight_of(_trade(PRE_CONTRACT_SIZE), settings) == 1.0
    assert weight_of(_trade(CLEAN), settings) == 1.0


# ----------------------------------------------------------------------
# Lo que exige el punto 4 del bloque
# ----------------------------------------------------------------------


def test_a_trade_from_a_buggy_era_never_enters_with_the_same_weight_as_a_clean_one():
    """El test que pide explícitamente el bloque."""
    builder = DatasetBuilder(data_quality=_quality())
    trades = [
        _trade(PRE_CONTRACT_SIZE),
        _trade(PRE_TRAILING),
        _trade(CLEAN),
    ]

    dataset = builder.build(trades)

    weights = dataset.metadata["sample_weights"]
    eras = dataset.metadata["sample_eras"]
    # La era de medición inválida no entra en absoluto.
    assert "pre_contract_size_y_familias_regimen" not in eras
    assert dataset.metadata["dropped_by_era"] == 1
    # Y la de sesgo acotado entra pesando menos que la limpia.
    by_era = dict(zip(eras, weights, strict=True))
    assert by_era["pre_trailing_activate_r"] < by_era[CLEAN_ERA]


def test_sample_weights_stay_aligned_with_rows_after_a_split():
    """Un split desalineado haría que cada muestra heredase el peso de otra."""
    builder = DatasetBuilder(data_quality=_quality())
    trades = [_trade(PRE_TRAILING + timedelta(hours=i)) for i in range(4)]
    trades += [_trade(CLEAN + timedelta(hours=i)) for i in range(4)]

    dataset = builder.build(trades)
    train, test = dataset.split(test_size=0.5)

    assert len(train.sample_weights) == len(train)
    assert len(test.sample_weights) == len(test)
    assert train.sample_weights == dataset.sample_weights[: len(train)]
    assert test.sample_weights == dataset.sample_weights[len(train) :]


def test_the_dataset_documents_where_its_data_came_from():
    builder = DatasetBuilder(data_quality=_quality())
    trades = [_trade(PRE_CONTRACT_SIZE), _trade(CLEAN)]

    breakdown = builder.build(trades).metadata["era_breakdown"]

    assert breakdown["total_trades"] == 2
    assert breakdown["eligible_trades"] == 1
    assert breakdown["excluded_trades"] == 1
    excluded = next(r for r in breakdown["eras"] if r["excluded"])
    assert excluded["reason"], "una exclusión sin motivo escrito no es auditable"


# ----------------------------------------------------------------------
# Etiqueta dual: señal vs. ejecución (punto 2b del bloque)
# ----------------------------------------------------------------------


def test_the_signal_label_ignores_trades_the_execution_closed():
    """Una operación cerrada por cambio de régimen nunca puso a prueba su tesis:
    etiquetarla como 'señal mala' es justo el error que se quiere evitar."""
    builder = DatasetBuilder(data_quality=_quality())
    trades = [
        _trade(CLEAN, exit_reason=ExitReason.REGIME_CHANGE, r_multiple=-0.4),
        _trade(CLEAN, exit_reason=ExitReason.TIME_EXIT, r_multiple=-0.2),
        _trade(CLEAN, exit_reason=ExitReason.TAKE_PROFIT, r_multiple=2.0),
        _trade(CLEAN, exit_reason=ExitReason.STOP_LOSS, r_multiple=-1.0),
    ]

    dataset = builder.build(trades, label=SIGNAL_LABEL)

    assert len(dataset) == 2, "sólo las salidas que resuelven la tesis"
    assert dataset.metadata["dropped_not_signal_verdict"] == 2
    assert sorted(dataset.y) == [0, 1]


def test_the_execution_label_keeps_every_exit_reason():
    """La contraparte: para medir la ejecución sí cuentan todas las salidas."""
    builder = DatasetBuilder(data_quality=_quality())
    trades = [
        _trade(CLEAN, exit_reason=ExitReason.REGIME_CHANGE),
        _trade(CLEAN, exit_reason=ExitReason.TAKE_PROFIT),
    ]

    dataset = builder.build(trades, label="win")

    assert len(dataset) == 2
    assert dataset.metadata["dropped_not_signal_verdict"] == 0


def test_each_dataset_declares_what_it_measures():
    """Confundir las dos etiquetas es el fallo que este bloque evita."""
    builder = DatasetBuilder(data_quality=_quality())
    trades = [_trade(CLEAN, exit_reason=ExitReason.TAKE_PROFIT)]

    assert builder.build(trades, label="win").metadata["label_measures"] == "execution"
    assert builder.build(trades, label=SIGNAL_LABEL).metadata["label_measures"] == "signal"


# ----------------------------------------------------------------------
# Configurabilidad
# ----------------------------------------------------------------------


def test_eras_are_declarative_and_can_be_extended_without_touching_code():
    """Cuando se arregle el próximo bug de ejecución, basta añadir una era."""
    settings = _quality(
        eras=[
            MLEraSettings(
                name="pre_fix_futuro",
                until="2026-09-01",
                weight=0.0,
                reason="bug hipotético",
            )
        ]
    )

    assert era_of(_trade(CLEAN), settings) == "pre_fix_futuro"
    assert weight_of(_trade(CLEAN), settings) == 0.0
    assert era_of(_trade(datetime(2026, 9, 2, tzinfo=UTC)), settings) == CLEAN_ERA


def test_the_summary_reports_every_era_even_with_no_trades():
    summary = era_summary([], _quality())

    assert summary["total_trades"] == 0
    assert [row["era"] for row in summary["eras"]][-1] == CLEAN_ERA
