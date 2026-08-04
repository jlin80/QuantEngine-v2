"""Holding mínimo por estrategia antes de una salida por cambio de régimen.

El evaluador continuo daba +0.26R en señales virtuales mientras la ejecución
real daba -0.15R: un `regime_change_min_holding_seconds` único cortaba a las
estrategias de tesis larga (`order_block`, ~30 min) antes de que resolvieran.
Aquí se fija el contrato del umbral resuelto por estrategia → categoría →
global, y que el límite de 4h por tiempo sigue siendo la red de seguridad.
"""

from datetime import timedelta

from app.config.settings import ExecutionSettings
from app.engine.events import DecisionGenerated
from app.execution.execution_engine.engine import _MarketView
from app.execution.models import ExitReason

from tests.unit.execution_helpers import (
    make_engine,
    make_execution_settings,
    make_market_with_state,
)
from tests.unit.quant_helpers import make_candles, make_ticker


def _market():
    return make_market_with_state(
        candles=make_candles([100.0, 101.0, 100.5, 101.5, 102.0] * 6),
        ticker=make_ticker(bid=101.9, ask=101.95),
    )


def _decision(strategy: str = "", category: str = "") -> DecisionGenerated:
    return DecisionGenerated(
        source="test",
        decision_id="d1",
        symbol="BTCUSDT",
        action="open_long",
        accepted=True,
        score=80.0,
        confidence=0.8,
        summary="momentum",
        strategy=strategy,
        strategy_category=category,
    )


# ----------------------------------------------------------------------
# Resolución del umbral (unidad pura sobre la configuración)
# ----------------------------------------------------------------------


def test_each_strategy_uses_its_own_threshold():
    """Dos estrategias con tesis de duración distinta no comparten umbral."""
    cfg = ExecutionSettings()

    order_block = cfg.min_holding_seconds_for("order_block", "smc")
    bos = cfg.min_holding_seconds_for("bos", "smc")

    assert order_block == 1950.0
    assert bos == 135.0
    assert order_block > bos


def test_unknown_strategy_falls_back_to_its_category():
    """Una estrategia nueva sin historial hereda el umbral de su categoría."""
    cfg = ExecutionSettings()

    assert cfg.min_holding_seconds_for("liquidity_sweep", "smc") == 900.0
    assert cfg.min_holding_seconds_for("volatility_compression", "volatility") == 150.0


def test_unknown_strategy_and_category_fall_back_to_the_global_value():
    """Sin estrategia ni categoría conocidas se comporta como antes del cambio."""
    cfg = ExecutionSettings(regime_change_min_holding_seconds=222.0)

    assert cfg.min_holding_seconds_for("estrategia_inventada", "categoria_inventada") == 222.0
    assert cfg.min_holding_seconds_for("", "") == 222.0


def test_choch_has_no_own_threshold_pending_clean_sample():
    """`choch` cae a su categoría: su duración de ~4s era un artefacto.

    El evaluador continuo resolvía contra la vela en curso (rango previo a la
    señal). Hasta que haya muestra nueva y limpia no se le fija valor propio.
    """
    cfg = ExecutionSettings()

    assert "choch" not in cfg.regime_change_min_holding_by_strategy
    assert cfg.min_holding_seconds_for("choch", "smc") == 900.0


def test_threshold_lookup_is_case_insensitive():
    cfg = ExecutionSettings()

    assert cfg.min_holding_seconds_for("ORDER_BLOCK", "") == 1950.0
    assert cfg.min_holding_seconds_for("desconocida", "SMC") == 900.0


# ----------------------------------------------------------------------
# Aplicación real en el motor de ejecución
# ----------------------------------------------------------------------


async def _engine_with_adverse_regime(settings: ExecutionSettings):
    """Motor con el contexto activo y una vista de mercado siempre adversa."""
    market, _ = _market()
    engine = make_engine(market, settings)
    engine._context = object()  # type: ignore[assignment]

    async def fake_view(symbol: str) -> _MarketView:
        return _MarketView(
            atr=None,
            atr_pct=None,
            spread_bps=None,
            regime="ranging",
            volatility="normal",
            volume=None,
            last_price=None,
            session="america",
        )

    engine._market_view = fake_view  # type: ignore[method-assign]
    return engine


async def test_long_thesis_strategy_is_not_cut_at_the_short_threshold():
    """`order_block` sigue viva a los 300s; `bos`, en la misma situación, sale."""
    settings = make_execution_settings(regime_exit_confirmations=1)

    engine = await _engine_with_adverse_regime(settings)
    slow = await engine.process_decision(_decision("order_block", "smc"))
    assert slow is not None
    slow.regime = "trending"
    slow.opened_at = slow.opened_at - timedelta(seconds=300)
    assert await engine._exit_reason(slow) is None

    engine = await _engine_with_adverse_regime(settings)
    fast = await engine.process_decision(_decision("bos", "smc"))
    assert fast is not None
    fast.regime = "trending"
    fast.opened_at = fast.opened_at - timedelta(seconds=300)
    assert await engine._exit_reason(fast) is ExitReason.REGIME_CHANGE


async def test_attribution_travels_from_the_decision_to_the_journal():
    """La estrategia atribuida llega a la posición y al Trade Journal."""
    engine = await _engine_with_adverse_regime(make_execution_settings())
    position = await engine.process_decision(_decision("order_block", "smc"))
    assert position is not None
    assert position.strategy == "order_block"
    assert position.strategy_category == "smc"

    await engine.close_position(position, ExitReason.MANUAL)
    trade = engine.journal.all()[-1]
    assert trade.strategy == "order_block"
    assert trade.strategy_category == "smc"


async def test_max_holding_minutes_still_overrides_a_long_threshold():
    """La red de seguridad de 4h por tiempo sigue mandando sobre el holding."""
    engine = await _engine_with_adverse_regime(make_execution_settings(max_holding_minutes=240.0))
    position = await engine.process_decision(_decision("order_block", "smc"))
    assert position is not None
    position.regime = "trending"
    position.opened_at = position.opened_at - timedelta(minutes=241)

    assert await engine._exit_reason(position) is ExitReason.TIME_EXIT


async def test_applied_threshold_is_audited_on_the_trade():
    """El umbral aplicado queda en el contexto de salida, auditable."""
    engine = await _engine_with_adverse_regime(make_execution_settings())
    position = await engine.process_decision(_decision("bos", "smc"))
    assert position is not None
    position.regime = "trending"
    position.opened_at = position.opened_at - timedelta(seconds=300)

    await engine._exit_reason(position)
    await engine.close_position(position, ExitReason.REGIME_CHANGE)

    trade = engine.journal.all()[-1]
    assert trade.context_snapshot["min_holding_seconds"] == 135.0
