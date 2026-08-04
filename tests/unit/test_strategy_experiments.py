"""Experimentos con fecha de corte por estrategia (decisión asistida).

`atr_expansion` y `mean_reversion` llevan R negativo consistente. En vez de
depender de que el operador se acuerde de revisarlas, el sistema abre un
experimento con fecha de corte y, al vencer, propone (nunca aplica) la
desactivación. Lo que se fija aquí: el veredicto es correcto, el reloj
sobrevive a un reinicio, y **nada se desactiva solo**.
"""

from datetime import datetime, timedelta
from pathlib import Path

from app.config.settings import StrategyExperimentSettings
from app.execution.models import ExitReason, PositionSide, TradeRecord
from app.execution.strategy_experiments import StrategyExperimentManager
from app.utils.time import utc_now

from tests.unit.execution_helpers import (
    make_engine,
    make_execution_settings,
    make_market_with_state,
)
from tests.unit.quant_helpers import make_candles, make_ticker

START = utc_now() - timedelta(hours=100)


def _settings(tmp_path: Path, **overrides: object) -> StrategyExperimentSettings:
    base: dict[str, object] = {
        "enabled": True,
        "watching": ["atr_expansion"],
        "deadline_hours": 72.0,
        "min_trades": 5,
        "state_path": tmp_path / "experiments.jsonl",
    }
    base.update(overrides)
    return StrategyExperimentSettings(**base)  # type: ignore[arg-type]


def _trade(strategy: str, r: float, exit_time: datetime) -> TradeRecord:
    """Operación cerrada mínima, sólo con lo que el experimento mira."""
    return TradeRecord(
        position_id="p",
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        quantity=1.0,
        entry_time=exit_time - timedelta(minutes=10),
        exit_time=exit_time,
        entry_price=100.0,
        exit_price=100.0 + r,
        r_multiple=r,
        exit_reason=ExitReason.TAKE_PROFIT if r > 0 else ExitReason.STOP_LOSS,
        strategy=strategy,
        strategy_category="volatility",
    )


def _losing_window(strategy: str = "atr_expansion", n: int = 6) -> list[TradeRecord]:
    return [_trade(strategy, -0.6, START + timedelta(hours=i + 1)) for i in range(n)]


# ----------------------------------------------------------------------
# Apertura
# ----------------------------------------------------------------------


def test_opening_is_idempotent_and_does_not_restart_the_clock(tmp_path: Path):
    manager = StrategyExperimentManager(_settings(tmp_path))

    first = manager.open_pending(now=START)
    again = manager.open_pending(now=START + timedelta(hours=10))

    assert [e.strategy for e in first] == ["atr_expansion"]
    assert again == [], "una estrategia ya observada no reabre su experimento"
    assert first[0].deadline == START + timedelta(hours=72)


def test_disabled_manager_does_nothing(tmp_path: Path):
    manager = StrategyExperimentManager(_settings(tmp_path, enabled=False))

    assert manager.open_pending(now=START) == []
    assert manager.evaluate(_losing_window(), now=START + timedelta(hours=200)) == []


# ----------------------------------------------------------------------
# Veredicto
# ----------------------------------------------------------------------


def test_no_verdict_before_the_deadline(tmp_path: Path):
    manager = StrategyExperimentManager(_settings(tmp_path))
    manager.open_pending(now=START)

    verdicts = manager.evaluate(_losing_window(), now=START + timedelta(hours=71))

    assert verdicts == []


def test_persistently_negative_strategy_becomes_a_candidate(tmp_path: Path):
    manager = StrategyExperimentManager(_settings(tmp_path))
    manager.open_pending(now=START)

    (verdict,) = manager.evaluate(_losing_window(), now=START + timedelta(hours=73))

    assert verdict.outcome == "deactivation_candidate"
    assert verdict.strategy == "atr_expansion"
    assert verdict.trades == 6
    assert verdict.expectancy_r < 0
    assert manager.candidates() == ["atr_expansion"]


def test_a_candidate_is_proposed_but_never_disabled(tmp_path: Path):
    """El punto 2 del bloque: preparar el mecanismo, no aplicar la decisión."""
    execution = make_execution_settings()
    manager = StrategyExperimentManager(_settings(tmp_path))
    manager.open_pending(now=START)
    manager.evaluate(_losing_window(), now=START + timedelta(hours=73))

    assert manager.candidates() == ["atr_expansion"]
    assert execution.strategies_enabled == {}, "el toggle no se toca solo"
    assert execution.strategies_enabled.get("atr_expansion", True) is True


def test_a_recovered_strategy_closes_without_action(tmp_path: Path):
    manager = StrategyExperimentManager(_settings(tmp_path))
    manager.open_pending(now=START)
    winners = [_trade("atr_expansion", 0.8, START + timedelta(hours=i + 1)) for i in range(6)]

    (verdict,) = manager.evaluate(winners, now=START + timedelta(hours=73))

    assert verdict.outcome == "passed"
    assert verdict.expectancy_r > 0
    assert manager.candidates() == []


def test_insufficient_sample_extends_the_window_instead_of_judging(tmp_path: Path):
    """Juzgar con 2 operaciones sería ruido, no evidencia."""
    manager = StrategyExperimentManager(_settings(tmp_path, extension_hours=24.0))
    manager.open_pending(now=START)
    now = START + timedelta(hours=73)

    (verdict,) = manager.evaluate(_losing_window(n=2), now=now)

    assert verdict.outcome == "extended"
    assert manager.candidates() == []
    (experiment,) = manager.experiments
    assert experiment.closed is False
    assert experiment.deadline == now + timedelta(hours=24)


