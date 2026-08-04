"""Falsacion automatica del cambio de holding por estrategia (Bloque 7.1).

"Se desplego sin errores" no es evidencia de que un cambio funcione. El Bloque 1
hizo tres predicciones concretas y esto las mide solo en la ventana posterior.

Lo que se fija aqui: el veredicto se emite **acierte o falle** (una prediccion
que solo se reporta cuando se cumple no es una falsacion), la muestra corta
extiende la ventana en vez de concluir con ruido, y nada de esto cambia
configuracion.
"""

from datetime import timedelta
from pathlib import Path

from app.config.settings import ExecutionSettings, FalsificationSettings
from app.execution.falsification import HoldingChangeFalsifier
from app.execution.models import ExitReason, PositionSide, TradeRecord
from app.utils.time import utc_now

START = utc_now() - timedelta(hours=100)


def _settings(tmp_path: Path, **overrides: object) -> FalsificationSettings:
    base: dict[str, object] = {
        "enabled": True,
        "window_hours": 48.0,
        "min_trades": 4,
        "state_path": tmp_path / "falsification.jsonl",
    }
    base.update(overrides)
    return FalsificationSettings(**base)  # type: ignore[arg-type]


def _falsifier(tmp_path: Path, **overrides: object) -> HoldingChangeFalsifier:
    return HoldingChangeFalsifier(_settings(tmp_path, **overrides), ExecutionSettings())


def _trade(
    reason: ExitReason,
    *,
    offset_hours: int = 1,
    holding_seconds: float = 1000.0,
    strategy: str = "order_block",
) -> TradeRecord:
    exit_time = START + timedelta(hours=offset_hours)
    return TradeRecord(
        position_id="p",
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        quantity=1.0,
        entry_time=exit_time - timedelta(seconds=holding_seconds),
        exit_time=exit_time,
        entry_price=100.0,
        exit_price=101.0,
        exit_reason=reason,
        strategy=strategy,
        strategy_category="smc",
    )


# ----------------------------------------------------------------------
# Ventana
# ----------------------------------------------------------------------


def test_the_window_opens_once_and_survives_a_restart(tmp_path: Path):
    settings = _settings(tmp_path)
    first = HoldingChangeFalsifier(settings, ExecutionSettings())

    assert first.start(now=START) is True
    assert first.start(now=START + timedelta(hours=10)) is False

    revived = HoldingChangeFalsifier(settings, ExecutionSettings())
    assert revived.start(now=START + timedelta(hours=20)) is False
    assert revived.status()["started_at"] == START.isoformat()


def test_no_verdict_before_the_window_closes(tmp_path: Path):
    falsifier = _falsifier(tmp_path)
    falsifier.start(now=START)

    trades = [_trade(ExitReason.TAKE_PROFIT, offset_hours=i + 1) for i in range(10)]

    assert falsifier.evaluate(trades, now=START + timedelta(hours=47)) is None


def test_a_disabled_falsifier_does_nothing(tmp_path: Path):
    falsifier = _falsifier(tmp_path, enabled=False)

    assert falsifier.start(now=START) is False
    assert falsifier.evaluate([], now=START + timedelta(hours=100)) is None


# ----------------------------------------------------------------------
# Las tres predicciones del Bloque 1
# ----------------------------------------------------------------------


def _healthy_window() -> list[TradeRecord]:
    """Mezcla de salidas sana: hay take_profit y el regimen no domina."""
    return [
        _trade(ExitReason.TAKE_PROFIT, offset_hours=1, holding_seconds=2000.0),
        _trade(ExitReason.TAKE_PROFIT, offset_hours=2, holding_seconds=2100.0),
        _trade(ExitReason.STOP_LOSS, offset_hours=3, holding_seconds=1900.0),
        _trade(ExitReason.REGIME_CHANGE, offset_hours=4, holding_seconds=2000.0),
    ]


def test_a_healthy_exit_mix_confirms_the_change(tmp_path: Path):
    falsifier = _falsifier(tmp_path)
    falsifier.start(now=START)

    verdict = falsifier.evaluate(_healthy_window(), now=START + timedelta(hours=49))

    assert verdict is not None
    assert verdict.outcome == "confirmed"
    assert verdict.confirmed is True
    assert all(verdict.checks.values())


def test_zero_take_profit_refutes_the_change(tmp_path: Path):
    """Si ninguna operacion llega al objetivo, el holding sigue cortando."""
    falsifier = _falsifier(tmp_path)
    falsifier.start(now=START)
    trades = [
        _trade(ExitReason.STOP_LOSS, offset_hours=i + 1, holding_seconds=2000.0) for i in range(6)
    ]

    verdict = falsifier.evaluate(trades, now=START + timedelta(hours=49))

    assert verdict is not None
    assert verdict.outcome == "refuted"
    assert verdict.checks["take_profit_above_zero"] is False
    assert verdict.take_profit_pct == 0.0


def test_regime_change_still_dominating_refutes_the_change(tmp_path: Path):
    """Era el sintoma original: el 80% de las salidas por regimen."""
    falsifier = _falsifier(tmp_path)
    falsifier.start(now=START)
    trades = [
        _trade(ExitReason.REGIME_CHANGE, offset_hours=i + 1, holding_seconds=2000.0)
        for i in range(9)
    ]
    trades.append(_trade(ExitReason.TAKE_PROFIT, offset_hours=10, holding_seconds=2000.0))

    verdict = falsifier.evaluate(trades, now=START + timedelta(hours=49))

    assert verdict is not None
    assert verdict.outcome == "refuted"
    assert verdict.checks["regime_change_below_threshold"] is False
    assert verdict.regime_change_pct == 90.0


