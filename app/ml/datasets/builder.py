"""Construcción de datasets desde el historial de operaciones del motor.

El ML se entrena con lo que el propio Quant Engine genera: operaciones cerradas
(``TradeRecord``). El builder ordena por tiempo, aplica el ``FeatureEngineer`` y
etiqueta cada operación (buena/mala). No inventa datos ni usa el precio directo.

Desde el saneamiento del training set (ver :mod:`app.ml.datasets.eras`) el
builder además **filtra y pondera por era de ejecución**, para que el modelo no
aprenda de bugs ya arreglados, y admite dos etiquetas distintas:

- ``win`` / ``rr_positive`` / ``not_stopped`` — **calidad de ejecución**: qué
  hizo el motor con la señal, costes y salidas incluidos.
- ``signal_quality`` — **calidad de la señal en sí**: sólo cuentan las
  operaciones cuyo cierre resolvió la tesis (objetivo/stop/trailing/BE). Las que
  cerró la ejecución (régimen, tiempo, kill switch, manual) se descartan, porque
  nunca llegaron a poner a prueba la señal.
"""

from collections.abc import Sequence

from app.config.settings import MLDataQualitySettings
from app.execution.models.trades import TradeRecord
from app.ml.datasets.dataset import Dataset
from app.ml.datasets.eras import era_of, era_summary, is_signal_verdict, weight_of
from app.ml.features.engineer import FEATURE_SCHEMA_VERSION, FeatureEngineer
from app.utils.time import utc_now

SIGNAL_LABEL = "signal_quality"
"""Etiqueta que juzga la señal, no la ejecución."""


class DatasetBuilder:
    """Turn journalled trades into a supervised :class:`Dataset`.

    Args:
        engineer: Constructor de features (esquema fijo). Uno nuevo por defecto.
        data_quality: Saneamiento por era. ``None`` usa los valores por defecto
            (segmentación activada).
    """

    def __init__(
        self,
        engineer: FeatureEngineer | None = None,
        data_quality: MLDataQualitySettings | None = None,
    ) -> None:
        self._engineer = engineer or FeatureEngineer()
        self._quality = data_quality or MLDataQualitySettings()

    @property
    def engineer(self) -> FeatureEngineer:
        """The feature engineer used to build vectors."""
        return self._engineer

    @property
    def data_quality(self) -> MLDataQualitySettings:
        """Training-set sanitation settings in force."""
        return self._quality

    def build(self, trades: Sequence[TradeRecord], *, label: str = "win") -> Dataset:
        """Build a dataset from closed trades (sorted by exit time).

        Args:
            trades: Operaciones cerradas (el journal es la fuente de verdad).
            label: ``win`` | ``rr_positive`` | ``not_stopped`` (calidad de
                ejecución) o ``signal_quality`` (calidad de la señal).

        Returns:
            El dataset supervisado, temporalmente ordenado, con la procedencia
            por era en sus metadatos.
        """
        ordered = sorted(trades, key=lambda t: t.exit_time)
        x: list[list[float]] = []
        y: list[int] = []
        weights: list[float] = []
        eras: list[str] = []
        dropped_by_era = 0
        dropped_not_signal_verdict = 0

        for trade in ordered:
            weight = weight_of(trade, self._quality)
            if weight <= 0.0:
                # Una era con el stop mal calculado no da una muestra floja: da
                # una medición inválida. Ponderarla a la baja seguiría metiendo
                # ruido correlacionado, así que se excluye.
                dropped_by_era += 1
                continue
            if label == SIGNAL_LABEL and not is_signal_verdict(trade, self._quality):
                dropped_not_signal_verdict += 1
                continue
            x.append(self._engineer.from_trade(trade))
            y.append(self._label(trade, label))
            weights.append(weight)
            eras.append(era_of(trade, self._quality))

        return Dataset(
            feature_names=self._engineer.feature_names(),
            x=x,
            y=y,
            metadata={
                "id": f"trades:{len(x)}:{label}",
                "source": "trade_journal",
                "label": label,
                # Qué mide esta etiqueta. Confundir las dos es exactamente el
                # fallo que el saneamiento evita, así que va explícito.
                "label_measures": "signal" if label == SIGNAL_LABEL else "execution",
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "built_at": utc_now().isoformat(),
                "samples": len(x),
                "positives": sum(y),
                # Procedencia auditable: qué entró, qué no, y por qué.
                "sample_weights": weights,
                "sample_eras": eras,
                "dropped_by_era": dropped_by_era,
                "dropped_not_signal_verdict": dropped_not_signal_verdict,
                "era_breakdown": era_summary(ordered, self._quality),
            },
        )

    def _label(self, trade: TradeRecord, label: str) -> int:
        """Binary label for a trade under the requested labelling scheme."""
        if label == SIGNAL_LABEL:
            # Llegados aquí la salida ya resolvió la tesis: el signo de R es el
            # veredicto sobre la señal, sin que lo enturbie una salida forzada.
            return 1 if trade.r_multiple >= 0 else 0
        return self._engineer.label(trade, kind=label)
