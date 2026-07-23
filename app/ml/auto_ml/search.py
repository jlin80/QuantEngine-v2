"""Espacio de búsqueda del AutoML: modelos × hiperparámetros (Fase 7).

Genera variantes acotadas por tipo de modelo. Se mantiene pequeño a propósito:
los datasets del motor son pequeños y el AutoML corre de noche; la meta es
comparar modelos de forma honesta, no barrer un espacio gigante.
"""

from app.config.settings import MLModelSettings

_GRID: dict[str, list[dict[str, float | int]]] = {
    "logistic_regression": [{"l2": 0.0}, {"l2": 0.001}, {"l2": 0.01}],
    "decision_tree": [{"max_depth": 4}, {"max_depth": 6}],
    "random_forest": [
        {"n_estimators": 40, "max_depth": 5},
        {"n_estimators": 80, "max_depth": 6},
    ],
    "extra_trees": [{"n_estimators": 60, "max_depth": 6}],
    "xgboost": [{}],
    "lightgbm": [{}],
    "catboost": [{}],
    "neural_net": [{}],
}


def candidate_specs(
    model_types: list[str], base: MLModelSettings
) -> list[tuple[str, MLModelSettings]]:
    """Expand a list of model types into concrete (type, settings) candidates.

    Args:
        model_types: Tipos de modelo a probar.
        base: Hiperparámetros base sobre los que se aplican las variantes.

    Returns:
        Pares ``(tipo, settings)`` listos para construir y entrenar.
    """
    specs: list[tuple[str, MLModelSettings]] = []
    for model_type in model_types:
        for overrides in _GRID.get(model_type, [{}]):
            specs.append((model_type, base.model_copy(update=dict(overrides))))
    return specs
