"""Fábrica de modelos: construye cualquier modelo desde su tipo.

Todos los modelos son intercambiables: el resto del sistema pide un
``ModelType`` y recibe un ``Model`` listo para entrenar. Los tipos nativos usan
los hiperparámetros de ``MLModelSettings``; los backends pesados quedan
preparados y fallan con un mensaje claro si su librería no está instalada. Los
conjuntos (voting/stacking) se construyen en ``app.ml.ensemble`` porque
requieren sub-modelos.
"""

from app.config.settings import MLModelSettings
from app.core.exceptions import MLError
from app.ml.interfaces.model import Model, ModelType
from app.ml.models.external import (
    CatBoostModel,
    LightGBMModel,
    NeuralNetModel,
    XGBoostModel,
)
from app.ml.models.forest import ExtraTreesModel, RandomForestModel
from app.ml.models.logistic_regression import LogisticRegressionModel
from app.ml.models.tree import DecisionTreeModel


def build_model(model_type: ModelType | str, settings: MLModelSettings, *, seed: int = 7) -> Model:
    """Build an untrained model for a given type.

    Args:
        model_type: Tipo de modelo (``ModelType`` o su valor string).
        settings: Hiperparámetros por defecto de los modelos nativos.
        seed: Semilla para los modelos con aleatoriedad (determinismo).

    Returns:
        Un :class:`Model` sin entrenar.

    Raises:
        MLError: Para voting/stacking (usar ``app.ml.ensemble``) o tipo inválido.
        ModelBackendUnavailableError: Si un backend pesado no está instalado (al
            entrenar).
    """
    kind = ModelType(model_type) if not isinstance(model_type, ModelType) else model_type
    if kind is ModelType.LOGISTIC_REGRESSION:
        return LogisticRegressionModel(
            learning_rate=settings.learning_rate, epochs=settings.epochs, l2=settings.l2
        )
    if kind is ModelType.DECISION_TREE:
        return DecisionTreeModel(
            max_depth=settings.max_depth,
            min_samples_split=settings.min_samples_split,
            max_features=1.0,
            seed=seed,
        )
    if kind is ModelType.RANDOM_FOREST:
        return RandomForestModel(
            n_estimators=settings.n_estimators,
            max_depth=settings.max_depth,
            min_samples_split=settings.min_samples_split,
            max_features=settings.max_features,
            seed=seed,
        )
    if kind is ModelType.EXTRA_TREES:
        return ExtraTreesModel(
            n_estimators=settings.n_estimators,
            max_depth=settings.max_depth,
            min_samples_split=settings.min_samples_split,
            max_features=settings.max_features,
            seed=seed,
        )
    if kind is ModelType.XGBOOST:
        return XGBoostModel()
    if kind is ModelType.LIGHTGBM:
        return LightGBMModel()
    if kind is ModelType.CATBOOST:
        return CatBoostModel()
    if kind is ModelType.NEURAL_NET:
        return NeuralNetModel()
    raise MLError(
        f"El tipo '{kind.value}' es un conjunto; constrúyelo con app.ml.ensemble.",
        context={"model_type": kind.value},
    )
