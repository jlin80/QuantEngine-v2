"""AutoML: prueba modelos e hiperparámetros y guarda sólo el mejor (Fase 7).

Entrena cada candidato con validación temporal, los ordena por el objetivo
(AUC walk-forward por defecto) y devuelve un leaderboard. Los backends pesados no
instalados se saltan con una nota; nunca tumban el proceso. El AutoML no activa
nada: sólo produce el mejor candidato para que la puerta de validación decida.
"""

from dataclasses import dataclass, field
from typing import Any

from app.config.settings import MLModelSettings
from app.core.exceptions import MLError, ModelBackendUnavailableError
from app.ml.auto_ml.search import candidate_specs
from app.ml.datasets.dataset import Dataset
from app.ml.ensemble.ensemble import VotingEnsemble
from app.ml.interfaces.model import Model
from app.ml.models.factory import build_model
from app.ml.training.trainer import Trainer, TrainingResult


@dataclass(slots=True)
class AutoMLResult:
    """Outcome of an AutoML run (leaderboard + best candidate)."""

    objective: str
    leaderboard: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    best_type: str = ""
    best_result: TrainingResult | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (best model object excluded)."""
        return {
            "objective": self.objective,
            "best_type": self.best_type,
            "best": self.leaderboard[0] if self.leaderboard else None,
            "leaderboard": self.leaderboard,
            "skipped": self.skipped,
        }


class AutoML:
    """Search over models and hyperparameters, keeping the best by objective.

    Args:
        trainer: Entrenador con validación temporal.
        model_settings: Hiperparámetros base de los modelos.
        seed: Semilla para los modelos con aleatoriedad.
    """

    def __init__(self, trainer: Trainer, model_settings: MLModelSettings, *, seed: int = 7) -> None:
        self._trainer = trainer
        self._settings = model_settings
        self._seed = seed

    def run(
        self,
        dataset: Dataset,
        model_types: list[str],
        *,
        objective: str = "cv_auc",
        include_voting: bool = True,
    ) -> AutoMLResult:
        """Train every candidate and rank them by ``objective``."""
        result = AutoMLResult(objective=objective)
        for model_type, settings in candidate_specs(model_types, self._settings):
            self._evaluate(model_type, dataset, objective, result, settings)
        if include_voting:
            self._evaluate_voting(dataset, objective, result)
        result.leaderboard.sort(key=lambda entry: entry["score"], reverse=True)
        if result.leaderboard:
            best = result.leaderboard[0]
            result.best_type = str(best["model_type"])
            result.best_result = best.pop("_result")
        for entry in result.leaderboard:
            entry.pop("_result", None)
        return result

    def _evaluate(
        self,
        model_type: str,
        dataset: Dataset,
        objective: str,
        result: AutoMLResult,
        settings: MLModelSettings,
    ) -> None:
        """Train and score one candidate, recording skips on failure."""
        try:
            training = self._trainer.train(
                lambda: build_model(model_type, settings, seed=self._seed), dataset
            )
        except ModelBackendUnavailableError as exc:
            result.skipped.append({"model_type": model_type, "reason": exc.message})
            return
        except MLError as exc:
            result.skipped.append({"model_type": model_type, "reason": str(exc)})
            return
        self._record(result, model_type, training, objective)

    def _evaluate_voting(self, dataset: Dataset, objective: str, result: AutoMLResult) -> None:
        """Add a soft-voting ensemble of the native models as a candidate."""
        native = ["logistic_regression", "random_forest", "extra_trees"]

        def factory() -> Model:
            members = [build_model(t, self._settings, seed=self._seed) for t in native]
            return VotingEnsemble(members, mode="soft")

        try:
            training = self._trainer.train(factory, dataset)
        except MLError as exc:
            result.skipped.append({"model_type": "voting", "reason": str(exc)})
            return
        self._record(result, "voting", training, objective)

    @staticmethod
    def _record(
        result: AutoMLResult, model_type: str, training: TrainingResult, objective: str
    ) -> None:
        """Append a scored candidate to the leaderboard."""
        metrics = training.metrics()
        result.leaderboard.append(
            {
                "model_type": model_type,
                "params": training.params,
                "score": round(metrics.get(objective, 0.0), 6),
                "metrics": {k: round(v, 4) for k, v in metrics.items()},
                "_result": training,
            }
        )