def test_trades_closed_before_the_experiment_are_ignored(tmp_path: Path):
    """La ventana empieza con el experimento: mezclar operaciones anteriores al
    cambio de holding mediría justo lo que el cambio pretendía arreglar."""
    manager = StrategyExperimentManager(_settings(tmp_path, min_trades=3))
    manager.open_pending(now=START)
    old = [_trade("atr_expansion", -2.0, START - timedelta(hours=i + 1)) for i in range(10)]
    inside = [_trade("atr_expansion", 0.5, START + timedelta(hours=i + 1)) for i in range(4)]

    (verdict,) = manager.evaluate(old + inside, now=START + timedelta(hours=73))

    assert verdict.trades == 4
    assert verdict.outcome == "passed"


def test_other_strategies_do_not_pollute_the_verdict(tmp_path: Path):
    manager = StrategyExperimentManager(_settings(tmp_path, min_trades=3))
    manager.open_pending(now=START)
    noise = [_trade("order_block", 5.0, START + timedelta(hours=i + 1)) for i in range(10)]

    (verdict,) = manager.evaluate(_losing_window(n=4) + noise, now=START + timedelta(hours=73))

    assert verdict.trades == 4
    assert verdict.outcome == "deactivation_candidate"


# ----------------------------------------------------------------------
# Persistencia
# ----------------------------------------------------------------------


def test_the_clock_survives_a_restart(tmp_path: Path):
    """Sin rehidratar, cada reinicio reiniciaría el reloj y la fecha de corte
    no llegaría nunca."""
    settings = _settings(tmp_path)
    StrategyExperimentManager(settings).open_pending(now=START)

    revived = StrategyExperimentManager(settings)
    assert revived.open_pending(now=START + timedelta(hours=50)) == []
    (experiment,) = revived.experiments
    assert experiment.started_at == START
    assert experiment.deadline == START + timedelta(hours=72)


def test_an_extended_deadline_survives_a_restart(tmp_path: Path):
    settings = _settings(tmp_path, extension_hours=24.0)
    manager = StrategyExperimentManager(settings)
    manager.open_pending(now=START)
    now = START + timedelta(hours=73)
    manager.evaluate(_losing_window(n=1), now=now)

    revived = StrategyExperimentManager(settings)
    revived.open_pending(now=now)
    (experiment,) = revived.experiments
    assert experiment.closed is False
    assert experiment.deadline == now + timedelta(hours=24)


def test_a_closed_experiment_is_not_re_adjudicated_after_a_restart(tmp_path: Path):
    settings = _settings(tmp_path)
    manager = StrategyExperimentManager(settings)
    manager.open_pending(now=START)
    manager.evaluate(_losing_window(), now=START + timedelta(hours=73))

    revived = StrategyExperimentManager(settings)
    revived.open_pending(now=START + timedelta(hours=100))
    assert revived.evaluate(_losing_window(), now=START + timedelta(hours=100)) == []


# ----------------------------------------------------------------------
# Toggle por estrategia en el motor
# ----------------------------------------------------------------------


def _decision(strategy: str):
    from app.engine.events import DecisionGenerated

    return DecisionGenerated(
        source="test",
        decision_id="d1",
        symbol="BTCUSDT",
        action="open_long",
        accepted=True,
        score=80.0,
        confidence=0.8,
        summary="s",
        strategy=strategy,
        strategy_category="volatility",
    )


def _market():
    return make_market_with_state(
        candles=make_candles([100.0, 101.0, 100.5, 101.5, 102.0] * 6),
        ticker=make_ticker(bid=101.9, ask=101.95),
    )


async def test_strategy_toggle_blocks_only_that_strategy():
    market, _ = _market()
    engine = make_engine(
        market, make_execution_settings(strategies_enabled={"atr_expansion": False})
    )

    assert await engine.process_decision(_decision("atr_expansion")) is None
    assert await engine.process_decision(_decision("order_block")) is not None


async def test_unattributed_decisions_are_not_blocked_by_the_toggle():
    """Una decisión sin atribución (p. ej. posición adoptada) no se bloquea."""
    market, _ = _market()
    engine = make_engine(
        market, make_execution_settings(strategies_enabled={"atr_expansion": False})
    )

    assert await engine.process_decision(_decision("")) is not None


async def test_engine_publishes_the_verdict_instead_of_acting_on_it(tmp_path: Path):
    """El motor publica el veredicto; Discord lo convierte en aviso. El motor
    no toca `strategies_enabled`."""
    market, _ = _market()
    settings = make_execution_settings()
    settings.experiments.watching = ["atr_expansion"]
    settings.experiments.min_trades = 1
    settings.experiments.state_path = tmp_path / "exp.jsonl"
    engine = make_engine(market, settings)

    opened = await engine.run_strategy_experiments()
    assert opened == [], "recién abierto, aún no vence"

    # Se fuerza el vencimiento moviendo la fecha de corte al pasado.
    assert engine.experiments is not None
    (experiment,) = engine.experiments.experiments
    experiment.deadline = utc_now() - timedelta(seconds=1)
    engine.journal.record(_trade("atr_expansion", -1.0, utc_now()))

    (verdict,) = await engine.run_strategy_experiments()

    assert verdict.outcome == "deactivation_candidate"
    assert settings.strategies_enabled == {}, "el motor no desactiva nada"
