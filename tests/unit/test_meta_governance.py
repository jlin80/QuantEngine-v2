"""El Meta Strategy Manager gobernando de verdad (Bloque 3).

Antes de este bloque el MSM existía pero no gobernaba nada útil por dos motivos
independientes, ambos cubiertos aquí:

1. **No veía estrategias.** `label_trades` etiquetaba todas las operaciones como
   ``portfolio``, así que el MSM recibía una única entrada agregada.
2. **Nadie aplicaba sus decisiones.** Publicaba pesos y activaciones que sólo
   escuchaba el notificador de Discord; el consenso seguía usando los pesos de
   arranque.
"""

import asyncio
from typing import Any

import pytest
from app.config.settings import MLMetaStrategySettings
from app.core.events.bus import EventBus
from app.core.exceptions import ConfigurationError
from app.engine.meta_governance import MetaGovernanceApplier
from app.ml.events import MetaStrategyDecision, StrategyWeightsUpdated
from app.ml.meta import MetaStrategyManager
from app.ml.services import StrategyIntelligence, VirtualStrategyStats

from tests.unit.ml_helpers import make_trade, strategy_trades


def _intelligence() -> StrategyIntelligence:
    return StrategyIntelligence(lookback=60)


def _manager(**overrides: Any) -> MetaStrategyManager:
    return MetaStrategyManager(MLMetaStrategySettings(**overrides), _intelligence())


# ----------------------------------------------------------------------
# 1. El MSM ve estrategias, no un agregado "portfolio"
# ----------------------------------------------------------------------


def test_trades_are_labelled_with_their_own_strategy():
    trades = [
        make_trade(strategy="order_block"),
        make_trade(strategy="bos"),
    ]

    labeled = StrategyIntelligence.label_trades(trades)

    assert sorted(name for name, _ in labeled) == ["bos", "order_block"]


def test_legacy_journal_entries_still_resolve_from_the_context_snapshot():
    """El journal escrito antes de que existiera el campo sigue siendo legible."""
    legacy = make_trade(context_snapshot={"strategy": "fair_value_gap"})

    ((name, _),) = StrategyIntelligence.label_trades([legacy])

    assert name == "fair_value_gap"


def test_unattributable_trades_fall_back_to_portfolio():
    """Una posición adoptada del broker no pertenece a ninguna estrategia."""
    ((name, _),) = StrategyIntelligence.label_trades([make_trade()])

    assert name == "portfolio"


def test_the_manager_governs_each_strategy_separately():
    manager = _manager()
    labeled = [
        *[("order_block", t) for t in strategy_trades("order_block", 40, win_rate=0.8)],
        *[("atr_expansion", t) for t in strategy_trades("atr_expansion", 40, win_rate=0.2)],
    ]

    report = manager.evaluate(labeled)

    assert set(report.weights) == {"order_block", "atr_expansion"}
    assert report.weights["order_block"] > report.weights["atr_expansion"]


# ----------------------------------------------------------------------
# Evidencia mixta: Trade Journal (Fase 5) + evaluador continuo (Fase 4)
# ----------------------------------------------------------------------


def _virtual(name: str, expectancy: float, evaluated: int = 300) -> VirtualStrategyStats:
    return VirtualStrategyStats(
        strategy=name,
        evaluated=evaluated,
        win_rate=0.6 if expectancy > 0 else 0.3,
        profit_factor=1.8 if expectancy > 0 else 0.6,
        expectancy_r=expectancy,
    )


def test_good_virtual_evidence_never_raises_the_effective_score():
    """La evidencia virtual sólo puede frenar, nunca empujar (2026-08-26).

    Antes esta prueba exigía lo contrario: que 300 señales evaluadas subieran el
    score de una estrategia con 4 operaciones cerradas. Se midió el sesgo del
    evaluador contra la ejecución real y resultó no sólo optimista sino
    optimista de forma DESIGUAL — sesgo medio +0.3457R, rango -0.0684 a +1.1039,
    y el orden cambia en 8 de 10 posiciones. Promover con ese ranking es
    promover casi al azar. Ver `docs/evaluator_bias.md`.
    """
    manager = _manager(min_trades=20)
    labeled = [("order_block", t) for t in strategy_trades("order_block", 4, win_rate=0.5)]

    report = manager.evaluate(labeled, {"order_block": _virtual("order_block", 1.0)})

    decision = next(d for d in report.decisions if d["strategy"] == "order_block")
    assert decision["evidence"]["source"] == "blended"
    assert decision["evidence"]["executed_weight"] == pytest.approx(0.2)
    assert decision["evidence"]["virtual_capped"] is True
    assert decision["metrics"]["effective_score"] == decision["metrics"]["score"]


