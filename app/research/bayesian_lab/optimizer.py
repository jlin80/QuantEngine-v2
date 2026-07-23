"""Tree-structured Parzen Estimator (TPE) — optimización bayesiana (Fase 10)."""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.backtesting.optimizer.space import ParameterSpace, ParameterSpec
from app.config.settings import BayesianLabSettings

Objective = Callable[[dict[str, Any]], float]
_EPS = 1e-9


@dataclass(frozen=True, kw_only=True, slots=True)
class BayesianTrial:
    """One evaluated point in a TPE run."""

    params: dict[str, Any]
    score: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {"params": dict(self.params), "score": round(self.score, 6)}


@dataclass(frozen=True, kw_only=True, slots=True)
class BayesianResult:
    """Outcome of a Bayesian (TPE) optimization run."""

    method: str
    objective: str
    best_params: dict[str, Any]
    best_score: float
    evaluations: int
    trials: tuple[BayesianTrial, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "method": self.method,
            "objective": self.objective,
            "best_params": dict(self.best_params),
            "best_score": round(self.best_score, 6) if math.isfinite(self.best_score) else None,
            "evaluations": self.evaluations,
            "trials": [t.to_dict() for t in self.trials],
        }


class TPEOptimizer:
    """Bayesian optimization via a Tree-structured Parzen Estimator.

    Args:
        settings: Configuración del laboratorio bayesiano.
        objective: Nombre informativo de la métrica que se maximiza.
    """

    def __init__(self, settings: BayesianLabSettings, *, objective: str = "score") -> None:
        self._settings = settings
        self._objective = objective

    def optimize(self, space: ParameterSpace, evaluate: Objective) -> BayesianResult:
        """Maximize ``evaluate`` over ``space`` using TPE.

        Args:
            space: Espacio de parámetros (rangos continuos/enteros).
            evaluate: Función objetivo a maximizar.

        Returns:
            El mejor punto hallado y el historial de la corrida.
        """
        rng = random.Random(self._settings.random_seed)
        specs = space.specs
        trials: list[BayesianTrial] = []

        def _run(params: dict[str, Any]) -> None:
            score = evaluate(params)
            if not math.isfinite(score):
                score = float("-inf")
            trials.append(BayesianTrial(params=params, score=score))

        startup = min(self._settings.n_startup, self._settings.max_evaluations)
        for _ in range(max(1, startup)):
            _run(space.sample(rng))

        while len(trials) < self._settings.max_evaluations:
            ordered = sorted(trials, key=lambda t: t.score, reverse=True)
            n_good = max(1, math.ceil(self._settings.gamma * len(ordered)))
            good = ordered[:n_good]
            bad = ordered[n_good:] or good
            candidate = self._propose(specs, good, bad, rng)
            _run(candidate)

        best = max(trials, key=lambda t: t.score)
        return BayesianResult(
            method="bayesian_tpe",
            objective=self._objective,
            best_params=dict(best.params),
            best_score=best.score,
            evaluations=len(trials),
            trials=tuple(trials),
        )

    def _propose(
        self,
        specs: list[ParameterSpec],
        good: list[BayesianTrial],
        bad: list[BayesianTrial],
        rng: random.Random,
    ) -> dict[str, Any]:
        """Sample candidates near the good set and pick the best l/g ratio."""
        best_candidate: dict[str, Any] | None = None
        best_ratio = float("-inf")
        explore = max(1, self._settings.candidate_pool // 5)
        for i in range(self._settings.candidate_pool):
            if i < explore:
                candidate = {spec.name: spec.sample(rng) for spec in specs}  # exploración
            else:
                candidate = self._sample_near_good(specs, good, rng)
            ratio = self._log_ratio(specs, candidate, good, bad)
            if ratio > best_ratio:
                best_ratio = ratio
                best_candidate = candidate
        return (
            best_candidate if best_candidate is not None else {s.name: s.sample(rng) for s in specs}
        )

    @staticmethod
    def _bandwidth(spec: ParameterSpec) -> float:
        """Kernel bandwidth for a spec (a fraction of its range)."""
        if spec.low is None or spec.high is None:
            return 1.0
        return max(_EPS, (spec.high - spec.low) * 0.15)

    def _sample_near_good(
        self, specs: list[ParameterSpec], good: list[BayesianTrial], rng: random.Random
    ) -> dict[str, Any]:
        """Draw a candidate from a Gaussian around a random good observation."""
        center = rng.choice(good)
        candidate: dict[str, Any] = {}
        for spec in specs:
            base = float(center.params.get(spec.name, 0.0))
            value = rng.gauss(base, self._bandwidth(spec))
            if spec.low is not None and spec.high is not None:
                value = min(spec.high, max(spec.low, value))
            candidate[spec.name] = round(value) if spec.is_int else value
        return candidate

    def _log_ratio(
        self,
        specs: list[ParameterSpec],
        candidate: dict[str, Any],
        good: list[BayesianTrial],
        bad: list[BayesianTrial],
    ) -> float:
        """Sum of per-parameter log density ratios log l(x) - log g(x)."""
        total = 0.0
        for spec in specs:
            x = float(candidate[spec.name])
            bw = self._bandwidth(spec)
            good_density = _kde([float(t.params[spec.name]) for t in good], x, bw)
            bad_density = _kde([float(t.params[spec.name]) for t in bad], x, bw)
            total += math.log(good_density + _EPS) - math.log(bad_density + _EPS)
        return total


def _kde(observations: list[float], x: float, bandwidth: float) -> float:
    """Gaussian kernel density estimate at ``x`` (mean of kernels)."""
    if not observations or bandwidth <= 0.0:
        return _EPS
    coef = 1.0 / (bandwidth * math.sqrt(2.0 * math.pi))
    acc = 0.0
    for obs in observations:
        z = (x - obs) / bandwidth
        acc += coef * math.exp(-0.5 * z * z)
    return acc / len(observations)
