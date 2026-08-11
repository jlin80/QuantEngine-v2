"""Position sizing: cuánta cantidad operar según el método configurado.

Métodos soportados: monto fijo, porcentaje del capital, ATR, riesgo fijo,
riesgo dinámico (ajustado por confianza) y Kelly parcial. Todos respetan un
tope de exposición por operación (``max_position_pct``) y una cantidad mínima.

Unidades: el cálculo interno razona en **unidades del subyacente** (donde el
riesgo es ``unidades × distancia_de_stop``), pero ``quantity`` se devuelve en la
unidad que el broker acepta — **lotes** — dividiendo por el ``contract_size`` del
símbolo. Sin esa conversión un lote de XAUUSD (contract_size=100) se envía 100×
más grande de lo que el motor cree.
"""

from dataclasses import dataclass

from app.config.settings import SizingSettings
from app.execution.models.instrument import DEFAULT_SPEC, InstrumentSpec


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Cantidad calculada y su justificación.

    Args:
        quantity: Volumen a enviar al broker, en **lotes** del símbolo.
        units: Unidades del subyacente equivalentes (``quantity × contract_size``).
        notional: Valor nocional real de la posición (``units × precio``).
    """

    quantity: float
    method: str
    risk_amount: float
    stop_distance: float
    notional: float
    reason: str
    units: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """JSON-safe dict."""
        return {
            "quantity": self.quantity,
            "units": self.units,
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
        spec: InstrumentSpec | None = None,
        risk_multiplier: float = 1.0,
        symbol: str | None = None,
    ) -> SizingResult:
        """Compute the position quantity.

        Args:
            equity: Equity actual de la cartera.
            price: Precio de entrada de referencia.
            stop_distance: Distancia absoluta entre entrada y stop.
            confidence: Confianza de la decisión (para riesgo dinámico).
            win_rate: Tasa de acierto histórica (para Kelly).
            reward_risk: Relación beneficio/riesgo (para Kelly).
            spec: Contrato del símbolo. Sin él se asume 1 lote = 1 unidad (paper).
            risk_multiplier: Reductor externo de exposición, en ``(0, 1]``
                (Bloque 11: calidad del dato). Se aplica **después** de los
                topes y **antes** del redondeo a lotes: así reducir riesgo puede
                dejar la operación por debajo del lote mínimo y rechazarse
                limpio, que es el comportamiento correcto — si el dato no es
                fiable y el tamaño reducido ya no cabe, no se opera. Nunca
                puede aumentar el tamaño: valores por encima de 1.0 se acotan.
            symbol: Símbolo a operar. Resuelve el riesgo por operación y el
                tope de notional por símbolo (``*_by_symbol``), con el global
                como fallback. Sin él (compatibilidad con llamadas antiguas y
                tests) se usa directamente el global.

        Returns:
            Cantidad en lotes y su justificación (0 si no es posible dimensionar).
        """
        method = self._settings.method
        spec = spec or DEFAULT_SPEC
        if price <= 0 or equity <= 0:
            return SizingResult(0.0, method, 0.0, stop_distance, 0.0, "equity/precio no válidos")

        risk_pct = (
            self._settings.risk_per_trade_pct_for(symbol)
            if symbol is not None
            else self._settings.risk_per_trade_pct
        )
        position_pct = (
            self._settings.max_position_pct_for(symbol)
            if symbol is not None
            else self._settings.max_position_pct
        )

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
                equity, stop_distance, win_rate, reward_risk, risk_pct
            )
        elif method == "dynamic_risk":
            quantity, risk_amount, reason = self._risk_based(
                equity, stop_distance, confidence=confidence, risk_pct=risk_pct
            )
        else:  # fixed_risk | atr (ambos = riesgo fijo sobre la distancia de stop)
            quantity, risk_amount, reason = self._risk_based(
                equity, stop_distance, confidence=1.0, risk_pct=risk_pct
            )

        # El reductor externo entra DESPUÉS de los topes, y el orden importa:
        # aplicado antes, un tope que ya estuviera mordiendo se lo tragaba
        # entero —reducir a la mitad una cantidad que el tope iba a recortar
        # igualmente no reduce nada— y la protección desaparecía justo en las
        # operaciones más grandes, que son las que más importan. Acotado por
        # arriba a 1.0: subir tamaño por aquí sería una puerta trasera al sizing.
        units = self._apply_caps(quantity, equity, price, position_pct) * max(
            0.0, min(1.0, risk_multiplier)
        )
        if units < self._settings.min_quantity:
            return SizingResult(
                0.0, method, risk_amount, stop_distance, 0.0, "bajo la cantidad mínima"
            )

        # Unidades → lotes (lo que el broker acepta), redondeando hacia abajo al
        # paso del símbolo. Si el lote mínimo no cabe en el presupuesto de riesgo
        # se rechaza limpio: inflarlo hasta `volume_min` operaría con un riesgo
        # muy superior al configurado (el bug histórico del oro).
        lots = spec.quantize(units / spec.contract_size if spec.contract_size > 0 else units)
        if not spec.fits(lots):
            return SizingResult(
                0.0,
                method,
                risk_amount,
                stop_distance,
                0.0,
                (
                    f"el lote mínimo ({spec.volume_min}) no cabe en el riesgo: "
                    f"caben {lots:.4f} lotes de {spec.symbol or 'el símbolo'}"
                ),
            )

        final_units = spec.units(lots)
        return SizingResult(
            quantity=round(lots, 8),
            units=round(final_units, 8),
            method=method,
            risk_amount=round(final_units * stop_distance, 4),
            stop_distance=stop_distance,
            notional=round(final_units * price, 4),
            reason=reason,
        )

    def _risk_based(
        self, equity: float, stop_distance: float, *, confidence: float, risk_pct: float
    ) -> tuple[float, float, str]:
        """Fixed/dynamic risk sizing off the stop distance."""
        if stop_distance <= 0:
            return 0.0, 0.0, "sin distancia de stop (riesgo indefinido)"
        pct = risk_pct
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
        risk_pct: float,
    ) -> tuple[float, float, str]:
        """Partial-Kelly sizing; falls back to fixed risk without history."""
        if win_rate is None or reward_risk is None or reward_risk <= 0 or stop_distance <= 0:
            quantity, risk_amount, _ = self._risk_based(
                equity, stop_distance, confidence=1.0, risk_pct=risk_pct
            )
            return quantity, risk_amount, "Kelly sin historial → riesgo fijo"
        # f* = W - (1 - W) / RR ; se usa una fracción parcial acotada.
        edge = win_rate - (1.0 - win_rate) / reward_risk
        fraction = max(0.0, edge) * self._settings.kelly_fraction
        cap = risk_pct / 100.0
        fraction = min(fraction, cap)
        risk_amount = equity * fraction
        quantity = risk_amount / stop_distance
        reason = f"Kelly parcial f={fraction:.4f} (W={win_rate:.2f}, RR={reward_risk:.2f})"
        return quantity, risk_amount, reason

    def _apply_caps(
        self, quantity: float, equity: float, price: float, max_position_pct: float
    ) -> float:
        """Cap the quantity by the maximum notional exposure per trade."""
        if quantity <= 0:
            return 0.0
        max_notional = equity * max_position_pct / 100.0
        max_quantity = max_notional / price
        return min(quantity, max_quantity)
