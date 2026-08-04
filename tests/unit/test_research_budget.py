"""Presupuesto de CPU del ciclo autonomo del Research Lab (Bloque 6).

El laboratorio comparte VPS con el motor que esta operando. Un ciclo de
generacion son cientos de backtests: sin techo explicito puede robarle CPU al
bucle de gestion de posiciones, que es el unico que no puede llegar tarde.

Se fijan aqui los tres limites duros (ventana horaria, tope de trabajo por
ejecucion, timeout) y los dos vetos de cortesia.
"""

import asyncio
import logging
from datetime import UTC, datetime

from app.config.settings import ResearchBudgetSettings, Settings
from app.core.container import Container
from app.engine.engine import QuantEngine
from app.market.services import MarketDataService
from app.research.budget import evaluate_budget, in_window
from app.research.rollback import ResearchRollbackMonitor

from tests.unit.quant_helpers import make_candles


def _budget(**overrides: object) -> ResearchBudgetSettings:
    return ResearchBudgetSettings(**overrides)  # type: ignore[arg-type]


def _at(hour: int) -> datetime:
    return datetime(2026, 8, 3, hour, 30, tzinfo=UTC)


# ----------------------------------------------------------------------
# Ventana horaria
# ----------------------------------------------------------------------


def test_the_window_only_opens_during_the_configured_hours():
    settings = _budget(window_start_hour_utc=1, window_end_hour_utc=5)

    assert in_window(settings, _at(2)) is True
    assert in_window(settings, _at(4)) is True
    assert in_window(settings, _at(14)) is False
    assert in_window(settings, _at(23)) is False


def test_a_window_crossing_midnight_works():
    settings = _budget(window_start_hour_utc=22, window_end_hour_utc=3)

    assert in_window(settings, _at(23)) is True
    assert in_window(settings, _at(1)) is True
    assert in_window(settings, _at(12)) is False


def test_an_equal_start_and_end_means_always_open():
    """Forma explicita de quitar la restriccion horaria sin quitar el resto."""
    settings = _budget(window_start_hour_utc=0, window_end_hour_utc=0)

    assert all(in_window(settings, _at(h)) for h in (0, 6, 13, 21))


def test_outside_the_window_the_cycle_is_denied():
    decision = evaluate_budget(
        _budget(window_start_hour_utc=1, window_end_hour_utc=5), moment=_at(15)
    )

    assert decision.allowed is False
    assert "ventana" in decision.reason


# ----------------------------------------------------------------------
# Vetos de cortesia
# ----------------------------------------------------------------------


def test_an_open_position_postpones_the_cycle():
    """El laboratorio puede esperar; la gestion de una posicion viva, no."""
    decision = evaluate_budget(
        _budget(window_start_hour_utc=0, window_end_hour_utc=0), open_positions=1
    )

    assert decision.allowed is False
    assert "posici" in decision.reason


def test_high_cpu_postpones_the_cycle():
    decision = evaluate_budget(
        _budget(window_start_hour_utc=0, window_end_hour_utc=0, skip_if_cpu_pct_above=70.0),
        cpu_pct=85.0,
    )

    assert decision.allowed is False
    assert "CPU" in decision.reason


def test_an_unreadable_cpu_sensor_does_not_block_the_cycle():
    """Un sensor mudo no para el laboratorio, igual que no degrada Safe Mode."""
    decision = evaluate_budget(
        _budget(window_start_hour_utc=0, window_end_hour_utc=0), cpu_pct=None
    )

    assert decision.allowed is True


def test_a_disabled_budget_denies_instead_of_removing_the_limits():
    """Un laboratorio sin techo en la VPS que opera es justo lo que se evita."""
    decision = evaluate_budget(_budget(enabled=False))

    assert decision.allowed is False


# ----------------------------------------------------------------------
# Limites de trabajo
# ----------------------------------------------------------------------


def test_the_decision_carries_the_work_limits():
    decision = evaluate_budget(
        _budget(
            window_start_hour_utc=0,
            window_end_hour_utc=0,
            max_symbols_per_run=3,
            max_generated_per_run=7,
            run_timeout_seconds=600.0,
            max_workers=2,
        )
    )

    assert decision.allowed is True
    assert decision.max_symbols == 3
    assert decision.max_generated == 7
    assert decision.timeout_seconds == 600.0
    assert decision.max_workers == 2


