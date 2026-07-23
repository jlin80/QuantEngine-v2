"""Position sizing: cuánta cantidad operar según el método configurado.

Métodos soportados: monto fijo, porcentaje del capital, ATR, riesgo fijo,
riesgo dinámico (ajustado por confianza) y Kelly parcial. Todos respetan un
tope de exposición por operación (``max_position_pct``) y una cantidad mínima.
"""

from dataclasses import dataclass

from app.config.settings import SizingSettings


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Cantidad calculada y su justificación."""

    quantity: float
    method: str
    risk_amount: float
    stop_distance: float
    notional: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        """JSON-safe dict."""
        return {
            "quantity": self.quantity,
            "method": self.method,
            "risk_amount": self.risk_amount,
            "stop_distance": self.stop_distance,
            "notional": self.notional,
            "reason": self.reason,
        }


class PositionSizer:
    """Compute order quantity from equity, price and stop distance.

    Args:
        settings: Sizing configuration.
    """

    def __init__(self, settings: SizingSettings) -> None:
        self._settings = settings

    def calculate(
        self,
        *,
        equity: float,
        price: float,
        stop_distance: float,
        confidence: float = 1.0,
        win_rate: float | None = None,
        reward_risk: float | None = None,
    ) -> SizingResult:
        """Compute the position quantity.

        Args:
            equity: Equity actual de la cartera.
            price: Precio de entrada de referencia.
            stop_distance: Distancia absoluta entre entrada y stop.
            confidence: Confianza de la decisión (para riesgo dinámico).
            win_rate: Tasa de acierto histórica (para Kelly).
            reward_risk: Relación beneficio/riesgo (para Kelly).

        Returns:
            Cantidad y su justificación (0 si no es posible dimensionar).
        """
        method = self._settings.method
        if price <= 0 or equity <= 0:
            return SizingResult(0.0, method, 0.0, stop_distance, 0.0, "equity/precio no válidos")

        if method == "fixed_amount":
            notional = self._settings.fixed_amount
            quantity = notional / price
            risk_amount = quantity * stop_distance
            reason = f"monto fijo {notional:.2f}"
        elif method == "percent":
            notional = equity * self._settings.percent_of_equity / 100.0
            quantity = notional / price
            risk_amount = quantity * stop_distance
            reason = f"{self._settings.percent_of_equity:.2f}% del equity"
        elif method == "kelly":
            quantity, risk_amount, reason = self._kelly(
                equity, stop_distance, win_rate, reward_risk
            )
        elif method == "dynamic_risk":
            quantity, risk_amount, reason = self._risk_based(
                equity, stop_distance, confidence=confidence
            )
        else:  # fixed_risk | atr (ambos = riesgo fijo sobre la distancia de stop)
            quantity, risk_amount, reason = self._risk_based(equity, stop_distance, confidence=1.0)

        quantity = self._apply_caps(quantity, equity, price)
        if quantity < self._settings.min_quantity:
            return SizingResult(
                0.0, method, risk_amount, stop_distance, 0.0, "bajo la cantidad mínima"
            )
        return SizingResult(
            quantity=round(quantity, 8),
            method=method,
            risk_amount=round(quantity * stop_distance, 4),
            stop_distance=stop_distance,
            notional=round(quantity * price, 4),
            reason=reason,
        )

    def _risk_based(
        self, equity: float, stop_distance: float, *, confidence: float
    ) -> tuple[float, float, str]:
        """Fixed/dynamic risk sizing off the stop distance."""
        if stop_distance <= 0:
            return 0.0, 0.0, "sin distancia de stop (riesgo indefinido)"
        pct = self._settings.risk_per_trade_pct
        if confidence < 1.0:
            pct *= max(0.25, min(confidence, 1.0))
        risk_amount = equity * pct / 100.0
        quantity = risk_amount / stop_distance
        reason = f"riesgo {pct:.3f}% del equity ({risk_amount:.2f})"
        return quantity, risk_amount, reason

    def _kelly(
        self,
        equity: float,
        stop_distance: float,
        win_rate: float | None,
        reward_risk: float | None,
    ) -> tuple[float, float, str]:
        """Partial-Kelly sizing; falls back to fixed risk without history."""
        if win_rate is None or reward_risk is None or reward_risk <= 0 or stop_distance <= 0:
            quantity, risk_amount, _ = self._risk_based(equity, stop_distance, confidence=1.0)
            return quantity, risk_amount, "Kelly sin historial → riesgo fijo"
        # f* = W - (1 - W) / RR ; se usa una fracción parcial acotada.
        edge = win_rate - (1.0 - win_rate) / reward_risk
        fraction = max(0.0, edge) * self._settings.kelly_fraction
        cap = self._settings.risk_per_trade_pct / 100.0
        fraction = min(fraction, cap)
        risk_amount = equity * fraction
        quantity = risk_amount / stop_distance
        reason = f"Kelly parcial f={fraction:.4f} (W={win_rate:.2f}, RR={reward_risk:.2f})"
        return quantity, risk_amount, reason

    def _apply_caps(self, quantity: float, equity: float, price: float) -> float:
        """Cap the quantity by the maximum notional exposure per trade."""
        if quantity <= 0:
            return 0.0
        max_notional = equity * self._settings.max_position_pct / 100.0
        max_quantity = max_notional / price
        return min(quantity, max_quantity)
