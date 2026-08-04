"""Construcción de datasets desde el historial de operaciones del motor.

El ML se entrena con lo que el propio Quant Engine genera: operaciones cerradas
(``TradeRecord``). El builder ordena por tiempo, aplica el ``FeatureEngineer`` y
etiqueta cada operación (buena/mala). No inventa datos ni usa el precio directo.

Desde el saneamiento del training set (ver :mod:`app.ml.datasets.eras`) el
builder además **filtra y pondera por era de ejecución**, para que el modelo no
aprenda de bugs ya arreglados, y admite dos etiquetas distintas:

- ``win`` / ``rr_positive`` / ``not_stopped`` — **calidad de ejecución**: qué
  hizo el motor con la señal, costes y salidas incluidos.
- ``signal_quality`` — **calidad de la señal en sí**.

Desde el Bloque 8, ``signal_quality`` se resuelve con el **join real** contra el
evaluador continuo (:mod:`app.ml.datasets.join`): para cada operación, el
resultado virtual de las señales que la originaron, medido sobre precio
posterior a la señal e independiente de lo que la ejecución hiciera después. La
aproximación anterior —filtrar por motivo de salida y quedarse con las
operaciones cuyo cierre resolvió la tesis— sobrevive como **fallback** para el
historial sin ``signal_ids``, que es todo el journal anterior al bloque.

La diferencia importa: la aproximación no podía ver las señales que la ejecución
cortó por régimen o por tiempo, que son precisamente las que había que separar.
"""

from collections.abc import Mapping, Sequence

from app.config.settings import MLDataQualitySettings
from app.execution.models.trades import TradeRecord
from app.ml.datasets.dataset import Dataset
from app.ml.datasets.eras import era_of, era_summary, is_signal_verdict, weight_of
from app.ml.datasets.join import JoinedTrade, SignalOutcome, join_trades_with_outcomes
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

    def build(
        self,
        trades: Sequence[TradeRecord],
        *,
        label: str = "win",
        outcomes: Mapping[str, SignalOutcome] | None = None,
    ) -> Dataset:
        """Build a dataset from closed trades (sorted by exit time).

        Args:
            trades: Operaciones cerradas (el journal es la fuente de verdad).
            label: ``win`` | ``rr_positive`` | ``not_stopped`` (calidad de
                ejecución) o ``signal_quality`` (calidad de la señal).
            outcomes: Índice ``signal_id`` → resultado virtual del evaluador
                continuo. Con él, ``signal_quality`` sale del join real; sin él
                (o para las filas sin match) se usa la aproximación por motivo
                de salida.

        Returns:
            El dataset supervisado, temporalmente ordenado, con la procedencia
            por era y por join en sus metadatos.
        """
        ordered = sorted(trades, key=lambda t: t.exit_time)
        joined = join_trades_with_outcomes(ordered, outcomes or {})
        x: list[list[float]] = []
        y: list[int] = []
        weights: list[float] = []
        eras: list[str] = []
        dropped_by_era = 0
        dropped_not_signal_verdict = 0
        labelled_from_join = 0

        for row in joined.rows:
            trade = row.trade
            weight = weight_of(trade, self._quality)
            if weight <= 0.0:
                # Una era con el stop mal calculado no da una muestra floja: da
                # una medición inválida. Ponderarla a la baja seguiría metiendo
                # ruido correlacionado, así que se excluye. El saneamiento por
                # era se aplica DESPUÉS del join, no antes: el join no cambia
                # qué operaciones son medibles, sólo cómo se etiquetan.
                dropped_by_era += 1
                continue
            verdict = self._signal_verdict(row)
            if label == SIGNAL_LABEL:
                if verdict is None:
                    dropped_not_signal_verdict += 1
                    continue
                if row.matched:
                    labelled_from_join += 1
            x.append(self._engineer.from_trade(trade))
            y.append(self._label(trade, label, verdict=verdict))
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
                # Qué se pudo unir con el evaluador continuo y qué no. Sin este
                # desglose el dataset no dice si sus etiquetas de señal vienen
                # de evidencia directa o de la aproximación heredada.
                "join_breakdown": joined.breakdown(),
                "labelled_from_join": labelled_from_join,
            },
        )

    def _signal_verdict(self, row: JoinedTrade) -> bool | None:
        """Veredicto sobre la señal de una operación (``None`` = no medible).

        Dos fuentes, en orden de calidad:

        1. **Join real** — el evaluador resolvió la señal contra precio
           posterior. Vale siempre, incluso si la ejecución cortó por régimen o
           por tiempo: ese es justo el caso que la aproximación no veía.
        2. **Aproximación por motivo de salida** — sólo si la operación no tiene
           resolución virtual. Exige que el cierre haya resuelto la tesis.

        Args:
            row: Fila del join.

        Returns:
            Si la señal tenía edge, o ``None`` si no se puede juzgar.
        """
        if row.matched:
            return row.signal_had_edge
        if not is_signal_verdict(row.trade, self._quality):
            return None
        return row.trade.r_multiple >= 0

    def _label(self, trade: TradeRecord, label: str, *, verdict: bool | None) -> int:
        """Binary label for a trade under the requested labelling scheme."""
        if label == SIGNAL_LABEL:
            return 1 if verdict else 0
        return self._engineer.label(trade, kind=label)