def test_limits_are_floored_to_sane_values():
    decision = evaluate_budget(
        _budget(
            window_start_hour_utc=0,
            window_end_hour_utc=0,
            max_symbols_per_run=0,
            max_generated_per_run=0,
            run_timeout_seconds=0.0,
            max_workers=0,
        )
    )

    assert decision.max_symbols >= 1
    assert decision.max_generated >= 1
    # Un timeout de 0 cancelaria el ciclo antes de empezar.
    assert decision.timeout_seconds >= 1.0
    assert decision.max_workers >= 1


# ----------------------------------------------------------------------
# Aplicacion real en el ciclo
# ----------------------------------------------------------------------


class _FakeMarket:
    def __init__(self, candles):
        self._candles = candles

    def get_candles(self, symbol, timeframe, limit=100):
        return self._candles


class _RecordingLab:
    def __init__(self, *, hang: bool = False):
        self.cycles: list[str] = []
        self.counts: list[int] = []
        self._hang = hang

    def make_config(self, symbol, **overrides):
        return {"symbol": symbol}

    async def run_generation_cycle(self, symbol, timeframe, candles, config, *, count=None):
        if self._hang:
            await asyncio.sleep(30)
        self.cycles.append(symbol)
        self.counts.append(count)
        return {"generated": count or 0, "qualified": 0}


def _engine(symbols: list[str], **budget: object) -> QuantEngine:
    settings = Settings()
    settings.research.cycle_symbols = symbols
    settings.research.cycle_timeframe = "1m"
    settings.research.budget = _budget(window_start_hour_utc=0, window_end_hour_utc=0, **budget)
    market = _FakeMarket(make_candles([100.0 + i * 0.1 for i in range(200)]))
    container = Container()
    container.register_instance(MarketDataService, market)
    engine = QuantEngine.__new__(QuantEngine)
    engine._settings = settings
    engine._container = container
    engine._log = logging.getLogger("test")
    # El motor se construye con `__new__` (sin `__init__`), asi que la vigilancia
    # de rollback hay que ponerla a mano. Se deja desactivada: estas pruebas
    # cubren el presupuesto y el ciclo, no el rollback (que tiene las suyas).
    engine._research_rollback = ResearchRollbackMonitor(settings=settings.research.rollback)
    engine._manage_latency_baseline = None
    settings.research.rollback.enabled = False
    return engine


async def test_the_symbol_cap_limits_the_work_per_run():
    engine = _engine(["ETHUSDM", "USTECM", "XAUUSDM"], max_symbols_per_run=2)
    lab = _RecordingLab()

    await engine._run_research_cycle(lab)

    assert lab.cycles == ["ETHUSDM", "USTECM"]


async def test_the_generation_cap_reaches_the_lab():
    """Es la variable que mas multiplica el numero de backtests del ciclo."""
    engine = _engine(["ETHUSDM"], max_generated_per_run=5)
    lab = _RecordingLab()

    await engine._run_research_cycle(lab)

    assert lab.counts == [5]


async def test_a_hung_cycle_is_cancelled_by_the_hard_timeout():
    """Un ciclo colgado no puede consumir CPU hasta el proximo disparo."""
    engine = _engine(["ETHUSDM"], run_timeout_seconds=1.0)
    lab = _RecordingLab(hang=True)

    # No propaga el TimeoutError: se registra y se retoma en el proximo ciclo.
    await asyncio.wait_for(engine._run_research_cycle(lab), timeout=10.0)

    assert lab.cycles == [], "el ciclo se cancelo antes de completarse"


async def test_a_denied_budget_never_reaches_the_lab():
    engine = _engine(["ETHUSDM"])
    engine._settings.research.budget.enabled = False
    lab = _RecordingLab()

    await engine._run_research_cycle(lab)

    assert lab.cycles == []


def test_the_cycle_is_still_off_by_default():
    """Punto 4 del bloque: no se activa por defecto, ni siquiera con presupuesto."""
    assert Settings().research.auto_cycle is False
