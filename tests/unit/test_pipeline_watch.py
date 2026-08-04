"""Vigilancia del pipeline: motor ciego y motor mudo.

Estas alarmas existen por un incidente real: un reloj simulado filtrado dejó al
validador descartando el 100% de los ticks durante **4 días**. El proceso
respondía a la API, los servicios figuraban `running` y el watchdog de
componentes no tenía nada que decir. El motor no estaba caído: había dejado de
ver el mercado — y eso no se parecía a un fallo en ninguna métrica.

Lo que se fija aquí: se detecta el **efecto** (ciego / mudo) sea cual sea la
causa, no se avisa dos veces por lo mismo, y un mercado cerrado no dispara nada.
"""

import asyncio
from typing import Any

from app.config.settings import PipelineWatchSettings
from app.core.events.bus import EventBus
from app.monitoring.events import MarketDataBlind, MarketDataRecovered, SignalDrought
from app.monitoring.pipeline_watch import PipelineWatchdog


class _Counters:
    """Contadores acumulados que los tests hacen avanzar a mano."""

    def __init__(self) -> None:
        self.checked = 0
        self.discarded = 0
        self.signals = 0

    def feed(self, *, clean: int = 0, discarded: int = 0, signals: int = 0) -> None:
        self.checked += clean + discarded
        self.discarded += discarded
        self.signals += signals

    def stats(self) -> dict[str, int]:
        return {"checked": self.checked, "discarded": self.discarded}


