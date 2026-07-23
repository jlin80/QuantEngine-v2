"""Capa de producción (Fase 9): live gating, safe mode, kill switch, recuperación.

Convierte el motor en una plataforma operable 24/7. El principio rector de la
fase es explícito: **la estabilidad tiene prioridad sobre la rentabilidad**. Ante
cualquier duda sobre el estado del sistema, se deja de operar automáticamente.

Regla de oro de la fase: **Live Trading sigue deshabilitado**. Aquí se construye
la maquinaria que algún día podrá habilitarlo —el Live Gate, con criterios
estadísticos, de infraestructura y de aprobación humana— pero no hay ningún
atajo para saltársela y ``production.allow_live`` nace en ``False``.
"""
