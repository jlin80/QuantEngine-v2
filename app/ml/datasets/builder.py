"""Construcción de datasets desde el historial de operaciones del motor.

El ML se entrena con lo que el propio Quant Engine genera: operaciones cerradas
(``TradeRecord``). El builder ordena por tiempo, aplica el ``FeatureEngineer`` y
etiqueta cada operación (buena/mala). No inventa datos ni usa el precio directo.
"""

from collections.abc import Sequence

from app.execution.models.trades import TradeRecord
from app.ml.datasets.dataset import Dataset
from app.ml.features.engineer import FEATURE_SCHEMA_VERSION, FeatureEngineer
from app.utils.time import utc_now


class DatasetBuilder:
    """Turn journalled trades into a supervised :class:`Dataset`.

    Args:
        engineer: Constructor de features (esquema fijo). Uno nuevo por defecto.
    """

    def __init__(self, engineer: FeatureEngineer | None = None) -> None:
        self._engineer = engineer or FeatureEngineer()

    @property
    def engineer(self) -> FeatureEngineer:
        """The feature engineer used to build vectors."""
        return self._engineer

    def build(self, trades: Sequence[TradeRecord], *, label: str = "win") -> Dataset:
        """Build a dataset from closed trades (sorted by exit time).

        Args:
            trades: Operaciones cerradas (el journal es la fuente de verdad).
            label: Tipo de etiqueta (``win`` | ``rr_positive`` | ``not_stopped``).

        Returns:
            El dataset supervisado, temporalmente ordenado.
        """
        ordered = sorted(trades, key=lambda t: t.exit_time)
        x: list[list[float]] = []
        y: list[int] = []
        for trade in ordered:
            x.append(self._engineer.from_trade(trade))
            y.append(self._engineer.label(trade, kind=label))
        dataset = Dataset(
            feature_names=self._engineer.feature_names(),
            x=x,
            y=y,
            metadata={
                "id": f"trades:{len(x)}:{label}",
                "source": "trade_journal",
                "label": label,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "built_at": utc_now().isoformat(),
                "samples": len(x),
                "positives": sum(y),
            },
        )
        return dataset
