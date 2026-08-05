"""Integración del Position Quality Engine (Bloque 7): veto en la cadena."""

import pytest
from app.config.settings import (
    QuantFiltersSettings,
    QuantPositionQualitySettings,
    Settings,
)
from app.engine.filters import build_filter_chain
from app.engine.models import ConsensusResult, Direction, MarketContext, VolatilityState
from app.engine.position_quality import PositionQualityEngine
from app.utils.time import utc_now

pytestmark = pytest.mark.integration


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


def _consensus(score: float = 75.0) -> ConsensusResult:
    return ConsensusResult(
        symbol="BTCUSDm",
        direction=Direction.LONG,
        method="weighted",
        score=score,
        agreement=0.8,
    )


def _chain(engine: PositionQualityEngine | None, inputs=None):
    settings = QuantFiltersSettings(enabled=["position_quality"])
    return build_filter_chain(
        settings,
        drawdown_reader=lambda: 0.0,
        recent_decisions=lambda: [],
        position_quality=None if engine is None else (engine, inputs),
    )


def _inputs(**values):
    def reader(_symbol: str) -> dict[str, float | None]:
        return dict(values)

    return reader


def test_the_veto_appears_in_the_decision_explanation() -> None:
    # Es la unica forma de auditar despues por que no se opero.
    engine = PositionQualityEngine(QuantPositionQualitySettings(min_dimensions=3, min_score=95.0))
    chain = _chain(
        engine, _inputs(expected_cost_bps=25.0, expected_r=0.3, risk_pct=1.9, max_risk_pct=2.0)
    )
    results = chain.evaluate(_context(), _consensus(score=20.0))
    blocked = [r for r in results if not r.passed]
    assert blocked
    assert blocked[0].name == "position_quality"
    assert "calidad" in (blocked[0].reason or "")


def test_a_healthy_position_passes_the_chain() -> None:
    engine = PositionQualityEngine(QuantPositionQualitySettings(min_dimensions=3))
    chain = _chain(
        engine, _inputs(expected_cost_bps=2.0, expected_r=2.0, risk_pct=0.2, max_risk_pct=2.0)
    )
    assert all(r.passed for r in chain.evaluate(_context(), _consensus()))


def test_without_the_inputs_reader_the_missing_dimensions_do_not_block() -> None:
    # Es el cableado real de hoy: coste y riesgo no se conocen cuando corre la
    # cadena. El comportamiento correcto es no bloquear por ellos.
    engine = PositionQualityEngine(QuantPositionQualitySettings(min_dimensions=6, min_score=99.0))
    assert all(r.passed for r in _chain(engine).evaluate(_context(), _consensus(score=5.0)))


def test_the_filter_stays_out_of_the_chain_when_not_wired() -> None:
    assert "position_quality" not in _chain(None).names


def test_the_engine_is_wired_from_the_real_composition_root() -> None:
    from app.engine.bootstrap import build_container

    settings = Settings()
    settings.market.enabled = True
    settings.quant.enabled = True
    container = build_container(settings)
    assert container.contains(PositionQualityEngine)
