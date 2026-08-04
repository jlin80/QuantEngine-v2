"""Ciclo autonomo del Quant Research Lab (job del scheduler).

El laboratorio existia pero nada lo disparaba: solo se registraba su notificador
en el scheduler, asi que `experiments` se quedaba en 0 para siempre. Aqui se
cubre el job que lo ejecuta, y sobre todo que **nunca puede tumbar el motor**.
"""

import logging

from app.config.settings import Settings
from app.core.container import Container
from app.engine.engine import QuantEngine
from app.market.models import Timeframe

from tests.unit.quant_helpers import make_candles


class _FakeMarket:
    def __init__(self, candles):
        self._candles = candles
        self.asked: list[tuple[str, Timeframe, int]] = []

    def get_candles(self, symbol, timeframe, limit=100):
        self.asked.append((symbol, timeframe, limit))
        return self._candles


class _FakeLab:
    def __init__(self, *, fail_on: str | None = None):
        self.cycles: list[str] = []
        self._fail_on = fail_on

    def make_config(self, symbol, **overrides):
        return {"symbol": symbol}

    async def run_generation_cycle(self, symbol, timeframe, candles, config, **kw):
        if symbol == self._fail_on:
            raise RuntimeError("boom")
        self.cycles.append(symbol)
        return {"generated": 3, "qualified": 1}


def _engine(market, symbols, *, candles_needed=100):
    settings = Settings()
    settings.research.cycle_symbols = symbols
    settings.research.cycle_timeframe = "1m"
    settings.research.cycle_candles = candles_needed
    # Presupuesto abierto: estas pruebas cubren el ciclo en si, no sus limites.
    # `start == end` significa "ventana siempre abierta" (ver `in_window`), y el
    # tope de simbolos se sube para no truncar los casos multi-simbolo.
    settings.research.budget.window_start_hour_utc = 0
    settings.research.budget.window_end_hour_utc = 0
    settings.research.budget.max_symbols_per_run = 10
    container = Container()
    container.register_instance(type(market), market)
    engine = QuantEngine.__new__(QuantEngine)
    engine._settings = settings
    engine._container = container
    engine._log = logging.getLogger("test")
    return engine


async def test_cycle_runs_for_each_configured_symbol():
    market = _FakeMarket(make_candles([100.0 + i * 0.1 for i in range(200)]))
    engine = _engine(market, ["ETHUSDM", "USTECM"])
    engine._container.register_instance(
        __import__("app.market.services", fromlist=["MarketDataService"]).MarketDataService,
        market,
    )
    lab = _FakeLab()

    await engine._run_research_cycle(lab)

    assert lab.cycles == ["ETHUSDM", "USTECM"]


async def test_cycle_skips_symbols_without_enough_history():
    market = _FakeMarket(make_candles([100.0] * 20))  # < 100 velas
    engine = _engine(market, ["ETHUSDM"])
    engine._container.register_instance(
        __import__("app.market.services", fromlist=["MarketDataService"]).MarketDataService,
        market,
    )
    lab = _FakeLab()

    await engine._run_research_cycle(lab)

    assert lab.cycles == []  # se salta, no revienta


async def test_a_failing_symbol_does_not_stop_the_rest():
    """El laboratorio nunca puede tumbar el motor que esta operando."""
    market = _FakeMarket(make_candles([100.0 + i * 0.1 for i in range(200)]))
    engine = _engine(market, ["ETHUSDM", "USTECM"])
    engine._container.register_instance(
        __import__("app.market.services", fromlist=["MarketDataService"]).MarketDataService,
        market,
    )
    lab = _FakeLab(fail_on="ETHUSDM")

    await engine._run_research_cycle(lab)  # no debe propagar

    assert lab.cycles == ["USTECM"]


async def test_cycle_without_symbols_is_a_noop():
    market = _FakeMarket(make_candles([100.0] * 200))
    engine = _engine(market, [])
    engine._settings.market.symbols = []
    engine._container.register_instance(
        __import__("app.market.services", fromlist=["MarketDataService"]).MarketDataService,
        market,
    )
    lab = _FakeLab()

    await engine._run_research_cycle(lab)

    assert lab.cycles == []
