"""Integración de Portfolio Intelligence (Bloque 8): cableado real y API."""

import pytest
from app.config.settings import Settings
from app.core.container import Container
from app.dashboard.api.main import create_app
from app.portfolio import PortfolioIntelligence
from fastapi.testclient import TestClient

from tests.unit.test_portfolio_intelligence import _intelligence, _trade

pytestmark = pytest.mark.integration


def _client(wired: bool) -> TestClient:
    container = Container()
    if wired:
        trades = [_trade(i, 10.0, symbol="BTCUSDm") for i in range(5)]
        trades += [_trade(10 + i, -4.0, symbol="ETHUSDm", strategy="beta") for i in range(5)]
        container.register_instance(PortfolioIntelligence, _intelligence(trades))
    return TestClient(create_app(Settings(), container=container))


def test_the_report_endpoint_serves_the_full_breakdown() -> None:
    body = _client(True).get("/api/portfolio/report").json()
    assert body["trades"] == 10
    assert set(body["contributions"]) == {"symbol", "strategy", "session", "regime"}
    assert body["heatmap"]["BTCUSDm"]["alpha"] == 50.0
    assert body["effective_bets"] is not None


def test_the_status_endpoint_does_not_recompute_before_the_first_report() -> None:
    client = _client(True)
    assert client.get("/api/portfolio/status").json()["last_report"] is None
    client.get("/api/portfolio/report")
    assert client.get("/api/portfolio/status").json()["last_report"] is not None


def test_portfolio_is_wired_from_the_real_composition_root() -> None:
    from app.engine.bootstrap import build_container

    settings = Settings()
    settings.market.enabled = True
    settings.quant.enabled = True
    settings.execution.enabled = True
    assert build_container(settings).contains(PortfolioIntelligence)


def test_portfolio_endpoints_answer_503_when_not_wired() -> None:
    assert _client(False).get("/api/portfolio/report").status_code == 503
