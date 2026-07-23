"""Factor Lab: familias de factores y su ranking por IC (Fase 10)."""

from collections.abc import Callable, Sequence

from app.config.settings import FactorLabSettings
from app.market.models import Candle
from app.research import series
from app.research import stats as st
from app.research.models import FactorReport

FactorFn = Callable[[Sequence[Candle]], list[float]]

# name -> (familia, serie). Cubre las familias que pide la especificación.
_FACTORS: dict[str, tuple[str, FactorFn]] = {
    "trend_momentum": ("trend", series.trend_score),
    "trend_ema_slope": ("trend", series.ema_slope),
    "reversion_zscore": ("reversion", series.reversion_z),
    "reversion_rsi": ("reversion", series.rsi_bias),
    "liquidity_volume_z": ("liquidity", series.volume_zscore),
    "liquidity_score": ("liquidity", series.liquidity_score),
    "volatility_atr_pct": ("volatility", series.atr_pct),
    "volatility_expansion": ("volatility", series.volatility_expansion),
    "temporal_hour": ("temporal", series.hour_of_day),
    "volume_cvd": ("volume", series.cvd_factor),
    "volume_buy_ratio": ("volume", series.buy_ratio),
    "hybrid_trend_liquidity": ("hybrid", series.trend_x_liquidity),
    "hybrid_vwapdist_pressure": ("hybrid", series.vwapdist_x_pressure),
}


class FactorLab:
    """Automatic factor research and ranking.

    Args:
        settings: Configuración (horizonte de retorno, |IC| mínimo, top-k).
    """

    def __init__(self, settings: FactorLabSettings) -> None:
        self._settings = settings

    def families(self) -> list[str]:
        """The factor families under research."""
        return sorted({family for family, _ in _FACTORS.values()})

    def catalog(self) -> list[dict[str, str]]:
        """Every factor with its family (for the dashboard)."""
        return [{"name": name, "family": family} for name, (family, _) in _FACTORS.items()]

    def research(self, candles: Sequence[Candle]) -> list[FactorReport]:
        """Research every factor and rank it by absolute IC.

        Args:
            candles: Serie histórica de velas.

        Returns:
            Factores ordenados por |IC| descendente, con su rango asignado.
        """
        prices = [c.close for c in candles]
        horizon = self._settings.forward_horizon
        reports: list[FactorReport] = []
        for name, (family, fn) in _FACTORS.items():
            values = fn(candles)
            ic, samples = st.information_coefficient(values, prices, horizon)
            hit = st.hit_rate(values, prices, horizon)
            reports.append(
                FactorReport(name=name, family=family, ic=ic, hit_rate=hit, samples=samples)
            )
        reports.sort(key=lambda r: abs(r.ic), reverse=True)
        ranked = [
            FactorReport(
                name=r.name,
                family=r.family,
                ic=r.ic,
                hit_rate=r.hit_rate,
                samples=r.samples,
                rank=i + 1,
            )
            for i, r in enumerate(reports)
        ]
        return ranked

    def top(self, candles: Sequence[Candle]) -> list[FactorReport]:
        """Retained factors: top-k with absolute IC above the minimum."""
        ranked = self.research(candles)
        retained = [r for r in ranked if abs(r.ic) >= self._settings.min_abs_ic]
        return retained[: self._settings.top_k]
