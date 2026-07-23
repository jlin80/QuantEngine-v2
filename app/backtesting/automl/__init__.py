"""Infraestructura AutoML del laboratorio (Fase 6) — preparada, sin entrenar."""

from app.backtesting.automl.registry import (
    AutoMLNotEnabledError,
    AutoMLRegistry,
    ModelSpec,
    ModelType,
)

__all__ = ["AutoMLNotEnabledError", "AutoMLRegistry", "ModelSpec", "ModelType"]
