"""Filtros independientes que pueden vetar una decisión.

Cada filtro devuelve un :class:`FilterResult` con razón obligatoria cuando
bloquea — la explicabilidad de la decisión los incluye siempre.
"""

from collections.abc import Callable

from app.config.settings import QuantFiltersSettings
from app.engine.interfaces.filters import SignalFilter
from app.engine.models import (
    ConsensusResult,
    Decision,
    FilterResult,
    MarketContext,
    VolatilityState,
)
from app.utils.time import utc_now


class SessionFilter(SignalFilter):
    """Bloquea fuera de las sesiones permitidas.

    Args:
        allowed: Sesiones horarias permitidas (forex/XAUUSD, que sí cierra).
        always_open: Símbolos que cotizan 24/7 (cripto) — exentos del filtro.
    """

    def __init__(self, allowed: list[str], always_open: list[str] | None = None) -> None:
        super().__init__("session")
        self._allowed = {name.lower() for name in allowed}
        self._always_open = {symbol.upper() for symbol in (always_open or [])}

    def check(self, context: MarketContext, consensus: ConsensusResult) -> FilterResult:
        """Pass if any active session is allowed, or the symbol trades 24/7."""
        if context.symbol.upper() in self._always_open:
            return FilterResult(name=self.name, passed=True)
        active = {name.lower() for name in context.sessions}
        if active & self._allowed:
            return FilterResult(name=self.name, passed=True)
        return FilterResult(
            name=self.name,
            passed=False,
            reason=f"sesión activa {sorted(active) or ['ninguna']} fuera de "
            f"{sorted(self._allowed)}",
        )


class SpreadFilter(SignalFilter):
    """Bloquea con spread elevado (umbral del contexto)."""

    def __init__(self) -> None:
        super().__init__("spread")

    def check(self, context: MarketContext, consensus: ConsensusResult) -> FilterResult:
        """Pass unless the context marks the spread as elevated."""
        if not context.spread_elevated:
            return FilterResult(name=self.name, passed=True)
        return FilterResult(
            name=self.name,
            passed=False,
            reason=(
                f"spread elevado ({context.spread_bps:.2f} bps)"
                if context.spread_bps is not None
                else "spread elevado"
            ),
        )


class VolatilityFilter(SignalFilter):
    """Bloquea con volatilidad insuficiente (LOW): sin rango no hay scalp."""

    def __init__(self) -> None:
        super().__init__("volatility")

    def check(self, context: MarketContext, consensus: ConsensusResult) -> FilterResult:
        """Pass unless volatility is classified LOW."""
        if context.volatility is not VolatilityState.LOW:
            return FilterResult(name=self.name, passed=True)
        return FilterResult(
            name=self.name,
            passed=False,
            reason=(
                f"volatilidad insuficiente (ATR {context.atr_pct:.3f}%)"
                if context.atr_pct is not None
                else "volatilidad insuficiente"
            ),
        )


class LiquidityFilter(SignalFilter):
    """Bloquea sin volumen suficiente."""

    def __init__(self) -> None:
        super().__init__("liquidity")

    def check(self, context: MarketContext, consensus: ConsensusResult) -> FilterResult:
        """Pass while recent volume clears the configured minimum."""
        if context.volume_sufficient:
            return FilterResult(name=self.name, passed=True)
        return FilterResult(
            name=self.name,
            passed=False,
            reason=f"volumen reciente insuficiente ({context.volume_recent})",
        )


class NewsFilter(SignalFilter):
    """Bloquea dentro de ventanas de noticias configuradas."""

    def __init__(self) -> None:
        super().__init__("news")

    def check(self, context: MarketContext, consensus: ConsensusResult) -> FilterResult:
        """Pass outside news blackout windows."""
        if not context.news_blackout:
            return FilterResult(name=self.name, passed=True)
        return FilterResult(name=self.name, passed=False, reason="ventana de noticias activa")


