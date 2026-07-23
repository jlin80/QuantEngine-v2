"""Motor de ejecución y paper trading (Fase 5).

Capa profesional desacoplada del Strategy/Decision Engine. Ninguna estrategia
envía órdenes: el flujo es Decision Engine → Risk Manager → Execution Engine →
Paper Engine → Position/Portfolio Manager → Journal → Discord.

Regla de oro de la fase: **solo paper trading**. Ninguna orden llega a un
broker real; el objetivo es validar el sistema en condiciones realistas
(spread, slippage, latencia, comisiones, rechazos) sin arriesgar capital.
"""
