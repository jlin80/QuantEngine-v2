"""Datasets del ML: construcción desde el historial y cortes temporales.

Incluye el saneamiento por era de ejecución (:mod:`app.ml.datasets.eras`), que
evita que el modelo aprenda de bugs de ejecución ya arreglados.
"""

from app.ml.datasets.builder import SIGNAL_LABEL, DatasetBuilder
from app.ml.datasets.dataset import Dataset
from app.ml.datasets.eras import (
    CLEAN_ERA,
    era_of,
    era_summary,
    is_signal_verdict,
    weight_of,
)

__all__ = [
    "CLEAN_ERA",
    "SIGNAL_LABEL",
    "Dataset",
    "DatasetBuilder",
    "era_of",
    "era_summary",
    "is_signal_verdict",
    "weight_of",
]
