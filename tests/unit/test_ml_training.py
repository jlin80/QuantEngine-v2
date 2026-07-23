"""Entrenamiento, validación temporal y AutoML (Fase 7)."""

import pytest
from app.config.settings import MLModelSettings, MLTrainingSettings
from app.core.exceptions import InsufficientDataError
from app.ml.auto_ml import AutoML
from app.ml.datasets import DatasetBuilder
from app.ml.models.factory import build_model
from app.ml.training import Trainer

from tests.unit.ml_helpers import learnable_trades, make_trade


def _dataset(n: int = 80):
    return DatasetBuilder().build(learnable_trades(n))


def _factory(kind: str = "logistic_regression"):
    settings = MLModelSettings()
    return lambda: build_model(kind, settings, seed=7)


def test_dataset_from_trades_is_balanced_and_temporal():
    dataset = _dataset(80)
    assert len(dataset) == 80
    assert dataset.positives == 40  # ganadoras/perdedoras alternadas
    assert set(dataset.y) == {0, 1}
    assert dataset.n_features == len(dataset.feature_names)


def test_training_learns_the_signal():
    trainer = Trainer(MLTrainingSettings())
    result = trainer.train(_factory(), _dataset(80))
    # La señal (score/confianza/RR) es aprendible: AUC out-of-sample muy por
    # encima del azar, tanto en holdout como en walk-forward.
    assert result.holdout.auc > 0.6
    assert result.cross_validation.mean_auc > 0.6
    assert result.n_test > 0
    assert result.model.is_fitted
    assert result.feature_names == _dataset(80).feature_names


def test_training_rejects_insufficient_samples():
    trainer = Trainer(MLTrainingSettings())
    with pytest.raises(InsufficientDataError):
        trainer.train(_factory(), _dataset(10))


def test_training_rejects_single_class():
    # 70 operaciones, todas ganadoras → falta una clase en el tramo de train.
    winners = [make_trade(pnl=1.0, r_multiple=1.0) for _ in range(70)]
    dataset = DatasetBuilder().build(winners)
    trainer = Trainer(MLTrainingSettings())
    with pytest.raises(InsufficientDataError):
        trainer.train(_factory(), dataset)


def test_automl_ranks_candidates_and_keeps_best():
    trainer = Trainer(MLTrainingSettings())
    automl = AutoML(trainer, MLModelSettings(), seed=7)
    result = automl.run(
        _dataset(80),
        ["logistic_regression", "decision_tree", "random_forest", "extra_trees"],
    )
    assert result.best_result is not None
    assert result.best_type
    # Leaderboard ordenado de mejor a peor por el objetivo (cv_auc).
    scores = [entry["score"] for entry in result.leaderboard]
    assert scores == sorted(scores, reverse=True)
    # Incluye el conjunto por voto entre los candidatos.
    assert any(entry["model_type"] == "voting" for entry in result.leaderboard)


def test_automl_skips_unavailable_backends_without_crashing():
    trainer = Trainer(MLTrainingSettings())
    automl = AutoML(trainer, MLModelSettings(), seed=7)
    # XGBoost/CatBoost no están instalados: se saltan con nota, no tumban la corrida.
    result = automl.run(_dataset(80), ["logistic_regression", "xgboost", "catboost"])
    assert result.best_result is not None
    skipped = {entry["model_type"] for entry in result.skipped}
    assert {"xgboost", "catboost"} <= skipped
