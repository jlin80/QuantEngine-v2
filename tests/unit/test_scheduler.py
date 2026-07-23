"""Pruebas del scheduler: ejecución periódica y aislamiento de errores."""

import asyncio

import pytest
from app.core.exceptions import SchedulerError
from app.scheduler.scheduler import AsyncScheduler


async def test_job_runs_periodically():
    scheduler = AsyncScheduler()
    runs = {"n": 0}

    async def tick() -> None:
        runs["n"] += 1

    scheduler.add_job("tick", tick, interval_seconds=0.02, run_immediately=True)
    await scheduler.start()
    await asyncio.sleep(0.09)
    await scheduler.stop()

    assert runs["n"] >= 2
    info = scheduler.jobs[0]
    assert info.name == "tick"
    assert info.run_count >= 2
    assert info.error_count == 0


async def test_failing_job_does_not_kill_scheduler():
    scheduler = AsyncScheduler()
    healthy_runs = {"n": 0}

    async def broken() -> None:
        raise ValueError("bad tick")

    async def healthy() -> None:
        healthy_runs["n"] += 1

    scheduler.add_job("broken", broken, interval_seconds=0.02, run_immediately=True)
    scheduler.add_job("healthy", healthy, interval_seconds=0.02, run_immediately=True)
    await scheduler.start()
    await asyncio.sleep(0.09)
    await scheduler.stop()

    broken_info = next(j for j in scheduler.jobs if j.name == "broken")
    assert broken_info.error_count >= 2
    assert "bad tick" in broken_info.last_error
    assert healthy_runs["n"] >= 2, "el job sano sigue corriendo"


async def test_duplicate_job_rejected():
    scheduler = AsyncScheduler()

    async def noop() -> None:
        pass

    scheduler.add_job("j", noop, interval_seconds=1)
    with pytest.raises(SchedulerError):
        scheduler.add_job("j", noop, interval_seconds=1)


async def test_invalid_interval_rejected():
    scheduler = AsyncScheduler()

    async def noop() -> None:
        pass

    with pytest.raises(SchedulerError):
        scheduler.add_job("bad", noop, interval_seconds=0)
