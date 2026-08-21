"""Cuánta fricción impone el laboratorio que el broker real NO cobra.

Mide, con los motores reales de la Fase 5 (comisión, slippage, latencia), el
coste de ida y vuelta de una operación representativa y lo expresa en R — la
unidad en la que se comparan backtest y producción.

Existe porque el backtest da -0.27R en oro donde el journal real da +0.0087R:
cambia el signo. Si la fricción simulada es del orden de la diferencia, el
laboratorio no está midiendo el edge, está midiendo su propio modelo de costes.

Dato de contraste: en 801 deals reales de Exness, CERO tienen `commission != 0`.
"""

from __future__ import annotations

import random

from app.config.settings import CommissionSettings, LatencySettings, SlippageSettings
from app.execution.commission import CommissionEngine
from app.execution.latency import LatencyEngine
from app.execution.models import OrderType
from app.execution.slippage import SlippageContext, SlippageEngine

# Escenarios: (etiqueta, precio, contract_size, lotes, spread_bps, atr_pct, R en $).
# XAUUSDM: 0.01 lotes = 1 onza. El R de la fila `prod` es la perdida mediana
# real del journal (1.19 USD), no un porcentaje supuesto del balance.
_CASES = [
    ("XAUUSDM  lab   (balance 1000, R=0.5%)", 4378.0, 100.0, 0.01, 0.56, 0.04, 5.00),
    ("XAUUSDM  prod  (balance  400, R real)", 4378.0, 100.0, 0.01, 0.56, 0.04, 1.19),
    ("ETHUSDM  lab   (balance 1000, R=0.5%)", 1946.0, 1.0, 0.17, 3.03, 0.08, 5.00),
]


def main() -> None:
    """Print the round-trip friction of each scenario, in dollars and in R."""
    commission = CommissionEngine(CommissionSettings())
    slippage = SlippageEngine(SlippageSettings())
    # Semilla fija: la latencia tiene jitter uniforme y aquí interesa el orden
    # de magnitud reproducible, no una corrida concreta.
    latency = LatencyEngine(LatencySettings(), rng=random.Random(7))

    header = f"{'escenario':<40} {'nocional':>10} {'R ($)':>8} {'coste ida+vuelta':>17} {'en R':>8}"
    print(header)
    print("-" * len(header))
    for label, price, csize, lots, spread_bps, atr_pct, r_usd in _CASES:
        units = lots * csize
        notional = units * price

        # Comisión: dos patas (apertura y cierre), taker en ambas.
        fee = 2.0 * commission.calculate("XAUUSDM", units, price, maker=False).amount

        # Slippage + deriva por latencia: también en ambas patas.
        ctx = SlippageContext(
            atr_pct=atr_pct,
            spread_bps=spread_bps,
            order_quantity=lots,
            session="europe",
            order_type=OrderType.MARKET,
        )
        adverse_bps = slippage.estimate_bps(ctx) + latency.sample().drift_bps
        slip = 2.0 * notional * adverse_bps / 10_000.0

        total = fee + slip
        print(
            f"{label:<40} {notional:>10,.0f} {r_usd:>8.2f} "
            f"{total:>10.2f} (fee {fee:.2f}) {total / r_usd:>7.2f}R"
        )

    print()
    print("La comisión es la parte que Exness NO cobra: en 801 deals reales,")
    print("cero con commission != 0. El slippage y la latencia sí son reales,")
    print("pero su magnitud aquí es un modelo, no una medición del broker.")


if __name__ == "__main__":
    main()
