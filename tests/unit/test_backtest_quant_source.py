"""QuantCoreDecisionSource: el backtest corre las estrategias reales.

Verifica que el adaptador reproduce el pipeline en vivo (estrategias → señales →
consenso → filtros → decisión) de forma síncrona sobre datos históricos, y que
se integra con el BacktestLab produciendo estadística sin errores.
"""

import math

from app.backtesting.api import BacktestLab
from app.backtesting.optimizer import ParameterSpace
from app.backtesting.quant_source import (
    QuantCoreDecisionSource,
    make_quant_source_factory,
    run_quantcore_backtest,
)
from app.config.settings import Settings

from tests.unit.quant_helpers import make_candles


def _eth_candles(n: int = 300) -> list:
    closes: list[float] = []
    price = 1800.0
    for i in range(n):
        price += math.sin(i / 12.0) * 3.0 + (1.0 if i % 7 else -2.0)
        closes.append(price)
    opens = [closes[0], *closes[:-1]]
    return make_candles(closes, symbol="ETHUSDM", opens=opens, range_pad=1.0)


def _settings() -> Settings:
    s = Settings()
    s.quant.enabled = True
    # No bloquear por spread/sesión en el test: interesa el pipeline, no los filtros.
    s.quant.context.max_spread_bps = 50.0
    s.quant.filters.always_open_symbols = ["ETHUSDM"]
    return s


def test_source_loads_the_real_strategy_library():
    s = _settings()
    source = QuantCoreDecisionSource(s, "ETHUSDM", spread_bps=5.3)
    try:
        candles = _eth_candles(60)
        # Forzar la inicialización corriendo una vela.
        source.decide("ETHUSDM", candles, 40)
        assert len(source._strategies) == 20  # las 20 estrategias de vela 1m
    finally:
        source.close()


def test_source_produces_accepted_open_decisions():
    s = _settings()
    source = QuantCoreDecisionSource(s, "ETHUSDM", spread_bps=5.3)
    try:
        candles = _eth_candles(300)
        opens = 0
        for i in range(len(candles)):
            d = source.decide("ETHUSDM", candles, i)
            if d is not None:
                assert d.accepted
                assert d.action in ("open_long", "open_short")
                opens += 1
        assert opens > 0  # el pipeline real sí abre en una serie con tendencia
    finally:
        source.close()


def test_source_respects_the_spread_filter():
    """Con spread por encima del máximo, ninguna decisión pasa (como en vivo)."""
    s = Settings()
    s.quant.enabled = True
    s.quant.context.max_spread_bps = 1.0  # spread 5.3 lo supera siempre
    s.quant.filters.always_open_symbols = ["ETHUSDM"]
    source = QuantCoreDecisionSource(s, "ETHUSDM", spread_bps=5.3)
    try:
        candles = _eth_candles(200)
        opens = sum(1 for i in range(len(candles)) if source.decide("ETHUSDM", candles, i))
        assert opens == 0
    finally:
        source.close()


def test_integrates_with_backtest_lab():
    s = _settings()
    lab = BacktestLab(s)
    source = QuantCoreDecisionSource(s, "ETHUSDM", spread_bps=5.3)
    try:
        candles = _eth_candles(400)
        config = lab.make_config(
            "ETHUSDM", timeframe="1m", label="eth-test", spread_bps=5.3, initial_balance=1000.0
        )
        result = lab.run_backtest(candles, source, config)
        assert "total_trades" in result.statistics
        assert result.statistics["total_trades"] >= 1
    finally:
        source.close()


def test_run_quantcore_backtest_returns_real_and_zero_spread():
    """La orquestación reporta el escenario real y el de control (spread 0)."""
    s = _settings()
    candles = _eth_candles(400)
    r = run_quantcore_backtest(s, "ETHUSDM", candles, spread_bps=5.3)
    assert r["symbol"] == "ETHUSDM"
    assert r["bars"] == 400
    assert r["spread_bps"] == 5.3
    # Ambos escenarios se ejecutan y reportan métricas.
    for key in ("trades", "profit_factor", "return_pct", "zero_spread_trades"):
        assert key in r
    # Aquí había un `return_pct <= zero_spread_return_pct`, que da por supuesto
    # que abaratar el coste no puede empeorar el resultado. **No se sostiene**:
    # el coste cambia la curva de equity, que cambia el sizing, que cambia qué
    # operaciones llegan a abrirse. Medido el 2026-08-21 sobre XAUUSDM, quitar
    # la comisión llevó la corrida de 1.864 a 5.786 operaciones, y las nuevas
    # eran peores. Los dos escenarios son poblaciones distintas, no la misma con
    # distinto descuento, así que sólo se exige que ambos midan algo.
    assert int(r["trades"]) >= 0
    assert int(r["zero_spread_trades"]) >= 0


def test_source_factory_applies_entry_thresholds():
    """La fábrica inyecta los umbrales de entrada en una copia independiente."""
    s = _settings()
    factory = make_quant_source_factory(s, "ETHUSDM", spread_bps=5.3)
    source = factory({"min_score": 88.0, "min_confidence": 0.9, "min_agreement": 0.6})
    try:
        # La copia del trial recibe los umbrales; el settings base no se toca.
        assert source._settings.quant.consensus.min_score == 88.0
        assert s.quant.consensus.min_score != 88.0
    finally:
        source.close()


def test_walk_forward_runs_over_the_quant_source():
    """El walk-forward optimiza in-sample y valida out-of-sample sin errores."""
    s = _settings()
    candles = _eth_candles(1200)
    space = ParameterSpace().add_choices("min_score", [60.0, 70.0])
    factory = make_quant_source_factory(s, "ETHUSDM", spread_bps=5.3)
    lab = BacktestLab(s)
    config = lab.make_config("ETHUSDM", timeframe="1m", label="wf-test", spread_bps=5.3)
    report = lab.run_walk_forward(
        candles, space, factory, config, method="grid", objective="profit_factor"
    )
    assert report.folds  # produjo al menos un pliegue
    for fold in report.folds:
        assert "min_score" in fold.best_params


def test_decisions_carry_the_strategy_attribution():
    """Sin atribución el laboratorio ignora los toggles y el holding por estrategia.

    `ExecutionEngine` comprueba `decision.strategy` para aplicar
    `execution.strategies_enabled` y el holding mínimo por estrategia del
    Bloque 1. Con la cadena vacía no bloquea ni resuelve nada, así que el
    backtest operaba estrategias que producción tiene apagadas.
    """
    s = _settings()
    source = QuantCoreDecisionSource(s, "ETHUSDM", spread_bps=5.3)
    try:
        candles = _eth_candles(300)
        decisions = [d for i in range(len(candles)) if (d := source.decide("ETHUSDM", candles, i))]
        assert decisions, "la serie debe producir alguna apertura"
        assert all(d.strategy for d in decisions)
        assert all(d.signal_ids for d in decisions)
        # La categoría sale del mapa de la decisión; si se conoce la estrategia
        # se conoce su directorio de categoría.
        assert all(d.strategy_category for d in decisions)
    finally:
        source.close()
