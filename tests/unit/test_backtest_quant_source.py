"""QuantCoreDecisionSource: el backtest corre las estrategias reales.

Verifica que el adaptador reproduce el pipeline en vivo (estrategias → señales →
consenso → filtros → decisión) de forma síncrona sobre datos históricos, y que
se integra con el BacktestLab produciendo estadística sin errores.
"""

import math

from app.backtesting.api import BacktestLab
from app.backtesting.quant_source import QuantCoreDecisionSource
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
