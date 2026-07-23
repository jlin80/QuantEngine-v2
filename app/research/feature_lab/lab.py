"""Feature Lab: catálogo de features candidatas y su validación (Fase 10)."""

import math
from collections.abc import Callable, Sequence

from app.config.settings import FeatureLabSettings
from app.market.models import Candle
from app.research import series
from app.research import stats as st
from app.research.models import FeatureReport

FeatureFn = Callable[[Sequence[Candle]], list[float]]

_DEFAULT_FEATURES: dict[str, FeatureFn] = {
    "atr_slope": series.atr_slope,
    "vwap_distance": series.vwap_distance,
    "delta_momentum": series.delta_momentum,
    "liquidity_score": series.liquidity_score,
    "trend_score": series.trend_score,
    "book_pressure_score": series.book_pressure_score,
    "microprice": series.microprice,
    "spread_velocity": series.spread_velocity,
    "volatility_expansion": series.volatility_expansion,
}


class FeatureLab:
    """Registry and validator of candidate features.

    Args:
        settings: Configuración del laboratorio de features (horizonte de
            validación y umbrales de cobertura/varianza/IC).
    """

    def __init__(self, settings: FeatureLabSettings) -> None:
        self._settings = settings
        self._features: dict[str, FeatureFn] = dict(_DEFAULT_FEATURES)

    def register(self, name: str, fn: FeatureFn) -> None:
        """Register a custom candidate feature (``generate_feature``)."""
        self._features[name] = fn

    def catalog(self) -> list[str]:
        """Names of every registered candidate feature."""
        return list(self._features)

    def validate(self, name: str, candles: Sequence[Candle]) -> FeatureReport:
        """Validate a single feature against forward returns.

        Args:
            name: Feature registrada.
            candles: Serie histórica de velas.

        Returns:
            El informe de validación (válida sólo si supera los tres umbrales).

        Raises:
            KeyError: Si la feature no está registrada.
        """
        fn = self._features[name]
        values = fn(candles)
        prices = [c.close for c in candles]
        finite = [v for v in values if math.isfinite(v)]
        coverage = len(finite) / len(values) if values else 0.0
        variance = st.variance(finite)
        ic, samples = st.information_coefficient(values, prices, self._settings.forward_horizon)

        reasons: list[str] = []
        if coverage < self._settings.min_coverage:
            reasons.append(f"cobertura {coverage:.2f} < {self._settings.min_coverage:.2f}")
        if variance < self._settings.min_variance:
            reasons.append("varianza casi nula (feature constante)")
        if abs(ic) < self._settings.min_abs_ic:
            reasons.append(f"|IC| {abs(ic):.3f} < {self._settings.min_abs_ic:.3f}")
        valid = not reasons
        if valid:
            reasons.append(f"válida: |IC|={abs(ic):.3f}, cobertura={coverage:.2f}")
        return FeatureReport(
            name=name,
            valid=valid,
            coverage=coverage,
            variance=variance,
            ic=ic,
            samples=samples,
            reasons=tuple(reasons),
        )

    def validate_all(self, candles: Sequence[Candle]) -> list[FeatureReport]:
        """Validate every registered feature, ranked by absolute IC."""
        reports = [self.validate(name, candles) for name in self._features]
        return sorted(reports, key=lambda r: abs(r.ic), reverse=True)
