"""Especificación del contrato de un instrumento en el venue de ejecución.

El sizing razona en **lotes** (la unidad que el broker acepta en ``order_send``),
pero el riesgo y la exposición se miden sobre el **notional**, que depende del
tamaño de contrato del símbolo. Sin esta traducción un lote de ``XAUUSDm``
(``contract_size=100``) se envía 100× más grande de lo que el motor cree.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    """Posición tal y como la ve el broker real, no el motor.

    La usa la adopción de arranque: tras un reinicio el Position Manager está
    vacío pero la cuenta puede tener posiciones vivas, y sin adoptarlas el motor
    abre duplicados y no las gestiona (ni trailing, ni break-even, ni exposición).

    Args:
        ticket: Identificador de la posición en el broker.
        symbol: Símbolo interno (MAYÚSCULAS).
        is_long: Dirección de la posición.
        volume: Volumen en lotes del broker.
        price_open: Precio de apertura.
        stop_loss: Stop en el broker (``None`` si no tiene).
        take_profit: Objetivo en el broker (``None`` si no tiene).
        opened_at: Instante de apertura (UTC).
    """

    ticket: int
    symbol: str
    is_long: bool
    volume: float
    price_open: float
    stop_loss: float | None = None
    take_profit: float | None = None
    opened_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """Tamaño de contrato y límites de volumen de un símbolo.

    Args:
        symbol: Símbolo interno (mayúsculas).
        contract_size: Unidades del activo por lote (1 en cripto, 100 en XAU).
        volume_min: Lote mínimo negociable.
        volume_step: Incremento de lote admitido.
        volume_max: Lote máximo (0 = sin tope conocido).
    """

    symbol: str
    contract_size: float = 1.0
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 0.0

    def units(self, lots: float) -> float:
        """Unidades del activo subyacente que representan ``lots``."""
        return lots * self.contract_size

    def notional(self, lots: float, price: float) -> float:
        """Valor nocional de ``lots`` al precio dado."""
        return self.units(lots) * price

    def quantize(self, lots: float) -> float:
        """Ajusta ``lots`` al paso del símbolo, **sin** subirlo al mínimo.

        Redondea hacia abajo: nunca devuelve más riesgo del solicitado. Un
        resultado por debajo de ``volume_min`` se deja tal cual para que quien
        llama pueda rechazar limpio en vez de inflar la orden.
        """
        if lots <= 0:
            return 0.0
        capped = min(lots, self.volume_max) if self.volume_max > 0 else lots
        if self.volume_step <= 0:  # sin paso conocido (paper): no se redondea
            return round(capped, 8)
        return round(int(capped / self.volume_step) * self.volume_step, 8)

    def fits(self, lots: float) -> bool:
        """Whether ``lots`` reaches the symbol's minimum tradable volume."""
        return lots >= self.volume_min - 1e-12


DEFAULT_SPEC = InstrumentSpec(
    symbol="", contract_size=1.0, volume_min=0.0, volume_step=0.0, volume_max=0.0
)
"""Spec neutro para el paper engine: 1 lote = 1 unidad, sin mínimo ni paso.

Deja el sizing exactamente como estaba antes de que existieran los specs; sólo
un broker real (MT5) aporta contract_size/volume_min y activa el redondeo y el
rechazo por lote mínimo.
"""
