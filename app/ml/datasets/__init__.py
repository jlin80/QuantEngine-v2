"""Datasets del ML: construcción desde el historial y cortes temporales.

Incluye el saneamiento por era de ejecución (:mod:`app.ml.datasets.eras`), que
evita que el modelo aprenda de bugs de ejecución ya arreglados, y el join fila
a fila con el evaluador continuo (:mod:`app.ml.datasets.join`), que separa la
calidad de la señal de la calidad de la ejecución.
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
from app.ml.datasets.join import (
    MATCHED,
    UNMATCHED_LEGACY,
    UNMATCHED_UNRESOLVED,
    JoinedTrade,
    JoinResult,
    SignalOutcome,
    join_trades_with_outcomes,
)

__all__ = [
    "CLEAN_ERA",
    "MATCHED",
    "SIGNAL_LABEL",
    "UNMATCHED_LEGACY",
    "UNMATCHED_UNRESOLVED",
    "Dataset",
    "DatasetBuilder",
    "JoinResult",
    "JoinedTrade",
    "SignalOutcome",
    "era_of",
    "era_summary",
    "is_signal_verdict",
    "join_trades_with_outcomes",
    "weight_of",
]
