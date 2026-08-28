"""Nodo ejecutor: corre las herramientas del plan.

No decide nada. Toma el plan que armó el planificador, valida los argumentos
contra el esquema de cada herramienta y ejecuta. Que la validación ocurra acá
—y no dentro de la tool— permite que un argumento inválido consuma un paso del
plan sin romper el grafo entero.
"""

from __future__ import annotations

from typing import Any

from agent.state import AnalysisState
from agent.tools.registry import buscar_tool


def ejecutar_plan(estado: AnalysisState, indice: Any = None) -> AnalysisState:
    estado.ya_ejecutado = True

    for paso in estado.plan:
        definicion = buscar_tool(paso.tool)
        if definicion is None or not definicion.esta_disponible(indice):
            estado._advertir(
                f"El plan pedía la herramienta '{paso.tool}', que no está "
                "disponible en esta versión."
            )
            continue

        try:
            entrada = definicion.validar_argumentos(paso.argumentos)
        except Exception as e:
            # Un argumento inválido no tumba el grafo: se salta ese paso y se
            # deja constancia. El resto del plan puede seguir siendo útil.
            estado._advertir(
                f"Los argumentos para '{paso.tool}' no son válidos y el paso se "
                f"omitió: {e}"
            )
            continue

        definicion.ejecutar(entrada, estado, indice)

    return estado
