"""Modelos de Machine Learning intercambiables (Python puro + backends preparados).

Nativos (sin dependencias): regresión logística, árbol de decisión, Random
Forest y Extra Trees. Preparados (dependencia pesada, carga perezosa): XGBoost,
LightGBM, CatBoost y redes neuronales. Todos comparten la interfaz ``Model``.
"""

from app.ml.models.external import (
    CatBoostModel,
    LightGBMModel,
    NeuralNetModel,
    XGBoostModel,
)
from app.ml.models.factory import build_model
from app.ml.models.forest import ExtraTreesModel, RandomForestModel
from app.ml.models.logistic_regression import LogisticRegressionModel
from app.ml.models.preprocessing import StandardScaler
from app.ml.models.tree import DecisionTreeModel

__all__ = [
    "CatBoostModel",
    "DecisionTreeModel",
    "ExtraTreesModel",
    "LightGBMModel",
    "LogisticRegressionModel",
    "NeuralNetModel",
    "RandomForestModel",
    "StandardScaler",
    "XGBoostModel",
    "build_model",
]
