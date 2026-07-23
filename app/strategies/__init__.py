"""Biblioteca de estrategias cuantitativas (Fase 4).

Estructura:
    base/            QuantStrategy: scoring, confianza, confirmaciones, señal.
    confirmation/    Motor de confirmaciones adicionales.
    filters/         Pre-chequeos por estrategia (datos, spread, calidad).
    shared/          Niveles (entry/SL/TP) y APIs internas de detección.
    utils/           Helpers de series y velas.
    trend/ momentum/ orderflow/ smc/ volume/ volatility/
    mean_reversion/ breakout/     ← plugins (una estrategia por archivo).

Las estrategias solo analizan: la decisión es del Decision Engine (Fase 3) y
la ejecución no existe todavía (regla absoluta de la fase).
"""
