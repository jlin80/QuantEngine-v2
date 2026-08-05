"""Position Quality Engine (Bloque 7): dimensiones, veto y fail-open."""

import pytest
from app.config.settings import QuantPositionQualitySettings
from app.engine.models import (
    ConsensusResult,
    Direction,
    MarketContext,
    VolatilityState,
)
from app.engine.position_quality import DIMENSIONS, PositionQualityEngine
from app.utils.time import utc_now


def _engine(**overrides) -> PositionQualityEngine:
    defaults = {"min_dimensions": 3}
    return PositionQualityEngine(QuantPositionQualitySettings(**{**defaults, **overrides}))


def _context(**overrides) -> MarketContext:
    defaults = {
        "symbol": "BTCUSDm",
        "generated_at": utc_now(),
        "data_quality": 0.9,
        "spread_bps": 3.0,
        "volume_recent": 1000.0,
        "volatility": VolatilityState.NORMAL,
    }
    return MarketContext(**{**defaults, **overrides})


def _consensus(score: float = 75.0, agreement: float = 0.8) -> ConsensusResult:
    return ConsensusResult(
        symbol="BTCUSDm",
        direction=Direction.LONG,
        method="weighted",
        score=score,
        agreement=agreement,
    )


def _full(engine: PositionQualityEngine, **overrides):
    """Evaluación con las seis dimensiones observables."""
    defaults = {
        "expected_cost_bps": 5.0,
        "expected_r": 1.5,
        "risk_pct": 0.5,
        "max_risk_pct": 2.0,
    }
    return engine.assess(_context(), _consensus(), **{**defaults, **overrides})


# ---------------------------------------------------------------------------
# Dimensiones
# ---------------------------------------------------------------------------


def test_a_good_setup_in_a_good_market_scores_well_and_passes() -> None:
    assessment = _full(_engine())
    assert assessment.score is not None
    assert assessment.score > 60.0
    assert assessment.blocked is False
    assert set(assessment.components) == set(DIMENSIONS)


def test_missing_dimensions_are_declared_not_scored_as_zero() -> None:
    # Un cero dice "es malo"; la ausencia dice "no se sabe". Promediar la
    # ausencia como cero bloquearía operaciones por ignorancia.
    assessment = _engine().assess(_context(data_quality=0.0), _consensus())
    assert "execution" in assessment.missing
    assert "risk" in assessment.missing
    assert "cost" in assessment.missing
    assert "execution" not in assessment.components


def test_the_score_reports_how_many_dimensions_it_rests_on() -> None:
    # Un 72 sobre dos dimensiones no es el mismo número que un 72 sobre seis.
    partial = _engine().assess(_context(), _consensus())
    assert partial.to_dict()["observed"] == len(partial.components)
    assert partial.to_dict()["missing"]


def test_risk_near_the_ceiling_scores_worse_than_risk_well_below_it() -> None:
    engine = _engine()
    tight = _full(engine, risk_pct=1.9, max_risk_pct=2.0).components["risk"]
    loose = _full(engine, risk_pct=0.2, max_risk_pct=2.0).components["risk"]
    assert loose > tight


def test_cost_is_judged_against_the_edge_it_eats() -> None:
    # 8 bps son baratos para 3 R y carísimos para 0.2 R.
    engine = _engine()
    for_big_move = _full(engine, expected_cost_bps=8.0, expected_r=3.0).components["cost"]
    for_scalp = _full(engine, expected_cost_bps=8.0, expected_r=0.2).components["cost"]
    assert for_big_move > for_scalp


def test_without_expected_r_the_cost_falls_back_to_an_absolute_ceiling() -> None:
    # Peor comparación, pero no se inventa la R que nadie ha estimado.
    engine = _engine(max_cost_bps=20.0)
    assessment = _full(engine, expected_cost_bps=10.0, expected_r=None)
    assert assessment.components["cost"] == pytest.approx(0.5)


def test_a_news_blackout_and_dead_volatility_hurt_the_context() -> None:
    engine = _engine()
    calm = _full(engine).components["context"]
    dead = engine.assess(
        _context(volatility=VolatilityState.LOW, news_blackout=True), _consensus()
    ).components["context"]
    assert dead < calm


def test_an_elevated_spread_hurts_liquidity() -> None:
    engine = _engine()
    healthy = _full(engine).components["liquidity"]
    wide = engine.assess(
        _context(spread_elevated=True, volume_sufficient=False), _consensus()
    ).components["liquidity"]
    assert wide < healthy


# ---------------------------------------------------------------------------
# Veto
# ---------------------------------------------------------------------------


def test_a_poor_position_is_blocked_with_its_reason() -> None:
    engine = _engine(min_score=80.0)
    assessment = _full(engine, expected_cost_bps=25.0, expected_r=0.3, risk_pct=1.9)
    assert assessment.blocked is True
    assert any("calidad" in reason for reason in assessment.reasons)


def test_a_floor_breach_can_block_even_when_the_average_survives() -> None:
    # Promediar deja que una liquidez pésima se esconda detrás de un setup
    # excelente, y esa es justo la posición que duele.
    engine = _engine(min_score=0.0, dimension_floors={"liquidity": 0.9})
    assessment = engine.assess(
        _context(spread_elevated=True),
        _consensus(score=99.0, agreement=1.0),
        expected_cost_bps=1.0,
        expected_r=3.0,
        risk_pct=0.1,
        max_risk_pct=2.0,
    )
    assert assessment.blocked is True
    assert any("liquidity" in reason for reason in assessment.reasons)


def test_a_floor_breach_is_only_a_warning_when_configured_that_way() -> None:
    engine = _engine(
        min_score=0.0, dimension_floors={"liquidity": 0.9}, block_on_floor_breach=False
    )
    assessment = engine.assess(
        _context(spread_elevated=True),
        _consensus(),
        expected_cost_bps=1.0,
        expected_r=3.0,
        risk_pct=0.1,
        max_risk_pct=2.0,
    )
    assert assessment.blocked is False
    # El aviso viaja igual: "paso, pero la liquidez está al límite" es
    # información, y sólo se ve si se conserva.
    assert any("liquidity" in reason for reason in assessment.reasons)


def test_it_never_blocks_on_too_little_evidence() -> None:
    # Con el bróker actual —sin libro, sin coste estimado en algunos símbolos—
    # bloquear aquí apagaría el motor sin que ningún log dijera nada raro.
    engine = _engine(min_dimensions=6, min_score=99.0)
    assessment = engine.assess(_context(data_quality=0.0), _consensus(score=1.0))
    assert assessment.blocked is False
    assert any("ignorancia" in reason for reason in assessment.reasons)


def test_the_verdict_always_carries_a_reason_even_when_it_passes() -> None:
    assessment = _full(_engine())
    assert assessment.reasons
    assert assessment.to_dict()["reasons"]


def test_weights_change_the_score_not_the_dimensions() -> None:
    baseline = _full(_engine()).score
    reweighted = _full(_engine(weights={"setup": 10.0})).score
    assert baseline is not None and reweighted is not None
    assert baseline != reweighted
