"""Candidate Pipeline, Ranking, Experiments y Knowledge Base (Fase 10)."""

from app.backtesting import BacktestLab
from app.config.settings import Settings
from app.core.exceptions import ExperimentNotFoundError
from app.research.experiment_manager import ExperimentManager
from app.research.knowledge_base import KnowledgeBase
from app.research.models import (
    CandidateStatus,
    ExperimentStatus,
    Hypothesis,
    PipelineStage,
)
from app.research.ranking_engine import RankingEngine
from app.research.validation_pipeline import CandidatePipeline

from tests.unit.research_helpers import candles, fast_pipeline, permissive_criteria, trend_genome


def _lab(settings: Settings) -> BacktestLab:
    return BacktestLab(settings)


def test_pipeline_runs_every_required_stage(settings: Settings):
    lab = _lab(settings)
    data = candles(400, drift=0.0009, seed=4)
    cfg = lab.make_config("BTCUSDT")
    pipe = CandidatePipeline(lab, fast_pipeline(), permissive_criteria(), objective="sharpe")
    report = pipe.evaluate(trend_genome(fast=3, slow=12), data, cfg)
    stages = {stage.stage for stage in report.stages}
    assert PipelineStage.BACKTEST in stages
    assert PipelineStage.MONTE_CARLO in stages
    assert PipelineStage.RISK_REVIEW in stages
    assert report.status in {CandidateStatus.CANDIDATE, CandidateStatus.REJECTED}


def test_pipeline_passes_with_permissive_criteria(settings: Settings):
    lab = _lab(settings)
    data = candles(400, drift=0.001, seed=8)
    cfg = lab.make_config("BTCUSDT")
    pipe = CandidatePipeline(lab, fast_pipeline(), permissive_criteria(), objective="sharpe")
    report = pipe.evaluate(trend_genome(fast=3, slow=10), data, cfg)
    assert report.passed
    assert report.status is CandidateStatus.CANDIDATE
    assert "stability" in report.statistics


def test_pipeline_rejects_with_strict_criteria(settings: Settings):
    lab = _lab(settings)
    data = candles(400, drift=0.0, vol=0.01, seed=2)
    cfg = lab.make_config("BTCUSDT")
    pipe = CandidatePipeline(
        lab, fast_pipeline(), settings.backtesting.criteria, objective="sharpe"
    )
    report = pipe.evaluate(trend_genome(), data, cfg)
    assert not report.passed
    assert report.reasons


def test_ml_review_stage_can_block(settings: Settings):
    lab = _lab(settings)
    data = candles(300, drift=0.001, seed=3)
    cfg = lab.make_config("BTCUSDT")
    pipeline_settings = fast_pipeline()
    pipeline_settings.require_ml_review = True
    pipe = CandidatePipeline(
        lab,
        pipeline_settings,
        permissive_criteria(),
        objective="sharpe",
        ml_review=lambda genome, stats: (False, "ML desaconseja"),
    )
    report = pipe.evaluate(trend_genome(), data, cfg)
    ml_stage = next(s for s in report.stages if s.stage is PipelineStage.ML_REVIEW)
    assert not ml_stage.passed
    assert not report.passed


def test_ranking_orders_by_composite_score(settings: Settings):
    lab = _lab(settings)
    data = candles(400, drift=0.001, seed=8)
    cfg = lab.make_config("BTCUSDT")
    pipe = CandidatePipeline(lab, fast_pipeline(), permissive_criteria(), objective="sharpe")
    reports = [
        pipe.evaluate(trend_genome(fast=f, slow=s), data, cfg)
        for f, s in ((3, 10), (5, 20), (8, 30))
    ]
    ranking = RankingEngine(settings.research.multi_objective.objectives).rank(reports)
    assert [e.rank for e in ranking] == [1, 2, 3]
    scores = [e.score for e in ranking]
    assert scores == sorted(scores, reverse=True)


def test_experiment_manager_lifecycle_append_only(tmp_path):
    manager = ExperimentManager(tmp_path)
    record = manager.create(
        "gen-batch", "generation", hypothesis=Hypothesis(text="trend beats random")
    )
    assert record.status is ExperimentStatus.OPEN
    manager.close(record.id, "algunas candidatas superan el benchmark")
    assert manager.get(record.id).status is ExperimentStatus.CLOSED
    manager.archive(record.id)
    assert manager.get(record.id).status is ExperimentStatus.ARCHIVED
    # persistencia append-only: se recarga la última versión
    reloaded = ExperimentManager(tmp_path)
    assert reloaded.get(record.id).status is ExperimentStatus.ARCHIVED
    assert reloaded.count() == 1


def test_experiment_manager_unknown_id_raises(tmp_path):
    manager = ExperimentManager(tmp_path)
    try:
        manager.get("nope")
        raise AssertionError("debió fallar")
    except ExperimentNotFoundError:
        pass


def test_knowledge_base_records_and_summarizes(tmp_path):
    kb = KnowledgeBase(tmp_path)
    kb.record(
        genome_id="g1", name="a", symbol="BTCUSDT", outcome="worked", reason="edge en tendencia"
    )
    kb.record(
        genome_id="g2",
        name="b",
        symbol="BTCUSDT",
        outcome="failed",
        reason="spread se come el edge",
    )
    summary = kb.summary()
    assert summary["total"] == 2
    assert summary["by_outcome"] == {"worked": 1, "failed": 1}
    assert kb.query(outcome="failed")[0]["name"] == "b"
    # persistencia
    assert KnowledgeBase(tmp_path).count() == 2
