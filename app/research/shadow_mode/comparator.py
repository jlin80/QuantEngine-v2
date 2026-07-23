"""Comparación estadística challenger vs official (Fase 10)."""

from collections.abc import Sequence

from app.backtesting import BacktestLab
from app.backtesting.decisions import DecisionSource
from app.backtesting.models import BacktestConfig
from app.config.settings import ShadowModeSettings
from app.market.models import Candle
from app.research import stats as st
from app.research.models import ShadowComparison


class ShadowComparator:
    """Run two decision sources over the same data and compare their edge.

    Args:
        lab: Laboratorio de backtesting (simula ambas estrategias).
        settings: Umbrales de significancia y efecto mínimo.
    """

    def __init__(self, lab: BacktestLab, settings: ShadowModeSettings) -> None:
        self._lab = lab
        self._settings = settings

    def compare(
        self,
        official: DecisionSource,
        challenger: DecisionSource,
        candles: Sequence[Candle],
        config: BacktestConfig,
        *,
        official_name: str = "official",
        challenger_name: str = "challenger",
    ) -> ShadowComparison:
        """Compare the challenger against the official over identical data.

        Args:
            official: Fuente de decisiones vigente.
            challenger: Fuente de decisiones experimental.
            candles: Serie histórica compartida (mismos datos para ambas).
            config: Configuración base del backtest.
            official_name: Nombre de la vigente.
            challenger_name: Nombre de la challenger.

        Returns:
            El informe comparativo con el veredicto estadístico.
        """
        official.reset()
        challenger.reset()
        off_result = self._lab.run_backtest(candles, official, config)
        chal_result = self._lab.run_backtest(candles, challenger, config)

        off_r = [trade.r_multiple for trade in off_result.trades]
        chal_r = [trade.r_multiple for trade in chal_result.trades]
        off_exp = st.mean(off_r)
        chal_exp = st.mean(chal_r)
        effect = chal_exp - off_exp
        t_stat, p_value = st.welch_t_test(chal_r, off_r)

        samples = min(len(off_r), len(chal_r))
        enough = samples >= self._settings.min_signals
        significant = enough and p_value <= self._settings.significance
        better = significant and effect >= self._settings.min_effect_r

        verdict = self._verdict(enough, significant, better, effect)
        return ShadowComparison(
            official=official_name,
            challenger=challenger_name,
            samples=samples,
            official_expectancy_r=off_exp,
            challenger_expectancy_r=chal_exp,
            effect_r=effect,
            t_stat=t_stat,
            p_value=p_value,
            significant=significant,
            better=better,
            verdict=verdict,
            metrics={
                "official_trades": float(len(off_r)),
                "challenger_trades": float(len(chal_r)),
                "official_return_pct": off_result.return_pct,
                "challenger_return_pct": chal_result.return_pct,
            },
        )

    def _verdict(self, enough: bool, significant: bool, better: bool, effect: float) -> str:
        """Human-readable conclusion of the comparison."""
        if not enough:
            return f"insuficiente: se requieren {self._settings.min_signals} señales por lado"
        if better:
            return f"la challenger supera a la vigente (+{effect:.3f} R, significativo)"
        if significant and effect < 0:
            return f"la vigente es mejor ({effect:.3f} R, significativo)"
        return "sin diferencia estadísticamente significativa"
