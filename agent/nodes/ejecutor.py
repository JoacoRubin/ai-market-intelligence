"""Nodo ejecutor: corre las herramientas del plan.

No decide nada. Toma el plan que armó el planificador, valida los argumentos
contra el esquema de cada herramienta y ejecuta. Que la validación ocurra acá
—y no dentro de la tool— permite que un argumento inválido consuma un paso del
plan sin romper el grafo entero.
"""

from __future__ import annotations

from agent.state import AnalysisState
from agent.tools.product_metrics import (
    EntradaProductMetrics,
    ejecutar_product_metrics,
)

NOMBRE_A_TOOL = {"product_metrics": (EntradaProductMetrics, ejecutar_product_metrics)}


def ejecutar_plan(estado: AnalysisState) -> AnalysisState:
    estado.ya_ejecutado = True

    for paso in estado.plan:
        entrada_cls, ejecutar = NOMBRE_A_TOOL.get(paso.tool, (None, None))
        if entrada_cls is None:
            estado._advertir(
                f"El plan pedía la herramienta '{paso.tool}', que no está "
                "disponible en esta versión."
            )
            continue

        try:
            entrada = entrada_cls(**paso.argumentos)
        except Exception as e:
            # Un argumento inválido no tumba el grafo: se salta ese paso y se
            # deja constancia. El resto del plan puede seguir siendo útil.
            estado._advertir(
                f"Los argumentos para '{paso.tool}' no son válidos y el paso se "
                f"omitió: {e}"
            )
            continue

        ejecutar(entrada, estado)

    return estado
