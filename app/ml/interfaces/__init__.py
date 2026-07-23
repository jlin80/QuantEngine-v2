"""Interfaces de la capa de Machine Learning (contratos, sin implementación)."""

from app.ml.interfaces.model import Matrix, Model, ModelType, Vector, as_labels, as_matrix

__all__ = ["Matrix", "Model", "ModelType", "Vector", "as_labels", "as_matrix"]