def test_a_median_duration_far_below_expected_refutes_the_change(tmp_path: Path):
    """`order_block` espera ~1950s; 60s significa que se sigue cortando pronto."""
    falsifier = _falsifier(tmp_path)
    falsifier.start(now=START)
    trades = [
        _trade(ExitReason.TAKE_PROFIT, offset_hours=1, holding_seconds=60.0),
        _trade(ExitReason.STOP_LOSS, offset_hours=2, holding_seconds=55.0),
        _trade(ExitReason.STOP_LOSS, offset_hours=3, holding_seconds=70.0),
        _trade(ExitReason.STOP_LOSS, offset_hours=4, holding_seconds=50.0),
    ]

    verdict = falsifier.evaluate(trades, now=START + timedelta(hours=49))

    assert verdict is not None
    assert verdict.outcome == "refuted"
    assert verdict.checks["median_duration_near_expected"] is False
    assert verdict.expected_holding_seconds == 1950.0


def test_a_longer_than_expected_duration_is_not_a_failure(tmp_path: Path):
    """Pasarse de largo ya lo acota el limite global de 4h; no es este fallo."""
    falsifier = _falsifier(tmp_path)
    falsifier.start(now=START)
    trades = [
        _trade(ExitReason.TAKE_PROFIT, offset_hours=1, holding_seconds=6000.0),
        _trade(ExitReason.TAKE_PROFIT, offset_hours=2, holding_seconds=6100.0),
        _trade(ExitReason.STOP_LOSS, offset_hours=3, holding_seconds=5900.0),
        _trade(ExitReason.STOP_LOSS, offset_hours=4, holding_seconds=6000.0),
    ]

    verdict = falsifier.evaluate(trades, now=START + timedelta(hours=49))

    assert verdict is not None
    assert verdict.checks["median_duration_near_expected"] is True


# ----------------------------------------------------------------------
# Muestra y expectativa por estrategia
# ----------------------------------------------------------------------


def test_insufficient_sample_extends_instead_of_concluding(tmp_path: Path):
    falsifier = _falsifier(tmp_path, min_trades=20, extension_hours=24.0)
    falsifier.start(now=START)
    trades = [_trade(ExitReason.TAKE_PROFIT, offset_hours=1)]

    verdict = falsifier.evaluate(trades, now=START + timedelta(hours=49))

    assert verdict is not None
    assert verdict.outcome == "pending"
    assert falsifier.verdict is None, "no se cierra: la ventana sigue abierta"


def test_the_expected_duration_is_per_strategy_not_global(tmp_path: Path):
    """Es justo lo que introdujo el Bloque 1: cada estrategia tiene su umbral."""
    falsifier = _falsifier(tmp_path)
    falsifier.start(now=START)
    trades = [
        _trade(ExitReason.TAKE_PROFIT, offset_hours=1, holding_seconds=200.0, strategy="bos"),
        _trade(ExitReason.STOP_LOSS, offset_hours=2, holding_seconds=200.0, strategy="bos"),
        _trade(ExitReason.STOP_LOSS, offset_hours=3, holding_seconds=200.0, strategy="bos"),
        _trade(ExitReason.STOP_LOSS, offset_hours=4, holding_seconds=200.0, strategy="bos"),
    ]

    verdict = falsifier.evaluate(trades, now=START + timedelta(hours=49))

    assert verdict is not None
    # `bos` espera 135s, no los 1950s de `order_block`: 200s es correcto.
    assert verdict.expected_holding_seconds == 135.0
    assert verdict.checks["median_duration_near_expected"] is True


def test_trades_closed_before_the_window_are_ignored(tmp_path: Path):
    falsifier = _falsifier(tmp_path)
    falsifier.start(now=START)
    old = [
        _trade(ExitReason.REGIME_CHANGE, offset_hours=-i - 1, holding_seconds=10.0)
        for i in range(50)
    ]

    verdict = falsifier.evaluate(old + _healthy_window(), now=START + timedelta(hours=49))

    assert verdict is not None
    assert verdict.trades == 4


def test_the_verdict_is_emitted_only_once(tmp_path: Path):
    falsifier = _falsifier(tmp_path)
    falsifier.start(now=START)
    now = START + timedelta(hours=49)

    assert falsifier.evaluate(_healthy_window(), now=now) is not None
    assert falsifier.evaluate(_healthy_window(), now=now) is None


def test_a_refuted_verdict_is_still_reported(tmp_path: Path):
    """Una prediccion que solo se reporta cuando se cumple no es una falsacion."""
    falsifier = _falsifier(tmp_path)
    falsifier.start(now=START)
    trades = [
        _trade(ExitReason.STOP_LOSS, offset_hours=i + 1, holding_seconds=2000.0) for i in range(6)
    ]

    verdict = falsifier.evaluate(trades, now=START + timedelta(hours=49))

    assert verdict is not None
    assert verdict.outcome == "refuted"
    assert "no" in verdict.detail.lower()
    assert falsifier.status()["verdict"]["outcome"] == "refuted"


def test_the_verdict_survives_a_restart(tmp_path: Path):
    settings = _settings(tmp_path)
    first = HoldingChangeFalsifier(settings, ExecutionSettings())
    first.start(now=START)
    first.evaluate(_healthy_window(), now=START + timedelta(hours=49))

    revived = HoldingChangeFalsifier(settings, ExecutionSettings())

    assert revived.status()["verdict"]["outcome"] == "confirmed"
    assert revived.evaluate(_healthy_window(), now=START + timedelta(hours=60)) is None
