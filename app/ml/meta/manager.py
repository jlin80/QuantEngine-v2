"""Meta Strategy Manager (Fase 7) — gobierno por encima de estrategias y ML.

Analiza continuamente el rendimiento de todas las estrategias y gestiona su
activación, prioridad y peso. Reduce el peso de las degradadas, sube el de las
consistentes y desactiva las que incumplen los mínimos durante un periodo
configurable. Recomienda combinaciones nuevas para el laboratorio y guarda un
historial de decisiones para auditoría.

Nunca modifica el código de las estrategias: sólo su configuración (activación y
ponderación). Y nunca abre ni cierra posiciones: sus pesos alimentan al consenso,
que sigue pasando por el Decision Engine y el Risk Manager. Solo paper trading.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import MLMetaStrategySettings
from app.ml.models.math import clamp
from app.ml.services.strategy_intelligence import (
    EdgeHealthStats,
    LabeledTrade,
    StrategyIntelligence,
    StrategyScore,
    VirtualStrategyStats,
)
from app.utils.time import utc_now


@dataclass(frozen=True, slots=True)
class MetaReport:
    """Outcome of a meta-strategy evaluation cycle."""

    weights: dict[str, float] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    active: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    ranking: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return {
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "decisions": self.decisions,
            "active": self.active,
            "disabled": self.disabled,
            "ranking": self.ranking,
        }


class MetaStrategyManager:
    """Govern strategy activation, priority and weight from live evidence.

    Args:
        settings: Parámetros del gobierno de estrategias.
        intelligence: Motor de puntuación de estrategias.
    """

    def __init__(
        self, settings: MLMetaStrategySettings, intelligence: StrategyIntelligence
    ) -> None:
        self._settings = settings
        self._intelligence = intelligence
        self._weights: dict[str, float] = {}
        self._active: dict[str, bool] = {}
        self._fail_counts: dict[str, int] = {}
        # Nº de operaciones visto en la última evaluación de cada estrategia:
        # sirve para no contar dos veces la misma evidencia (ver `_govern`).
        self._seen_trades: dict[str, int] = {}
        self._audit: list[dict[str, Any]] = []
        self._evaluations = 0

    def evaluate(
        self,
        labeled_trades: Sequence[LabeledTrade],
        virtual: Mapping[str, VirtualStrategyStats] | None = None,
        edge: Mapping[str, EdgeHealthStats] | None = None,
    ) -> MetaReport:
        """Score strategies and adjust their weights/activation.

        Args:
            labeled_trades: Operaciones ejecutadas etiquetadas por estrategia
                (Trade Journal, Fase 5). Miden lo que el motor **capturó**.
            virtual: Rendimiento por estrategia del evaluador continuo (Fase 4).
                Mide la calidad de la señal *en sí*, sin ejecución de por medio.
                Se usa como **prior** mientras la muestra ejecutada sea escasa:
                una estrategia con 4 operaciones cerradas no da evidencia para
                mover su peso, pero puede tener cientos de señales evaluadas.
            edge: Salud del edge por estrategia (Edge Research Engine, Bloque 1).
                Mide si el edge **sigue** ahí, no cuánto vale. Actúa sólo como
                **freno del peso**: un edge deteriorado amortigua el peso
                objetivo, nunca lo sube y nunca desactiva por sí solo. Un motor
                que apaga estrategias con una métrica de tendencia sobre unas
                decenas de resoluciones apaga estrategias por ruido.

        Returns:
            El informe del ciclo, con pesos, decisiones y auditoría.
        """
        scores = self._intelligence.rank(labeled_trades)
        virtual_stats = dict(virtual or {})
        edge_stats = dict(edge or {})
        decisions: list[dict[str, Any]] = []
        for score in scores:
            decisions.append(
                self._govern(score, virtual_stats.get(score.name), edge_stats.get(score.name))
            )
        self._evaluations += 1
        active = [name for name, on in self._active.items() if on]
        disabled = [name for name, on in self._active.items() if not on]
        return MetaReport(
            weights=dict(self._weights),
            decisions=decisions,
            active=sorted(active),
            disabled=sorted(disabled),
            ranking=[s.to_dict() for s in scores],
        )

    def _govern(
        self,
        score: StrategyScore,
        virtual: VirtualStrategyStats | None = None,
        edge: EdgeHealthStats | None = None,
    ) -> dict[str, Any]:
        """Decide activation and weight for a single strategy."""
        name = score.name
        previous_weight = self._weights.get(name, 1.0)
        was_active = self._active.get(name, True)
        degraded = self._is_degraded(score)

        # Sólo cuenta como "periodo degradado" si hay evidencia NUEVA. Sin esta
        # guarda, evaluar cada hora sobre el mismo historial cerrado convierte
        # una única observación en N observaciones: `disable_after_periods=3`
        # dejaba de significar "degradada de forma sostenida" y pasaba a
        # significar "degradada, y han pasado 3 horas". Los periodos tienen que
        # ser observaciones independientes o el umbral no mide nada.
        fresh_evidence = self._seen_trades.get(name) != score.trades
        self._seen_trades[name] = score.trades
        if degraded and fresh_evidence:
            self._fail_counts[name] = self._fail_counts.get(name, 0) + 1
        elif not degraded:
            self._fail_counts[name] = 0

        effective_score, evidence = self._effective_score(score, virtual)

        if self._fail_counts[name] >= self._settings.disable_after_periods:
            self._active[name] = False
            self._weights[name] = self._settings.min_weight
            # `disable` sólo es una DECISIÓN la primera vez; a partir de ahí es
            # un estado. Reanunciarlo en cada vuelta llenaba Discord de avisos
            # idénticos y hacía que el aplicador reescribiera lo ya aplicado.
            action = "disable" if was_active else "keep_disabled"
            detail = f"Degradada {self._fail_counts[name]} evaluaciones seguidas; se desactiva."
        else:
            self._active[name] = True
            target = self._target_weight(effective_score)
            # El freno del edge se aplica al OBJETIVO, no al peso ya suavizado:
            # así el amortiguamiento entra por el mismo suavizado que todo lo
            # demás y no produce saltos que la auditoría no pueda explicar.
            if edge is not None and edge.factor < 1.0:
                target *= edge.factor
            new_weight = previous_weight + self._settings.weight_smoothing * (
                target - previous_weight
            )
            self._weights[name] = round(
                clamp(new_weight, self._settings.min_weight, self._settings.max_weight), 4
            )
            action = self._classify(was_active, previous_weight, self._weights[name])
            detail = self._detail(action, score)

        decision = {
            "strategy": name,
            "action": action,
            "weight": self._weights[name],
            "previous_weight": round(previous_weight, 4),
            "active": self._active[name],
            "detail": detail,
            "metrics": {
                "score": round(score.score, 2),
                "effective_score": round(effective_score, 2),
                "trades": score.trades,
                "expectancy_r": round(score.recent.expectancy_r, 4),
                "profit_factor": round(score.recent.profit_factor, 4),
            },
            # Qué evidencia sostuvo esta decisión (ejecutada / virtual / mixta)
            # y con qué peso. Sin esto no se puede auditar por qué subió el peso
            # de una estrategia con 4 operaciones cerradas.
            "evidence": evidence,
            # Salud del edge que frenó (o no) el peso objetivo. `None` cuando el
            # Edge Research Engine no tenía muestra: sin evidencia no penaliza.
            "edge": None if edge is None else edge.to_dict(),
        }
        if action not in ("keep", "keep_disabled"):
            self._audit.append(
                {"evaluation": self._evaluations, "at": utc_now().isoformat(), **decision}
            )
        return decision

    def _effective_score(
        self, score: StrategyScore, virtual: VirtualStrategyStats | None
    ) -> tuple[float, dict[str, Any]]:
        """Blend executed and virtual evidence into the score that sets weight.

        La muestra ejecutada manda en cuanto es suficiente. Por debajo de
        ``min_trades`` su peso decrece linealmente y el resto lo aporta el
        evaluador continuo, que para entonces suele tener cientos de señales
        resueltas de la misma estrategia. Sin esto, una estrategia con 4
        operaciones cerradas movía su peso con evidencia que no es evidencia.

        Nota deliberada: esto **sólo** afecta al peso. La desactivación
        (:meth:`_is_degraded`) sigue exigiendo muestra ejecutada — no se apaga
        una estrategia por su rendimiento virtual, que no incluye costes,
        slippage ni salidas por régimen.

        **La evidencia virtual sólo puede frenar, nunca empujar** (2026-08-26).
        Se midió el sesgo del evaluador contra la ejecución real y no sólo es
        optimista: es optimista de forma **desigual**. Sesgo medio +0.3457R,
        rango de -0.0684R a +1.1039R, y **el orden cambia en 8 de 10
        posiciones** — `choch` y `vwap_mean_reversion` figuran entre las mejores
        virtualmente y son perdedoras reales. Un sesgo constante desplazaría a
        todas por igual y no rompería un ranking; este lo reordena, así que
        usarlo para *promover* es promover casi al azar. Ver
        ``docs/evaluator_bias.md``.

        Lo que sí conserva valor: una estrategia que sale mal **incluso con una
        estimación sesgada al alza** es mala con bastante seguridad. Por eso el
        mezclado se acota a la baja. Es la misma regla que Edge Research ya
        aplica a su multiplicador (ADR-100): frena, no empuja.

        Returns:
            El score efectivo y la traza de qué evidencia lo sostiene.
        """
        min_trades = max(1, self._settings.min_trades)
        executed_weight = min(1.0, score.trades / min_trades)
        if virtual is None or virtual.evaluated <= 0 or executed_weight >= 1.0:
            return score.score, {
                "source": "executed",
                "executed_weight": round(executed_weight, 4),
                "executed_trades": score.trades,
            }
        virtual_score = virtual.score()
        blended = executed_weight * score.score + (1.0 - executed_weight) * virtual_score
        effective = min(blended, score.score)
        return effective, {
            "source": "blended" if score.trades else "virtual",
            "executed_weight": round(executed_weight, 4),
            "executed_trades": score.trades,
            "virtual_score": round(virtual_score, 2),
            "virtual_capped": effective < blended,
            "virtual": virtual.to_dict(),
        }

    def _is_degraded(self, score: StrategyScore) -> bool:
        """Whether a strategy currently fails the minimum criteria."""
        if score.trades < self._settings.min_trades or score.recent.trades == 0:
            return False
        return (
            score.recent.expectancy_r < self._settings.disable_expectancy_r
            or score.recent.profit_factor < self._settings.disable_profit_factor
        )

    def _target_weight(self, score_value: float) -> float:
        """Map a 0-100 strategy score to a target weight."""
        span = self._settings.max_weight - self._settings.min_weight
        return self._settings.min_weight + span * (score_value / 100.0)

    @staticmethod
    def _classify(was_active: bool, previous: float, new: float) -> str:
        """Classify the governance action from the weight change."""
        if not was_active:
            return "enable"
        if new > previous + 0.05:
            return "boost"
        if new < previous - 0.05:
            return "reduce"
        return "keep"

    @staticmethod
    def _detail(action: str, score: StrategyScore) -> str:
        """Human-readable detail for a governance action."""
        mapping = {
            "boost": "Rendimiento consistente: se aumenta el peso.",
            "reduce": "Rendimiento a la baja: se reduce el peso.",
            "enable": "Recuperada: se reactiva.",
            "keep": "Sin cambios relevantes.",
        }
        return f"{mapping.get(action, '')} (score {score.score:.0f})"

    def recommend_combinations(
        self, labeled_trades: Sequence[LabeledTrade], *, limit: int = 3
    ) -> list[dict[str, Any]]:
        """Suggest complementary strategy pairs for the backtest lab."""
        scores = self._intelligence.rank(labeled_trades)
        strong = [s for s in scores if s.score >= 55.0 and s.trades >= self._settings.min_trades]
        suggestions: list[dict[str, Any]] = []
        for i, first in enumerate(strong):
            for second in strong[i + 1 :]:
                regime_a = _best_regime(first)
                regime_b = _best_regime(second)
                if regime_a and regime_b and regime_a != regime_b:
                    suggestions.append(
                        {
                            "strategies": [first.name, second.name],
                            "regimes": {first.name: regime_a, second.name: regime_b},
                            "rationale": (
                                f"'{first.name}' rinde en '{regime_a}' y '{second.name}' en "
                                f"'{regime_b}': combinarlas podría cubrir más regímenes."
                            ),
                        }
                    )
                if len(suggestions) >= limit:
                    return suggestions
        return suggestions

    def weights(self) -> dict[str, float]:
        """Current dynamic weight per strategy."""
        return dict(self._weights)

    def active_strategies(self) -> list[str]:
        """Strategies currently kept active."""
        return sorted(name for name, on in self._active.items() if on)

    def history(self) -> list[dict[str, Any]]:
        """Audit trail of governance decisions."""
        return list(self._audit)

    def status(self) -> dict[str, Any]:
        """Compact meta-manager status for the dashboard."""
        return {
            "enabled": self._settings.enabled,
            "evaluations": self._evaluations,
            "strategies": len(self._weights),
            "weights": {k: round(v, 4) for k, v in self._weights.items()},
            "active": self.active_strategies(),
            "disabled": sorted(name for name, on in self._active.items() if not on),
        }


def _best_regime(score: StrategyScore) -> str | None:
    """The regime where a strategy performs best (by expectancy)."""
    ranked = sorted(
        score.by_regime.items(), key=lambda kv: kv[1].get("expectancy_r", 0.0), reverse=True
    )
    return ranked[0][0] if ranked else None