class DrawdownFilter(SignalFilter):
    """Bloquea si el drawdown diario supera el máximo permitido.

    El valor lo publica el StateManager (en fases futuras lo alimentará la
    contabilidad real; hoy es 0 salvo que se fije manualmente).
    """

    def __init__(self, max_drawdown_pct: float, reader: Callable[[], float]) -> None:
        super().__init__("drawdown")
        self._max = max_drawdown_pct
        self._reader = reader

    def check(self, context: MarketContext, consensus: ConsensusResult) -> FilterResult:
        """Pass while daily drawdown stays under the limit."""
        current = self._reader()
        if current <= self._max:
            return FilterResult(name=self.name, passed=True)
        return FilterResult(
            name=self.name,
            passed=False,
            reason=f"drawdown diario {current:.2f}% > máximo {self._max:.2f}%",
        )


class CorrelationFilter(SignalFilter):
    """Bloquea si ya hay una decisión aceptada reciente en el mismo grupo.

    Args:
        groups: Grupos de símbolos correlacionados.
        window_minutes: Ventana de exclusión.
        recent_decisions: Lector de decisiones recientes.
    """

    def __init__(
        self,
        groups: list[list[str]],
        window_minutes: float,
        recent_decisions: Callable[[], list[Decision]],
    ) -> None:
        super().__init__("correlation")
        self._groups = [{symbol.upper() for symbol in group} for group in groups]
        self._window = window_minutes * 60.0
        self._recent = recent_decisions

    def _group_of(self, symbol: str) -> set[str] | None:
        """Correlation group containing a symbol."""
        for group in self._groups:
            if symbol.upper() in group:
                return group
        return None

    def check(self, context: MarketContext, consensus: ConsensusResult) -> FilterResult:
        """Pass unless a correlated symbol was recently accepted."""
        group = self._group_of(context.symbol)
        if group is None:
            return FilterResult(name=self.name, passed=True)
        now = utc_now()
        for decision in self._recent():
            if not decision.accepted or decision.symbol == context.symbol.upper():
                continue
            if decision.symbol not in group:
                continue
            age = (now - decision.timestamp).total_seconds()
            if age <= self._window:
                return FilterResult(
                    name=self.name,
                    passed=False,
                    reason=f"decisión reciente en símbolo correlacionado "
                    f"{decision.symbol} hace {age:.0f}s",
                )
        return FilterResult(name=self.name, passed=True)


class FilterChain:
    """Evaluates every enabled filter and collects the results.

    Args:
        filters: Filtros en orden de evaluación.
    """

    def __init__(self, filters: list[SignalFilter]) -> None:
        self._filters = filters

    @property
    def names(self) -> list[str]:
        """Enabled filter names."""
        return [f.name for f in self._filters]

    def evaluate(self, context: MarketContext, consensus: ConsensusResult) -> list[FilterResult]:
        """Run every filter (no se corta en el primero: explicabilidad)."""
        return [f.check(context, consensus) for f in self._filters]


def build_filter_chain(
    settings: QuantFiltersSettings,
    *,
    drawdown_reader: Callable[[], float],
    recent_decisions: Callable[[], list[Decision]],
) -> FilterChain:
    """Build the chain from configuration.

    Args:
        settings: Filtros habilitados y parámetros.
        drawdown_reader: Lector del drawdown diario actual.
        recent_decisions: Lector de decisiones recientes (correlación).

    Returns:
        Cadena con los filtros habilitados, en orden de configuración.
    """
    registry: dict[str, Callable[[], SignalFilter]] = {
        "session": lambda: SessionFilter(settings.allowed_sessions, settings.always_open_symbols),
        "spread": SpreadFilter,
        "volatility": VolatilityFilter,
        "liquidity": LiquidityFilter,
        "news": NewsFilter,
        "drawdown": lambda: DrawdownFilter(settings.max_drawdown_pct, drawdown_reader),
        "correlation": lambda: CorrelationFilter(
            settings.correlation_groups,
            settings.correlation_window_minutes,
            recent_decisions,
        ),
    }
    filters = [registry[name]() for name in settings.enabled if name in registry]
    return FilterChain(filters)