class _Recorder:
    """Captura las alarmas publicadas en el bus."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def __call__(self, event: Any) -> None:
        self.events.append(event)

    def of(self, kind: type) -> list[Any]:
        return [e for e in self.events if isinstance(e, kind)]


async def _settle() -> None:
    """El bus despacha en su propia tarea: hay que cederle el turno."""
    await asyncio.sleep(0.05)


def _settings(**overrides: object) -> PipelineWatchSettings:
    base: dict[str, object] = {
        "min_samples": 10,
        "blind_discard_ratio": 0.95,
        "min_clean_samples": 10,
        "silent_windows": 3,
    }
    base.update(overrides)
    return PipelineWatchSettings(**base)  # type: ignore[arg-type]


async def _watchdog(
    counters: _Counters, recorder: _Recorder, **overrides: object
) -> PipelineWatchdog:
    bus = EventBus()
    await bus.start()
    bus.subscribe(recorder)
    return PipelineWatchdog(
        _settings(**overrides),
        bus,
        stats_provider=counters.stats,
        signal_count_provider=lambda: counters.signals,
    )


# ----------------------------------------------------------------------
# Línea base
# ----------------------------------------------------------------------


async def test_the_first_pass_only_takes_a_baseline():
    """Arrancar comparando contra cero daría una alarma falsa al iniciar."""
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder)
    counters.feed(discarded=1000)

    result = await watchdog.evaluate()

    assert result == {"status": "priming"}
    await _settle()
    assert recorder.events == []


# ----------------------------------------------------------------------
# Motor ciego
# ----------------------------------------------------------------------


async def test_discarding_everything_raises_the_blind_alarm():
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder)
    await watchdog.evaluate()  # baseline

    counters.feed(discarded=500)  # exactamente el incidente: 100% descartado
    await watchdog.evaluate()

    await _settle()
    (alarm,) = recorder.of(MarketDataBlind)
    assert alarm.discard_ratio == 1.0
    assert alarm.discarded == 500
    assert "clock_skew_seconds" in alarm.detail, "la alarma debe decir por dónde empezar"


async def test_healthy_data_raises_nothing():
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder)
    await watchdog.evaluate()

    counters.feed(clean=500, discarded=5, signals=3)
    await watchdog.evaluate()

    await _settle()
    assert recorder.of(MarketDataBlind) == []
    await _settle()
    assert recorder.of(SignalDrought) == []


async def test_a_small_sample_never_triggers_the_blind_alarm():
    """Con 3 ticks descartados no se puede afirmar que el motor esté ciego."""
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder, min_samples=50)
    await watchdog.evaluate()

    counters.feed(discarded=3)
    await watchdog.evaluate()

    await _settle()
    assert recorder.of(MarketDataBlind) == []


async def test_the_blind_alarm_is_not_repeated_every_window():
    """Una alarma que se repite cada 5 minutos se acaba silenciando."""
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder)
    await watchdog.evaluate()

    for _ in range(10):
        counters.feed(discarded=500)
        await watchdog.evaluate()

    await _settle()
    assert len(recorder.of(MarketDataBlind)) == 1


async def test_recovery_is_announced_and_re_arms_the_alarm():
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder)
    await watchdog.evaluate()
    counters.feed(discarded=500)
    await watchdog.evaluate()

    counters.feed(clean=500, discarded=2, signals=1)
    await watchdog.evaluate()

    await _settle()
    assert len(recorder.of(MarketDataRecovered)) == 1

    counters.feed(discarded=500)
    await watchdog.evaluate()

    await _settle()
    assert len(recorder.of(MarketDataBlind)) == 2, "una recaída vuelve a avisar"


# ----------------------------------------------------------------------
# Motor mudo
# ----------------------------------------------------------------------


async def test_clean_data_without_signals_raises_the_drought_alarm():
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder, silent_windows=3)
    await watchdog.evaluate()

    for _ in range(3):
        counters.feed(clean=500)  # datos limpios, cero señales
        await watchdog.evaluate()

    await _settle()
    (alarm,) = recorder.of(SignalDrought)
    assert alarm.clean_samples == 500
    assert alarm.minutes > 0


async def test_the_drought_alarm_waits_for_the_configured_streak():
    """Un hueco corto entre señales es normal; no puede disparar la alarma."""
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder, silent_windows=3)
    await watchdog.evaluate()

    for _ in range(2):
        counters.feed(clean=500)
        await watchdog.evaluate()

    await _settle()
    assert recorder.of(SignalDrought) == []


async def test_a_single_signal_resets_the_streak():
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder, silent_windows=3)
    await watchdog.evaluate()

    counters.feed(clean=500)
    await watchdog.evaluate()
    counters.feed(clean=500)
    await watchdog.evaluate()
    counters.feed(clean=500, signals=1)  # una señal rompe la racha
    await watchdog.evaluate()
    counters.feed(clean=500)
    await watchdog.evaluate()

    await _settle()
    assert recorder.of(SignalDrought) == []


async def test_a_closed_market_does_not_raise_the_drought_alarm():
    """Sin datos no se distingue "mudo" de "cerrado". Avisar cada noche
    convertiria la alarma en ruido y acabaria ignorada."""
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder, silent_windows=2)
    await watchdog.evaluate()

    for _ in range(10):
        counters.feed()  # ni datos ni señales: mercado cerrado
        await watchdog.evaluate()

    await _settle()
    assert recorder.of(SignalDrought) == []


async def test_the_drought_alarm_is_not_repeated_every_window():
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder, silent_windows=2)
    await watchdog.evaluate()

    for _ in range(10):
        counters.feed(clean=500)
        await watchdog.evaluate()

    await _settle()
    assert len(recorder.of(SignalDrought)) == 1


async def test_blind_and_mute_can_be_reported_independently():
    """Estar ciego no debe disparar tambien "mudo": sin datos limpios la
    segunda alarma no aplica, y confundirlas despista el diagnostico."""
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder, silent_windows=2)
    await watchdog.evaluate()

    for _ in range(5):
        counters.feed(discarded=500)
        await watchdog.evaluate()

    await _settle()
    assert len(recorder.of(MarketDataBlind)) == 1
    await _settle()
    assert recorder.of(SignalDrought) == []


async def test_a_disabled_watchdog_does_not_start_its_loop():
    counters, recorder = _Counters(), _Recorder()
    watchdog = await _watchdog(counters, recorder, enabled=False)

    await watchdog.start()
    status = watchdog.status()
    await watchdog.stop()

    assert status["enabled"] is False
