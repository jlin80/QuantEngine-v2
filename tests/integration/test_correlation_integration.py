"""Integración de Correlation Intelligence (Bloque 5): filtro, servicio y API."""

import math

import pytest
from app.config.settings import QuantCorrelationSettings, QuantFiltersSettings, Settings
from app.core.container import Container
from app.dashboard.api.main import create_app
from app.engine.correlation import CorrelationEngine
from app.engine.filters import build_filter_chain
from app.engine.models import (
    ConsensusResult,
    Decision,
    DecisionAction,
    Direction,
    MarketContext,
)
from app.market.models import Timeframe
from app.utils.time import utc_now
from fastapi.testclient import TestClient

from tests.unit.quant_helpers import make_candles

pytestmark = pytest.mark.integration


def _wave(n: int, sign: float = 1.0) -> list[float]:
    return [100.0 + sign * 5.0 * math.sin(i / 3.0) for i in range(n)]


def _engine(series: dict[str, list[float]]) -> CorrelationEngine:
    markets = {symbol: make_candles(closes) for symbol, closes in series.items()}

    class _Market:
        def get_candles(self, symbol, timeframe, limit):  # type: ignore[no-untyped-def]
            return markets.get(symbol, [])[-limit:]

    settings = QuantCorrelationSettings(min_sample=20, window=200, min_correlation=0.7)
    return CorrelationEngine(
        settings, _Market(), list(series), timeframe=Timeframe.M5  # type: ignore[arg-type]
    )


def _context(symbol: str) -> MarketContext:
    return MarketContext(symbol=symbol, generated_at=utc_now())


def _consensus(symbol: str) -> ConsensusResult:
    return ConsensusResult(
        symbol=symbol, direction=Direction.LONG, method="weighted", score=70.0, agreement=0.8
    )


def _accepted(symbol: str) -> Decision:
    return Decision(
        symbol=symbol,
        timestamp=utc_now(),
        action=DecisionAction.OPEN_LONG,
        accepted=True,
        score=70.0,
        confidence=0.7,
        agreement=0.8,
    )


def _chain(measured=None):
    settings = QuantFiltersSettings(enabled=["correlation"], correlation_groups=[])
    return build_filter_chain(
        settings,
        drawdown_reader=lambda: 0.0,
        recent_decisions=lambda: [_accepted("B")],
        measured_correlation=measured,
    )


def test_measured_correlation_blocks_what_manual_groups_never_knew_about() -> None:
    # Los grupos manuales envejecen: dos símbolos pueden empezar a moverse
    # juntos sin que nadie toque el fichero de configuración.
    engine = _engine({"A": _wave(120), "B": _wave(120)})
    engine.analyze()
    chain = _chain(engine.correlated_with)
    results = chain.evaluate(_context("A"), _consensus("A"))
    assert any(not r.passed and "correlacionado" in (r.reason or "") for r in results)


def test_without_measurement_the_filter_behaves_exactly_as_before() -> None:
    results = _chain(None).evaluate(_context("A"), _consensus("A"))
    assert all(r.passed for r in results)


def test_manual_groups_still_apply_on_top_of_the_measurement() -> None:
    # Se unen, no compiten: la regla escrita a mano sigue siendo regla dura.
    settings = QuantFiltersSettings(enabled=["correlation"], correlation_groups=[["A", "B"]])
    chain = build_filter_chain(
        settings,
        drawdown_reader=lambda: 0.0,
        recent_decisions=lambda: [_accepted("B")],
        measured_correlation=lambda _symbol: set(),
    )
    results = chain.evaluate(_context("A"), _consensus("A"))
    assert any(not r.passed for r in results)


@pytest.mark.asyncio
async def test_the_service_measures_immediately_on_start() -> None:
    # Sin esto, el filtro pasaría el primer intervalo entero sin la evidencia
    # que este motor existe para darle, y nadie sabría por qué.
    engine = _engine({"A": _wave(120), "B": _wave(120)})
    assert engine.last_report() is None
    await engine.start()
    try:
        assert engine.last_report() is not None
        assert engine.correlated_with("A") == {"B"}
    finally:
        await engine.stop()


def _client(engine: CorrelationEngine | None) -> TestClient:
    container = Container()
    if engine is not None:
        container.register_instance(CorrelationEngine, engine)
    return TestClient(create_app(Settings(), container=container))


def test_correlation_endpoints_expose_pairs_leadership_and_gaps() -> None:
    client = _client(_engine({"A": _wave(120), "B": _wave(120), "C": _wave(5)}))

    assert client.get("/api/correlation/report").json()["status"] == "pending"

    report = client.post("/api/correlation/cycle").json()
    assert len(report["pairs"]) == 1
    assert "A~C" in report["skipped"]
    assert "leadership" in report

    assert client.get("/api/correlation/A").json()["correlated"] == ["B"]
    assert client.get("/api/correlation/status").json()["symbols"] == ["A", "B", "C"]


def test_correlation_endpoints_answer_503_when_not_wired() -> None:
    assert _client(None).get("/api/correlation/status").status_code == 503
