"""Sesión de Shadow Mode en paralelo (Fase 10).

Alimenta el mismo flujo de velas a la vigente y a la challenger, cuenta las
señales que cada una genera (sin enviar órdenes) y, cuando se alcanza el período
configurable, delega en el :class:`ShadowComparator` para el veredicto. No toca
posiciones ni el Decision Engine: sólo observa y compara.
"""

from app.backtesting.decisions import DecisionSource
from app.backtesting.models import BacktestConfig
from app.config.settings import ShadowModeSettings
from app.market.models import Candle
from app.research.models import ShadowComparison
from app.research.shadow_mode.comparator import ShadowComparator


class ShadowSession:
    """Parallel, order-free shadow run accumulating a shared candle stream.

    Args:
        comparator: Comparador estadístico.
        official: Fuente de decisiones vigente.
        challenger: Fuente de decisiones experimental.
        config: Configuración base del backtest (símbolo/timeframe).
        settings: Umbrales del Shadow Mode.
        official_name: Nombre de la vigente.
        challenger_name: Nombre de la challenger.
    """

    def __init__(
        self,
        comparator: ShadowComparator,
        official: DecisionSource,
        challenger: DecisionSource,
        config: BacktestConfig,
        settings: ShadowModeSettings,
        *,
        official_name: str = "official",
        challenger_name: str = "challenger",
    ) -> None:
        self._comparator = comparator
        self._official = official
        self._challenger = challenger
        self._config = config
        self._settings = settings
        self._official_name = official_name
        self._challenger_name = challenger_name
        self._candles: list[Candle] = []
        self._official_signals = 0
        self._challenger_signals = 0
        official.reset()
        challenger.reset()

    def feed(self, candle: Candle) -> None:
        """Deliver one candle to both strategies and count their signals.

        La challenger recibe exactamente el mismo dato que la vigente. Ninguna
        decisión se ejecuta: sólo se cuentan las señales generadas.
        """
        self._candles.append(candle)
        index = len(self._candles) - 1
        symbol = self._config.symbol
        if self._official.decide(symbol, self._candles, index) is not None:
            self._official_signals += 1
        if self._challenger.decide(symbol, self._candles, index) is not None:
            self._challenger_signals += 1

    @property
    def bars(self) -> int:
        """Number of candles observed so far."""
        return len(self._candles)

    @property
    def signals(self) -> dict[str, int]:
        """Signal counts per strategy."""
        return {
            self._official_name: self._official_signals,
            self._challenger_name: self._challenger_signals,
        }

    def is_ready(self) -> bool:
        """Whether both strategies produced enough signals to conclude."""
        return min(self._official_signals, self._challenger_signals) >= self._settings.min_signals

    def conclude(self) -> ShadowComparison:
        """Produce the statistical comparison over the observed data."""
        return self._comparator.compare(
            self._official,
            self._challenger,
            self._candles,
            self._config,
            official_name=self._official_name,
            challenger_name=self._challenger_name,
        )
