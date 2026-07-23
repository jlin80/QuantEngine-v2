"""Shadow Mode, Paper Validation, Promotion Manager y Candidate Store (Fase 10)."""

from app.backtesting import BacktestLab
from app.config.settings import (
    PaperValidationSettings,
    PromotionSettings,
    Settings,
    ShadowModeSettings,
)
from app.research.models import CandidateReport, CandidateStatus
from app.research.paper_validation import PaperValidationTracker
from app.research.production_candidate import CandidateStore, PromotionManager
from app.research.shadow_mode import ShadowComparator, ShadowSession

from tests.unit.research_helpers import candles, trend_genome


def _passed_report(genome_id: str, name: str) -> CandidateReport:
    return CandidateReport(
        genome_id=genome_id,
        name=name,
        symbol="BTCUSDT",
        status=CandidateStatus.CANDIDATE,
        passed=True,
        objective="sharpe",
        score=1.4,
        stages=(),
        statistics={
            "profit_factor": 1.8,
            "sharpe": 1.4,
            "expectancy_r": 0.2,
            "sqn": 2.5,
            "max_drawdown_pct": 7.0,
        },
    )


def test_shadow_comparator_produces_verdict(settings: Settings):
    lab = BacktestLab(settings)
    data = candles(400, drift=0.0008, seed=7)
    cfg = lab.make_config("BTCUSDT")
    shadow_settings = ShadowModeSettings(min_signals=2)
    comparator = ShadowComparator(lab, shadow_settings)
    from app.research.strategy_generator import compile_genome

    comparison = comparator.compare(
        compile_genome(trend_genome(fast=5, slow=20)),
        compile_genome(trend_genome(fast=3, slow=10)),
        data,
        cfg,
    )
    assert comparison.verdict
    assert 0.0 <= comparison.p_value <= 1.0
    assert comparison.samples >= 0


def test_shadow_session_streams_same_data_to_both(settings: Settings):
    lab = BacktestLab(settings)
    data = candles(300, drift=0.001, seed=3)
    cfg = lab.make_config("BTCUSDT")
    shadow_settings = ShadowModeSettings(min_signals=1)
    comparator = ShadowComparator(lab, shadow_settings)
    from app.research.strategy_generator import compile_genome

    session = ShadowSession(
        comparator,
        compile_genome(trend_genome(fast=5, slow=20)),
        compile_genome(trend_genome(fast=3, slow=10)),
        cfg,
        shadow_settings,
    )
    for candle in data:
        session.feed(candle)
    assert session.bars == len(data)
    assert sum(session.signals.values()) >= 1
    comparison = session.conclude()
    assert comparison.verdict


def test_paper_validation_maturity_gate():
    tracker = PaperValidationTracker(PaperValidationSettings(min_days=7.0, min_trades=20))
    tracker.open("g1")
    tracker.update("g1", trades=25, profit_factor=1.5, drawdown_pct=8.0)
    tracker.set_elapsed("g1", 10.0)
    assert tracker.status("g1").matured
    tracker.set_elapsed("g1", 3.0)  # muy pronto
    status = tracker.status("g1")
    assert not status.matured
    assert any("período" in r for r in status.reasons)


def test_promotion_is_fail_closed_without_operator(settings: Settings):
    manager = PromotionManager(PromotionSettings(), settings.research.multi_objective.objectives)
    genome = trend_genome()
    report = _passed_report(genome.id, genome.name)
    from app.research.models import PaperTrialStatus

    matured = PaperTrialStatus(
        genome_id=genome.id, matured=True, days=10, trades=25, profit_factor=1.5, drawdown_pct=7.0
    )
    blocked = manager.evaluate(
        genome,
        report,
        paper_status=matured,
        current_metrics=None,
        drift=0.1,
        operator_approved=False,
    )
    assert not blocked.approved
    assert any("operador" in b for b in blocked.blockers)
    approved = manager.evaluate(
        genome,
        report,
        paper_status=matured,
        current_metrics=None,
        drift=0.1,
        operator_approved=True,
        operator="naz",
    )
    assert approved.approved


def test_promotion_blocks_on_drift_and_paper(settings: Settings):
    manager = PromotionManager(
        PromotionSettings(require_operator_approval=False),
        settings.research.multi_objective.objectives,
    )
    genome = trend_genome()
    report = _passed_report(genome.id, genome.name)
    decision = manager.evaluate(
        genome, report, paper_status=None, current_metrics=None, drift=0.9, operator_approved=True
    )
    assert not decision.approved
    assert any("drift" in b for b in decision.blockers)
    assert any("paper" in b for b in decision.blockers)


def test_candidate_store_round_trip(tmp_path):
    store = CandidateStore(tmp_path)
    genome = trend_genome()
    report = _passed_report(genome.id, genome.name)
    store.register(genome, report)
    store.set_status(genome.id, CandidateStatus.PAPER)
    reloaded = CandidateStore(tmp_path)
    candidate = reloaded.get(genome.id)
    assert candidate.status is CandidateStatus.PAPER
    assert candidate.genome.blocks[0].kind == "ema_cross"
    assert candidate.report.passed
    assert reloaded.count() == 1
