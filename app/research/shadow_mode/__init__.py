"""Shadow Mode (Fase 10, mejora obligatoria): evolución segura por evidencia.

Una estrategia experimental (challenger) recibe exactamente los mismos datos que
la vigente (official), genera señales y simula operaciones en paralelo —sin
enviar órdenes, sin modificar posiciones, sin tocar el Decision Engine—. Tras un
período configurable, produce un informe comparativo que indica si la challenger
supera a la vigente de forma **estadísticamente significativa**.
"""

from app.research.shadow_mode.comparator import ShadowComparator
from app.research.shadow_mode.session import ShadowSession

__all__ = ["ShadowComparator", "ShadowSession"]
