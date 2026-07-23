"""Motor de comisiones configurable por broker y por símbolo.

Nunca se hardcodea una tarifa: todo procede de :class:`CommissionSettings`,
con overrides por símbolo. Soporta esquemas por nocional (bps), por unidad y
fijo, distinguiendo maker/taker.
"""

from dataclasses import dataclass

from app.config.settings import CommissionSettings


@dataclass(frozen=True, slots=True)
class CommissionQuote:
    """Comisión calculada para un fill."""

    amount: float
    model: str
    liquidity: str

    def to_dict(self) -> dict[str, object]:
        """JSON-safe dict."""
        return {"amount": self.amount, "model": self.model, "liquidity": self.liquidity}


class CommissionEngine:
    """Compute execution commissions from a broker fee schedule.

    Args:
        settings: Fee schedule (defaults + per-symbol overrides).
    """

    def __init__(self, settings: CommissionSettings) -> None:
        self._settings = settings

    def _rate_bps(self, symbol: str, *, maker: bool) -> float:
        """Resolve the maker/taker rate in bps for a symbol."""
        key = "maker_bps" if maker else "taker_bps"
        override = self._settings.per_symbol.get(symbol.upper(), {})
        if key in override:
            return float(override[key])
        return self._settings.maker_bps if maker else self._settings.taker_bps

    def _param(self, symbol: str, name: str, default: float) -> float:
        """Resolve a per-symbol scalar parameter (fixed/per_unit/minimum)."""
        override = self._settings.per_symbol.get(symbol.upper(), {})
        return float(override.get(name, default))

    def calculate(
        self, symbol: str, quantity: float, price: float, *, maker: bool = False
    ) -> CommissionQuote:
        """Compute the commission for a fill.

        Args:
            symbol: Símbolo operado.
            quantity: Cantidad ejecutada.
            price: Precio de ejecución.
            maker: ``True`` si aportó liquidez (maker), ``False`` si la tomó.

        Returns:
            Cuota de comisión con su modelo y tipo de liquidez.
        """
        quantity = abs(quantity)
        notional = quantity * price
        model = self._settings.model
        if model == "per_notional":
            amount = notional * self._rate_bps(symbol, maker=maker) / 10_000.0
        elif model == "per_unit":
            amount = quantity * self._param(symbol, "per_unit", self._settings.per_unit)
        elif model == "fixed":
            amount = self._param(symbol, "fixed", self._settings.fixed)
        elif model == "tiered":
            # Esquema por tramos sencillo: bps sobre nocional con mínimo.
            amount = notional * self._rate_bps(symbol, maker=maker) / 10_000.0
        else:
            amount = notional * self._rate_bps(symbol, maker=maker) / 10_000.0
        minimum = self._param(symbol, "minimum", self._settings.minimum)
        amount = max(amount, minimum) if quantity > 0 else 0.0
        return CommissionQuote(
            amount=round(amount, 8),
            model=model,
            liquidity="maker" if maker else "taker",
        )