def test_bad_virtual_evidence_still_drags_the_score_down():
    """Lo que sí conserva valor: salir mal AUN con una estimación sesgada al alza.

    Una estrategia que el evaluador optimista ya puntúa por debajo de su
    resultado ejecutado es mala con bastante seguridad, así que el mezclado
    sigue pudiendo bajarla.
    """
    manager = _manager(min_trades=20)
    labeled = [("order_block", t) for t in strategy_trades("order_block", 4, win_rate=1.0)]

    report = manager.evaluate(labeled, {"order_block": _virtual("order_block", -2.0)})

    decision = next(d for d in report.decisions if d["strategy"] == "order_block")
    assert decision["metrics"]["effective_score"] < decision["metrics"]["score"]
    assert decision["evidence"]["virtual_capped"] is False


def test_executed_evidence_wins_once_the_sample_is_sufficient():
    manager = _manager(min_trades=20)
    labeled = [("order_block", t) for t in strategy_trades("order_block", 40, win_rate=0.8)]

    report = manager.evaluate(labeled, {"order_block": _virtual("order_block", -1.0)})

    decision = next(d for d in report.decisions if d["strategy"] == "order_block")
    assert decision["evidence"]["source"] == "executed"
    assert decision["metrics"]["effective_score"] == decision["metrics"]["score"]


def test_virtual_evidence_alone_never_disables_a_strategy():
    """El rendimiento virtual no incluye costes, slippage ni salidas por régimen:
    no puede apagar una estrategia por sí solo."""
    settings = MLMetaStrategySettings(min_trades=20)
    manager = MetaStrategyManager(settings, _intelligence())
    labeled = [("atr_expansion", t) for t in strategy_trades("atr_expansion", 5, win_rate=0.0)]
    virtual = {"atr_expansion": _virtual("atr_expansion", -2.0)}

    report = None
    for _ in range(settings.disable_after_periods + 2):
        report = manager.evaluate(labeled, virtual)

    assert report is not None
    assert "atr_expansion" not in report.disabled


def test_missing_virtual_stats_are_not_an_error():
    manager = _manager()
    labeled = [("order_block", t) for t in strategy_trades("order_block", 40, win_rate=0.8)]

    report = manager.evaluate(labeled, {})

    decision = next(d for d in report.decisions if d["strategy"] == "order_block")
    assert decision["evidence"]["source"] == "executed"


# ----------------------------------------------------------------------
# 2. Las decisiones se aplican de verdad, y quedan auditadas
# ----------------------------------------------------------------------


class _FakeStrategies:
    """Doble del Strategy Engine con su superficie de gobierno."""

    def __init__(self, weights: dict[str, float]) -> None:
        self._weights = dict(weights)
        self.enabled: dict[str, bool] = dict.fromkeys(weights, True)

    @property
    def loaded(self) -> list[str]:
        return sorted(self._weights)

    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    def set_weight(self, name: str, weight: float) -> float:
        if name not in self._weights:
            raise ConfigurationError(f"Strategy '{name}' is not loaded")
        self._weights[name] = max(0.0, float(weight))
        return self._weights[name]

    def enable_strategy(self, name: str) -> None:
        self.enabled[name] = True

    def disable_strategy(self, name: str) -> None:
        self.enabled[name] = False


