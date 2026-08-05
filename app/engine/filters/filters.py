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
from app.engine.position_quality import PositionQualityEngine
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
        groups: Grupos de símbolos correlacionados, declarados a mano.
        window_minutes: Ventana de exclusión.
        recent_decisions: Lector de decisiones recientes.
        measured: Lector de correlación **medida** (Bloque 5). Los grupos
            manuales envejecen: dos símbolos pueden dejar de moverse juntos, o
            empezar a hacerlo, sin que nadie toque el fichero. Esto añade lo que
            el mercado está haciendo de verdad **sin sustituir** a la regla
            manual — se unen, no compiten. ``None`` deja el filtro exactamente
            como estaba antes de este bloque.
    """

    def __init__(
        self,
        groups: list[list[str]],
        window_minutes: float,
        recent_decisions: Callable[[], list[Decision]],
        measured: Callable[[str], set[str]] | None = None,
    ) -> None:
        super().__init__("correlation")
        self._groups = [{symbol.upper() for symbol in group} for group in groups]
        self._window = window_minutes * 60.0
        self._recent = recent_decisions
        self._measured = measured

    def _group_of(self, symbol: str) -> set[str] | None:
        """Correlation group of a symbol: manual groups plus measured ones."""
        found: set[str] = set()
        for group in self._groups:
            if symbol.upper() in group:
                found |= group
        if self._measured is not None:
            found |= {other.upper() for other in self._measured(symbol)}
        return found or None

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


class MicrostructureFilter(SignalFilter):
    """Veta cuando el libro está hostil para ejecutar (Bloque 3).

    **Fail-open a propósito.** Si no hay medición de microestructura —porque el
    proveedor no publica libro, que es el caso de MT5 en la demo— el filtro
    deja pasar. Un filtro que bloquea por ausencia de datos apagaría el motor
    entero con el bróker actual, y lo haría de la forma más difícil de
    diagnosticar: sin errores, sólo sin operaciones.

    Args:
        max_pressure: Presión de ejecución por encima de la cual se veta.
        pressure_reader: Lector de la presión actual del símbolo (``None`` = no
            observable).
    """

    def __init__(self, max_pressure: float, pressure_reader: Callable[[str], float | None]) -> None:
        super().__init__("microstructure")
        self._max = max_pressure
        self._reader = pressure_reader

    def check(self, context: MarketContext, consensus: ConsensusResult) -> FilterResult:
        """Pass unless the book is measurably hostile right now."""
        pressure = self._reader(context.symbol)
        if pressure is None or pressure <= self._max:
            return FilterResult(name=self.name, passed=True)
        return FilterResult(
            name=self.name,
            passed=False,
            reason=f"presión de ejecución {pressure:.2f} > máximo {self._max}",
        )


class PositionQualityFilter(SignalFilter):
    """Veta cuando la posición que saldría de la decisión es mala (Bloque 7).

    Vive como filtro —y no dentro del score— por dos razones. La primera es
    conceptual: el score mide la oportunidad, esto mide la posición, y una
    señal excelente puede producir una posición pésima. La segunda es práctica:
    como filtro, el veto **aparece en la explicación de la decisión** con su
    motivo, que es la única forma de auditar después por qué no se operó.

    Args:
        engine: Motor de calidad.
        inputs: Lector de las magnitudes que el filtro no ve por sí mismo
            (coste esperado, R esperada, riesgo propuesto y su techo). Devuelve
            un dict con lo que sepa; lo que falte queda como no observable.
    """

    def __init__(
        self,
        engine: PositionQualityEngine,
        inputs: Callable[[str], dict[str, float | None]] | None = None,
    ) -> None:
        super().__init__("position_quality")
        self._engine = engine
        self._inputs = inputs

    def check(self, context: MarketContext, consensus: ConsensusResult) -> FilterResult:
        """Pass unless the resulting position would be of poor quality."""
        extra = self._inputs(context.symbol) if self._inputs is not None else {}
        assessment = self._engine.assess(
            context,
            consensus,
            expected_cost_bps=extra.get("expected_cost_bps"),
            expected_r=extra.get("expected_r"),
            risk_pct=extra.get("risk_pct"),
            max_risk_pct=extra.get("max_risk_pct"),
        )
        if not assessment.blocked:
            return FilterResult(name=self.name, passed=True)
        return FilterResult(
            name=self.name,
            passed=False,
            reason="; ".join(assessment.reasons),
        )


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
    microstructure: tuple[float, Callable[[str], float | None]] | None = None,
    measured_correlation: Callable[[str], set[str]] | None = None,
    position_quality: (
        tuple[PositionQualityEngine, Callable[[str], dict[str, float | None]] | None] | None
    ) = None,
) -> FilterChain:
    """Build the chain from configuration.

    Args:
        settings: Filtros habilitados y parámetros.
        drawdown_reader: Lector del drawdown diario actual.
        recent_decisions: Lector de decisiones recientes (correlación).
        microstructure: ``(umbral, lector de presión)`` del Bloque 3. ``None``
            deja el filtro fuera de la cadena — no es lo mismo que dejarlo
            dentro y que siempre pase: fuera, ni siquiera aparece en la
            explicación de la decisión.
        measured_correlation: Lector de correlación medida (Bloque 5). Se une a
            los grupos manuales; ``None`` deja el filtro como antes.
        position_quality: ``(motor, lector de entradas)`` del Bloque 7. ``None``
            deja el veto de calidad fuera de la cadena.

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
            measured_correlation,
        ),
    }
    if microstructure is not None:
        registry["microstructure"] = lambda: MicrostructureFilter(*microstructure)
    if position_quality is not None:
        registry["position_quality"] = lambda: PositionQualityFilter(*position_quality)
    filters = [registry[name]() for name in settings.enabled if name in registry]
    return FilterChain(filters)
