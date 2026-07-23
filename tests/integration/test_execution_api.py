"""Integración: endpoints /api/execution/* con un ExecutionCore real cableado."""

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.dashboard.api.main import create_app
from app.execution.api import ExecutionCore
from fastapi.testclient import TestClient

from tests.unit.execution_helpers import make_engine, make_market_with_state
from tests.unit.quant_helpers import make_candles, make_ticker

pytestmark = pytest.mark.integration


def _build_core() -> ExecutionCore:
    market, _ = make_market_with_state(
        candles=make_candles([100.0, 101.0, 100.5, 101.5, 102.0] * 6),
        ticker=make_ticker(bid=101.9, ask=101.95),
    )
    engine = make_engine(market)
    return ExecutionCore(
        engine=engine,
        positions=engine.positions,
        portfolio=engine.portfolio,
        risk=engine.risk,
        journal=engine.journal,
        performance=engine._performance,
    )


@pytest.fixture()
def client(settings: Settings):
    container = Container()
    container.register_instance(ExecutionCore, _build_core())
    app = create_app(settings, container)
    with TestClient(app) as test_client:
        yield test_client


def test_execution_status(client: TestClient):
    body = client.get("/api/execution/status").json()
    assert body["mode"] == "paper"
    for key in ("portfolio", "positions", "orders", "risk", "paper", "journal"):
        assert key in body


def test_execution_portfolio_and_performance(client: TestClient):
    portfolio = client.get("/api/execution/portfolio").json()
    assert portfolio["initial_balance"] == 10_000.0
    perf = client.get("/api/execution/performance").json()
    assert perf["total_trades"] == 0


def test_execution_positions_and_risk(client: TestClient):
    positions = client.get("/api/execution/positions").json()
    assert positions == {"open": [], "closed": []}
    risk = client.get("/api/execution/risk").json()
    assert risk["kill_switch"] is False


def test_execution_endpoints_503_without_core(settings: Settings):
    app = create_app(settings, Container())
    with TestClient(app) as client:
        assert client.get("/api/execution/status").status_code == 503