class _RecordingAudit:
    """Audit log mínimo que sólo guarda lo que se le pide registrar."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> dict[str, Any]:
        self.entries.append(kwargs)
        return kwargs


async def _applier(
    strategies: _FakeStrategies, audit: _RecordingAudit, *, enabled: bool = True
) -> tuple[MetaGovernanceApplier, EventBus]:
    bus = EventBus()
    await bus.start()
    applier = MetaGovernanceApplier(
        strategies,  # type: ignore[arg-type]
        bus,
        audit=audit,  # type: ignore[arg-type]
        enabled=enabled,
    )
    await applier.start()
    return applier, bus


async def test_published_weights_reach_the_strategy_engine():
    strategies = _FakeStrategies({"order_block": 1.0, "bos": 1.0})
    audit = _RecordingAudit()
    applier, bus = await _applier(strategies, audit)

    await bus.publish(
        StrategyWeightsUpdated(
            source="ml", weights={"order_block": 1.8, "bos": 0.3}, reason="evaluación periódica"
        )
    )
    await asyncio.sleep(0.05)
    await applier.stop()
    await bus.stop()

    assert strategies.weights() == {"order_block": 1.8, "bos": 0.3}


async def test_every_applied_change_is_audited():
    strategies = _FakeStrategies({"order_block": 1.0})
    audit = _RecordingAudit()
    applier, bus = await _applier(strategies, audit)

    await bus.publish(
        StrategyWeightsUpdated(source="ml", weights={"order_block": 1.8}, reason="consistente")
    )
    await asyncio.sleep(0.05)
    await applier.stop()
    await bus.stop()

    (entry,) = audit.entries
    assert entry["actor"] == "meta_strategy_manager"
    assert entry["target"] == "order_block"
    assert entry["before"] == 1.0
    assert entry["after"] == 1.8
    assert entry["meta"]["applied"] is True
    assert entry["meta"]["reason"] == "consistente"


async def test_an_unchanged_weight_does_not_pollute_the_audit_trail():
    strategies = _FakeStrategies({"order_block": 1.0})
    audit = _RecordingAudit()
    applier, bus = await _applier(strategies, audit)

    await bus.publish(StrategyWeightsUpdated(source="ml", weights={"order_block": 1.0}))
    await asyncio.sleep(0.05)
    await applier.stop()
    await bus.stop()

    assert audit.entries == []


async def test_weights_for_unloaded_strategies_are_ignored_quietly():
    strategies = _FakeStrategies({"order_block": 1.0})
    audit = _RecordingAudit()
    applier, bus = await _applier(strategies, audit)

    await bus.publish(StrategyWeightsUpdated(source="ml", weights={"retirada": 1.5}))
    await asyncio.sleep(0.05)
    status = applier.status()
    await applier.stop()
    await bus.stop()

    assert audit.entries == []
    assert status["skipped"] == 1


async def test_activation_decisions_are_applied_and_audited():
    strategies = _FakeStrategies({"atr_expansion": 1.0})
    audit = _RecordingAudit()
    applier, bus = await _applier(strategies, audit)

    await bus.publish(
        MetaStrategyDecision(
            source="ml", strategy="atr_expansion", action="disable", detail="degradada 3 periodos"
        )
    )
    await asyncio.sleep(0.05)
    await applier.stop()
    await bus.stop()

    assert strategies.enabled["atr_expansion"] is False
    (entry,) = audit.entries
    assert entry["action"].value == "strategy.disabled"
    assert entry["meta"]["reason"] == "degradada 3 periodos"


async def test_keep_decisions_do_nothing():
    strategies = _FakeStrategies({"order_block": 1.0})
    audit = _RecordingAudit()
    applier, bus = await _applier(strategies, audit)

    await bus.publish(
        MetaStrategyDecision(source="ml", strategy="order_block", action="keep", detail="estable")
    )
    await asyncio.sleep(0.05)
    await applier.stop()
    await bus.stop()

    assert strategies.enabled["order_block"] is True
    assert audit.entries == []


async def test_observation_mode_audits_what_it_would_have_done_without_doing_it():
    """`apply_governance=false` deja observar al MSM antes de dejarle gobernar."""
    strategies = _FakeStrategies({"order_block": 1.0})
    audit = _RecordingAudit()
    applier, bus = await _applier(strategies, audit, enabled=False)

    await bus.publish(StrategyWeightsUpdated(source="ml", weights={"order_block": 1.8}))
    await asyncio.sleep(0.05)
    await applier.stop()
    await bus.stop()

    assert strategies.weights() == {"order_block": 1.0}, "no toca nada"
    (entry,) = audit.entries
    assert entry["meta"]["applied"] is False
    assert entry["after"] == 1.8, "pero deja constancia de lo que habría hecho"
