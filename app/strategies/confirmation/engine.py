"""Motor de confirmaciones: chequeos direccionales bajo demanda.

Cada estrategia declara qué confirmaciones quiere (parámetro
``confirmations``); el motor las evalúa con el Feature Store y el contexto.
Las que fallan se reportan como "Falta confirmación de X" — nunca se
descartan en silencio (explicabilidad).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.analytics.indicators import OrderFlowSnapshot, VolumeProfile
from app.engine.interfaces.strategy import AnalysisContext
from app.engine.models import Direction, Regime, VolatilityState


@dataclass(frozen=True, kw_only=True, slots=True)
class ConfirmationResult:
    """Resultado de una confirmación solicitada."""

    name: str
    passed: bool
    detail: str


class ConfirmationEngine:
    """Evaluates requested confirmations against live features/context."""

    SUPPORTED: tuple[str, ...] = (
        "delta",
        "cvd",
        "volume",
        "spread",
        "volatility",
        "session",
        "regime",
        "book_imbalance",
        "book_pressure",
        "volume_profile",
    )

    @classmethod
    async def evaluate(
        cls, ctx: AnalysisContext, names: Sequence[str], direction: Direction
    ) -> list[ConfirmationResult]:
        """Run every requested confirmation (las desconocidas fallan con nota).

        Args:
            ctx: Contexto de análisis de la estrategia.
            names: Confirmaciones solicitadas.
            direction: Dirección de la oportunidad evaluada.

        Returns:
            Un resultado por confirmación solicitada, en el mismo orden.
        """
        results: list[ConfirmationResult] = []
        sign = 1.0 if direction is Direction.LONG else -1.0
        for name in names:
            if name == "delta":
                results.append(await cls._delta(ctx, sign))
            elif name == "cvd":
                results.append(await cls._cvd(ctx, sign))
            elif name == "volume":
                results.append(await cls._volume(ctx))
            elif name == "spread":
                results.append(cls._spread(ctx))
            elif name == "volatility":
                results.append(cls._volatility(ctx))
            elif name == "session":
                results.append(cls._session(ctx))
            elif name == "regime":
                results.append(cls._regime(ctx))
            elif name == "book_imbalance":
                results.append(await cls._book(ctx, sign, "imbalance"))
            elif name == "book_pressure":
                results.append(await cls._book(ctx, sign, "book_pressure"))
            elif name == "volume_profile":
                results.append(await cls._volume_profile(ctx))
            else:
                results.append(
                    ConfirmationResult(name=name, passed=False, detail="confirmación desconocida")
                )
        return results

    @staticmethod
    async def _delta(ctx: AnalysisContext, sign: float) -> ConfirmationResult:
        """Delta agresor alineado con la dirección."""
        delta = await ctx.features.get("delta", ctx.symbol)
        if delta is None:
            return ConfirmationResult(name="delta", passed=False, detail="sin trades recientes")
        passed = delta * sign > 0
        return ConfirmationResult(
            name="delta",
            passed=passed,
            detail=f"delta {'positivo' if delta > 0 else 'negativo'} ({delta:+.2f})",
        )

    @staticmethod
    async def _cvd(ctx: AnalysisContext, sign: float) -> ConfirmationResult:
        """Pendiente del CVD alineada con la dirección."""
        snapshot = await ctx.features.get_object("orderflow", ctx.symbol)
        if not isinstance(snapshot, OrderFlowSnapshot):
            return ConfirmationResult(name="cvd", passed=False, detail="sin order flow")
        passed = snapshot.cvd_slope * sign > 0
        trendword = "creciente" if snapshot.cvd_slope > 0 else "decreciente"
        return ConfirmationResult(
            name="cvd", passed=passed, detail=f"CVD {trendword} ({snapshot.cvd_slope:+.2f})"
        )

    @staticmethod
    async def _volume(ctx: AnalysisContext) -> ConfirmationResult:
        """Volumen de la última vela por encima de la media."""
        ratio = await ctx.features.get("volume_ratio", ctx.symbol)
        if ratio is None:
            return ConfirmationResult(name="volume", passed=False, detail="sin datos de volumen")
        return ConfirmationResult(
            name="volume", passed=ratio >= 1.0, detail=f"volumen {ratio:.2f}x la media"
        )

    @staticmethod
    def _spread(ctx: AnalysisContext) -> ConfirmationResult:
        """Spread no elevado."""
        if ctx.context.spread_elevated:
            bps = ctx.context.spread_bps
            detail = f"spread elevado ({bps:.2f} bps)" if bps is not None else "spread elevado"
            return ConfirmationResult(name="spread", passed=False, detail=detail)
        return ConfirmationResult(name="spread", passed=True, detail="spread bajo")

    @staticmethod
    def _volatility(ctx: AnalysisContext) -> ConfirmationResult:
        """Volatilidad suficiente para operar (no LOW)."""
        low = ctx.context.volatility is VolatilityState.LOW
        return ConfirmationResult(
            name="volatility",
            passed=not low,
            detail=f"volatilidad {ctx.context.volatility.value}",
        )

    @staticmethod
    def _session(ctx: AnalysisContext) -> ConfirmationResult:
        """Alguna sesión de mercado activa."""
        active = ", ".join(ctx.context.sessions)
        return ConfirmationResult(
            name="session",
            passed=bool(ctx.context.sessions),
            detail=f"sesiones: {active or 'ninguna'}",
        )

    @staticmethod
    def _regime(ctx: AnalysisContext) -> ConfirmationResult:
        """Régimen identificado (no UNKNOWN)."""
        regime = ctx.context.regime.primary if ctx.context.regime else Regime.UNKNOWN
        return ConfirmationResult(
            name="regime",
            passed=regime is not Regime.UNKNOWN,
            detail=f"régimen {regime.value}",
        )

    @staticmethod
    async def _book(ctx: AnalysisContext, sign: float, feature: str) -> ConfirmationResult:
        """Imbalance/presión del libro alineado con la dirección (umbral 0.1)."""
        value = await ctx.features.get(feature, ctx.symbol)
        if value is None:
            return ConfirmationResult(name=feature, passed=False, detail="sin libro de órdenes")
        return ConfirmationResult(
            name=feature, passed=value * sign >= 0.1, detail=f"{feature} {value:+.2f}"
        )

    @staticmethod
    async def _volume_profile(ctx: AnalysisContext) -> ConfirmationResult:
        """Precio fuera del value area (en zona de interés del perfil)."""
        profile = await ctx.features.get_object("volume_profile", ctx.symbol)
        price = ctx.context.last_price
        if not isinstance(profile, VolumeProfile) or price is None:
            return ConfirmationResult(
                name="volume_profile", passed=False, detail="sin perfil de volumen"
            )
        position = profile.position(price)
        return ConfirmationResult(
            name="volume_profile",
            passed=position != "inside_value",
            detail=f"precio {position} (POC {profile.poc:.2f})",
        )
