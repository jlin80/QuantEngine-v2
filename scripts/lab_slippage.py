"""Cuanto slippage modela el laboratorio, y cuanto se observa de verdad.

El journal de produccion trae `slippage_bps = 0.000` en las 1.693 operaciones
de oro: eso NO mide que el slippage real sea cero, mide que no se registra —
en demo el ejecutor es el broker MT5 y ese campo lo rellena el Paper Engine.
La ausencia de medicion no es un cero.

La via indirecta si mide: una salida por `stop_loss` deberia costar exactamente
1R. Lo que se desvie de -1.000R es el relleno adverso, en la unidad en la que
se compara todo lo demas.
"""

from __future__ import annotations

import random

from app.config.settings import LatencySettings, SlippageSettings
from app.execution.latency import LatencyEngine
from app.execution.models import OrderType
from app.execution.slippage import SlippageContext, SlippageEngine

# Medido sobre el journal real (XAUUSDM, 2026-07-22..08-20):
#   distancia mediana del stop = 15.00 bps  (es `min_stop_pct=0.15`, no el ATR)
#   stop_loss real:      R medio -0.975  (n=250, peor -1.264)
#   stop_loss del lab:   R medio -1.291  (n=397)
_STOP_BPS = 15.00
_REAL_STOP_R = -0.975
_LAB_STOP_R = -1.291
_SPREAD_BPS = 0.56
_ATR_PCT = 0.04


def main() -> None:
    """Compare the modelled adverse fill against the observed one."""
    slippage = SlippageEngine(SlippageSettings())
    latency = LatencyEngine(LatencySettings(), rng=random.Random(7))

    ctx = SlippageContext(
        atr_pct=_ATR_PCT,
        spread_bps=_SPREAD_BPS,
        order_quantity=0.01,
        session="europe",
        order_type=OrderType.MARKET,
    )
    slip_bps = slippage.estimate_bps(ctx)
    drift_bps = latency.sample().drift_bps
    adverse = slip_bps + drift_bps

    print(f"Distancia del stop (1R):        {_STOP_BPS:.2f} bps")
    print()
    print("MODELADO por el laboratorio, por pata:")
    print(f"  slippage                      {slip_bps:.3f} bps")
    print(f"  deriva por latencia           {drift_bps:.3f} bps")
    print(f"  total adverso                 {adverse:.3f} bps  =  {adverse / _STOP_BPS:.3f}R")
    both = 2 * adverse
    print(f"  ida y vuelta                  {both:.3f} bps  =  {both / _STOP_BPS:.3f}R")
    print()
    observed = abs(_REAL_STOP_R) - 1.0
    lab = abs(_LAB_STOP_R) - 1.0
    print("OBSERVADO en los stops (lo que se desvia de -1.000R):")
    print(f"  produccion (n=250)            {observed:+.3f}R  =  {observed * _STOP_BPS:+.3f} bps")
    print(f"  laboratorio (n=397)           {lab:+.3f}R  =  {lab * _STOP_BPS:+.3f} bps")
    print()
    if observed != 0:
        ratio = lab / abs(observed)
        print(f"El laboratorio modela {ratio:.0f}x el relleno adverso observado.")
    print(
        "En oro el stop son 15 bps, asi que 1 bps de slippage es 1/15 de R:\n"
        "el modelo por defecto (base 1.0 bps + coeficientes + latencia) es\n"
        "enorme frente a la distancia que realmente se arriesga."
    )


if __name__ == "__main__":
    main()
