"""Integración del Live Shadow Benchmark (Bloque 15): cableado, API y guard.

El test que más importa aquí es el último: que este bloque **no** ha tocado el
guard anti-live. Todo lo demás es observación.
"""

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.dashboard.api.main import create_app
from app.execution.benchmark import ShadowBenchmark
from fastapi.testclient import TestClient

from tests.unit.test_shadow_benchmark import _benchmark, _trades

pytestmark = pytest.mark.integration


def _client(wired: bool) -> TestClient:
    container = Container()
    if wired:
        container.register_instance(ShadowBenchmark, _benchmark(_trades(20)))
    return TestClient(create_app(Settings(), container=container))


def test_the_report_endpoint_serves_the_three_tracks() -> None:
    body = _client(True).get("/api/benchmark/report").json()
    assert set(body["tracks"]) == {"paper", "ideal", "live"}
    assert body["tracks"]["paper"]["available"] is True
    assert body["tracks"]["live"]["available"] is False
    assert body["fill_difference_bps"] is None


def test_the_endpoint_says_live_is_disabled_in_the_payload() -> None:
    assert _client(True).get("/api/benchmark/report").json()["live_enabled"] is False
    assert _client(True).get("/api/benchmark/status").json()["live_enabled"] is False


def test_benchmark_endpoints_answer_503_when_not_wired() -> None:
    assert _client(False).get("/api/benchmark/report").status_code == 503


def test_the_benchmark_is_wired_from_the_real_composition_root() -> None:
    from app.engine.bootstrap import build_container

    settings = Settings()
    settings.market.enabled = True
    settings.quant.enabled = True
    settings.execution.enabled = True
    container = build_container(settings)
    benchmark = container.resolve(ShadowBenchmark)
    report = benchmark.analyze()
    assert report.tracks["live"].available is False


def test_this_block_did_not_touch_the_anti_live_guard() -> None:
    # La regla del proyecto sigue en pie y se verifica desde fuera, no por
    # lectura del codigo: el modo resuelto nunca es live con la config por
    # defecto, y el benchmark no tiene forma de cambiarlo.
    from app.production.live import ModeResolver

    from tests.unit.production_helpers import make_gate

    gate, _ = make_gate()
    resolver = ModeResolver(Settings(), gate)
    assert resolver.resolved_mode() == "paper"
    assert "allow_live" in resolver.blocking_reason()
