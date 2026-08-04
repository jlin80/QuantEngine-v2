"""El reloj de backtest no puede alcanzar al motor en vivo.

Incidente que motiva estas pruebas (2026-07-31 → 2026-08-04, produccion):

El proveedor de tiempo era un **global de modulo** y el `BacktestLab` corre en
el mismo proceso y el mismo event loop que el motor en vivo. Un backtest
instalo el reloj de replay y su bloque nunca llego a cerrarse, asi que el motor
vivio 4 dias creyendo que era el 31 de julio a las 16:41.

Consecuencia: el validador de mercado comparaba cada tick del broker contra esa
hora, los veia todos "en el futuro" y **descartaba el 100%**. Sin velas, sin
señales, sin operaciones. Y ninguna alarma salto, porque el motor no estaba
caido — estaba ciego.

Lo que se fija aqui: aislamiento por contexto, y que un reloj desviado se
**detecte** en vez de pasar callado.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from app.utils.time import clock_skew_seconds, set_clock, use_clock, utc_now, wall_now

FROZEN = datetime(2026, 7, 31, 16, 41, 0, tzinfo=UTC)


def _frozen() -> datetime:
    return FROZEN


# ----------------------------------------------------------------------
# Aislamiento
# ----------------------------------------------------------------------


def test_the_injected_clock_applies_inside_its_block():
    with use_clock(_frozen):
        assert utc_now() == FROZEN


def test_the_clock_is_restored_on_exit():
    before = utc_now()
    with use_clock(_frozen):
        pass
    assert utc_now() >= before


def test_the_clock_is_restored_even_if_the_block_raises():
    try:
        with use_clock(_frozen):
            raise RuntimeError("backtest reventado")
    except RuntimeError:
        pass
    assert abs((utc_now() - wall_now()).total_seconds()) < 1.0


async def test_a_sibling_task_never_sees_the_backtest_clock():
    """El caso del incidente: el motor en vivo corre en tareas hermanas.

    Con el proveedor en un global de modulo, esta prueba fallaba: el "motor"
    veia la hora congelada del backtest mientras este durase.
    """
    engine_saw: list[datetime] = []
    backtest_started = asyncio.Event()
    engine_done = asyncio.Event()

    async def live_engine() -> None:
        await backtest_started.wait()
        engine_saw.append(utc_now())
        engine_done.set()

    async def backtest() -> None:
        with use_clock(_frozen):
            backtest_started.set()
            await engine_done.wait()
            assert utc_now() == FROZEN

    await asyncio.gather(live_engine(), backtest())

    assert engine_saw and engine_saw[0] != FROZEN
    assert abs((engine_saw[0] - wall_now()).total_seconds()) < 5.0


async def test_a_backtest_task_that_never_finishes_does_not_freeze_the_engine():
    """El escenario exacto de produccion: el bloque se queda abierto para siempre."""

    async def leaking_backtest() -> None:
        with use_clock(_frozen):
            await asyncio.sleep(3600)  # nunca sale del bloque

    task = asyncio.create_task(leaking_backtest())
    await asyncio.sleep(0.05)

    assert utc_now() != FROZEN
    assert abs((utc_now() - wall_now()).total_seconds()) < 5.0

    task.cancel()


async def test_child_tasks_of_a_backtest_do_inherit_its_clock():
    """El aislamiento no puede romper el backtest: sus propias tareas si
    tienen que ver la hora simulada."""
    seen: list[datetime] = []

    async def inner() -> None:
        seen.append(utc_now())

    async def backtest() -> None:
        with use_clock(_frozen):
            await asyncio.create_task(inner())

    await backtest()

    assert seen == [FROZEN]


# ----------------------------------------------------------------------
# Deteccion
# ----------------------------------------------------------------------


def test_no_injected_clock_means_no_skew():
    assert clock_skew_seconds() == 0.0


def test_a_leaked_clock_is_measurable():
    """Sin una metrica de desviacion, el fallo es invisible: el proceso sigue
    vivo y respondiendo, sólo que con la hora equivocada."""
    with use_clock(_frozen):
        skew = clock_skew_seconds()

    assert abs(skew) > 60.0


def test_wall_now_ignores_the_injected_clock():
    """La vigilancia no puede medirse con el reloj que quiere vigilar."""
    with use_clock(_frozen):
        assert wall_now() != FROZEN
        assert utc_now() == FROZEN


def test_set_clock_can_be_cleared():
    set_clock(_frozen)
    assert utc_now() == FROZEN
    set_clock(None)
    assert utc_now() != FROZEN


def test_a_near_future_clock_still_reports_its_small_skew():
    """El validador de mercado tolera ±5s: la metrica tiene que ser fina."""

    def slightly_ahead() -> datetime:
        return datetime.now(UTC) + timedelta(seconds=8)

    with use_clock(slightly_ahead):
        skew = clock_skew_seconds()

    assert 7.0 < skew < 9.0
