"""Fachada ResearchLab de extremo a extremo y rutas del dashboard (Fase 10)."""

import asyncio

from app.config.environment import Environment
from app.config.settings import Settings, get_settings
from app.engine.bootstrap import build_container
from app.research.api import ResearchLab
from app.research.models import Hypothesis
from app.research.research_engine import ResearchEngine

from tests.unit.research_helpers import candles, fast_pipeline, permissive_criteria, trend_genome


def _lab() -> ResearchLab:
    settings = Settings()
    settings.backtesting.criteria = permissive_criteria()
    settings.research.pipeline = fast_pipeline()
    settings.research.multi_objective.population_size = 8
    settings.research.multi_objective.generations = 4
    settings.research.bayesian.max_evaluations = 15
    settings.research.bayesian.n_startup = 6
    return ResearchLab(settings, persist=False)


def test_generate_and_validate_registers_candidate():
    lab = _lab()
    data = candles(400, drift=0.001, seed=8)
    cfg = lab.make_config("BTCUSDT")
    genomes = lab.generate_strategy("BTCUSDT", "1m", count=3)
    assert len(genomes) == 3
    report = lab.validate_candidate(trend_genome(fast=3, slow=10), data, cfg)
    assert report.passed
    assert lab.candidates.count() == 1
    assert lab.knowledge.count() == 1  # el conocimiento se registra siempre


def test_optimize_strategy_multi_and_bayesian():
    lab = _lab()
    data = candles(300, drift=0.001, seed=5)
    cfg = lab.make_config("BTCUSDT")
    genome = trend_genome(fast=5, slow=20)
    multi = lab.optimize_strategy(genome, data, cfg, method="multi")
    assert multi.evaluations > 0
    bayes = lab.optimize_strategy(genome, data, cfg, method="bayesian")
    assert bayes.evaluations == 15
    assert lab.bayesian.history.count() == 1


def test_feature_and_factor_research():
    lab = _lab()
    data = candles(400, drift=0.0008, seed=6)
    report = lab.generate_feature("my_close", lambda cs: [c.close for c in cs], data)
    assert report.name == "my_close"
    factors = lab.research_factors(data)
    assert factors and factors[0].rank == 1


def test_shadow_comparison_via_facade():
    lab = _lab()
    data = candles(300, drift=0.001, seed=3)
    cfg = lab.make_config("BTCUSDT")
    comparison = lab.compare_shadow(
        trend_genome(fast=5, slow=20), trend_genome(fast=3, slow=10), data, cfg
    )
    assert comparison.official and comparison.challenger
    assert comparison.verdict


def test_promotion_flow_end_to_end():
    lab = _lab()
    data = candles(400, drift=0.001, seed=8)
    cfg = lab.make_config("BTCUSDT")
    genome = trend_genome(fast=3, slow=10)
    report = lab.validate_candidate(genome, data, cfg)
    assert report.passed

    lab.start_paper(genome.id)
    lab.paper.set_elapsed(genome.id, 10.0)
    status = lab.update_paper(
        genome.id, {"total_trades": 30.0, "profit_factor": 1.6, "max_drawdown_pct": 5.0}
    )
    assert status.matured

    decision = asyncio.run(
        lab.promote_strategy(genome.id, operator="naz", operator_approved=True, drift=0.05)
    )
    assert decision.approved
    assert lab.candidates.get(genome.id).status.value == "promoted"


def test_create_and_archive_experiment_async():
    lab = _lab()
    record = asyncio.run(
        lab.create_experiment("gen", "generation", hypothesis=Hypothesis(text="h"))
    )
    assert lab.experiments.count() == 1
    archived = asyncio.run(lab.archive_experiment(record.id, "concluido"))
    assert archived.status.value == "archived"


def test_run_generation_cycle_uses_cluster():
    lab = _lab()
    data = candles(300, drift=0.001, seed=8)
    cfg = lab.make_config("BTCUSDT")
    summary = asyncio.run(
        ResearchEngine(lab).run_generation_cycle("BTCUSDT", "1m", data, cfg, count=3)
    )
    assert summary["generated"] == 3
    assert summary["qualified"] <= 3
    assert "ranking" in summary


def test_status_and_report_shapes():
    lab = _lab()
    status = lab.status()
    assert status["blocks"] == 10
    report = lab.report()
    assert {"status", "candidates", "experiments", "knowledge", "bayesian"} <= set(report)


def test_bootstrap_wires_research_lab():
    from app.core.container import Container
    from app.research.notifications import ResearchNotifier

    get_settings.cache_clear()
    settings = get_settings(Environment.TESTING)
    settings.research.enabled = True
    settings.research.persist = False  # stores en memoria: aislamiento de test
    settings.backtesting.enabled = True
    container: Container = build_container(settings)
    assert container.contains(ResearchLab)
    assert container.contains(ResearchNotifier)


def test_research_routes_via_container():
    from app.core.container import Container
    from app.dashboard.api.main import create_app
    from starlette.testclient import TestClient

    # Container mínimo con un ResearchLab en memoria y SIN documentación: los
    # endpoints no deben escribir en la bitácora real durante los tests.
    settings = Settings()
    settings.research.enabled = True
    container = Container()
    container.register_instance(Settings, settings)
    container.register_instance(
        ResearchLab, ResearchLab(settings, documentation=None, persist=False)
    )
    client = TestClient(create_app(settings, container))

    assert client.get("/api/research/status").status_code == 200
    assert client.get("/api/research/catalog").status_code == 200
    generated = client.post("/api/research/generate", json={"count": 3}).json()
    assert generated["generated"] == 3
    created = client.post("/api/research/experiments", json={"label": "x", "kind": "note"}).json()
    assert created["kind"] == "note"
    listed = client.get("/api/research/experiments").json()
    assert len(listed["experiments"]) == 1
    assert any(e["id"] == created["id"] for e in listed["experiments"])
