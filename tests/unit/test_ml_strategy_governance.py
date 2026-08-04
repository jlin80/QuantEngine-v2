"""Inteligencia de estrategias (ranking) y Meta Strategy Manager (Fase 7)."""

from app.config.settings import MLMetaStrategySettings
from app.ml.meta import MetaStrategyManager
from app.ml.services import StrategyIntelligence

from tests.unit.ml_helpers import strategy_trades


def _intelligence() -> StrategyIntelligence:
    return StrategyIntelligence(lookback=60)


def _manager() -> MetaStrategyManager:
    return MetaStrategyManager(MLMetaStrategySettings(), _intelligence())


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_ranking_orders_strategies_by_evidence():
    labeled = [
        *[("momentum", t) for t in strategy_trades("momentum", 40, win_rate=0.8)],
        *[("meanrev", t) for t in strategy_trades("meanrev", 40, win_rate=0.2)],
    ]
    scores = _intelligence().rank(labeled)
    names = [s.name for s in scores]
    assert names[0] == "momentum"  # el consistente lidera
    assert scores[0].score > scores[-1].score
    # Segmentación por activo/sesión/régimen disponible para el asesor.
    assert scores[0].by_regime
    assert scores[0].historical.trades == 40


def test_ranking_handles_no_trades():
    assert _intelligence().rank([]) == []


# ---------------------------------------------------------------------------
# Meta Strategy Manager
# ---------------------------------------------------------------------------


def test_meta_boosts_a_consistent_strategy():
    manager = _manager()
    labeled = [("momentum", t) for t in strategy_trades("momentum", 40, win_rate=0.8)]
    report = manager.evaluate(labeled)
    assert report.weights["momentum"] > 1.0  # sube el peso del consistente
    assert "momentum" in report.active
    decision = next(d for d in report.decisions if d["strategy"] == "momentum")
    assert decision["action"] == "boost"


def test_meta_disables_degraded_strategy_after_configured_periods():
    """Cada periodo exige evidencia NUEVA, no sólo otra vuelta del reloj.

    El historial crece entre evaluaciones, que es lo que ocurre en producción:
    reevaluar el mismo lote cerrado no aporta una observación independiente.
    """
    settings = MLMetaStrategySettings()
    manager = MetaStrategyManager(settings, _intelligence())

    report = None
    for period in range(settings.disable_after_periods):
        trades = strategy_trades("meanrev", 40 + period + 1, win_rate=0.2)
        report = manager.evaluate([("meanrev", t) for t in trades])

    assert report is not None
    assert "meanrev" in report.disabled  # desactivada tras degradación sostenida
    assert report.weights["meanrev"] == settings.min_weight
    # El historial de auditoría registra la decisión de desactivación.
    actions = [entry["action"] for entry in manager.history()]
    assert "disable" in actions


def test_reevaluating_the_same_history_never_disables_a_strategy():
    """Regresión: sin esta guarda, `disable_after_periods` medía horas, no
    degradación sostenida — y con un job horario apagaba estrategias en 3h."""
    settings = MLMetaStrategySettings()
    manager = MetaStrategyManager(settings, _intelligence())
    labeled = [("meanrev", t) for t in strategy_trades("meanrev", 40, win_rate=0.2)]

    report = None
    for _ in range(settings.disable_after_periods * 4):
        report = manager.evaluate(labeled)

    assert report is not None
    assert "meanrev" not in report.disabled


def test_a_disabled_strategy_is_not_re_announced_every_evaluation():
    """Regresión: reanunciar `disable` en cada vuelta llenaba Discord de avisos
    idénticos y hacía que el aplicador reescribiera lo ya aplicado."""
    settings = MLMetaStrategySettings()
    manager = MetaStrategyManager(settings, _intelligence())

    for period in range(settings.disable_after_periods + 4):
        trades = strategy_trades("meanrev", 40 + period + 1, win_rate=0.2)
        report = manager.evaluate([("meanrev", t) for t in trades])

    disables = [d for d in report.decisions if d["action"] == "disable"]
    assert disables == [], "ya estaba desactivada: es un estado, no una decisión"
    assert [e["action"] for e in manager.history()].count("disable") == 1


def test_meta_keeps_degraded_strategy_active_before_threshold():
    settings = MLMetaStrategySettings()
    manager = MetaStrategyManager(settings, _intelligence())
    labeled = [("meanrev", t) for t in strategy_trades("meanrev", 40, win_rate=0.2)]
    # Una sola evaluación degradada no desactiva todavía (periodo configurable).
    report = manager.evaluate(labeled)
    assert "meanrev" in report.active
    assert "meanrev" not in report.disabled


def test_meta_recommends_complementary_combinations():
    manager = _manager()
    momentum = strategy_trades("momentum", 40, win_rate=0.75, regime="trending")
    breakout = strategy_trades("breakout", 40, win_rate=0.75, regime="breakout")
    labeled = [
        *[("momentum", t) for t in momentum],
        *[("breakout", t) for t in breakout],
    ]
    suggestions = manager.recommend_combinations(labeled, limit=3)
    assert suggestions
    assert all(len(s["strategies"]) == 2 for s in suggestions)


def test_meta_status_is_dashboard_ready():
    manager = _manager()
    manager.evaluate([("momentum", t) for t in strategy_trades("momentum", 40, win_rate=0.8)])
    status = manager.status()
    assert status["enabled"] is True
    assert status["evaluations"] == 1
    assert "momentum" in status["weights"]
