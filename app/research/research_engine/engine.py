"""Orquestación de alto nivel del laboratorio (Fase 10)."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from app.backtesting.models import BacktestConfig
from app.market.models import Candle
from app.research.models import FactorReport, ShadowComparison, StrategyGenome

if TYPE_CHECKING:
    from app.research.api import ResearchLab


class ResearchEngine:
    """Coordinate the laboratory's autonomous research cycles.

    Args:
        lab: La fachada del Research Lab con todos los motores cableados.
    """

    def __init__(self, lab: "ResearchLab") -> None:
        self._lab = lab

    @property
    def lab(self) -> "ResearchLab":
        """The underlying Research Lab facade."""
        return self._lab

    async def run_generation_cycle(
        self,
        symbol: str,
        timeframe: str,
        candles: Sequence[Candle],
        config: BacktestConfig,
        *,
        count: int | None = None,
    ) -> dict[str, Any]:
        """Generate → validate (cluster) → rank → record a batch of strategies."""
        return await self._lab.run_generation_cycle(symbol, timeframe, candles, config, count=count)

    def research_factors(self, candles: Sequence[Candle]) -> list[FactorReport]:
        """Run the factor research sweep."""
        return self._lab.research_factors(candles)

    def compare_shadow(
        self,
        official: StrategyGenome,
        challenger: StrategyGenome,
        candles: Sequence[Candle],
        config: BacktestConfig,
    ) -> ShadowComparison:
        """Run a Shadow Mode comparison of a challenger against the official."""
        return self._lab.compare_shadow(official, challenger, candles, config)
