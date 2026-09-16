"""Nodo ejecutor: corre las herramientas del plan.

No decide nada. Toma el plan que armó el planificador, valida los argumentos
contra el esquema de cada herramienta y ejecuta. Que la validación ocurra acá
—y no dentro de la tool— permite que un argumento inválido consuma un paso del
plan sin romper el grafo entero.

**Un fallo de una tool tampoco tumba el grafo.** Esa garantía es de este nodo y
no de cada tool, y hasta esta versión no existía: la validación de argumentos
estaba protegida, la ejecución no. La asimetría era medible — `research_company`
manejaba sus propias excepciones porque sale a internet, mientras que
`product_metrics`, `search_documents` y `forecast_sales` no tenían ni un
`except`. Con SQL Server caído a mitad de un análisis, un `pyodbc.Error` subía
hasta el borde del grafo y el análisis entero terminaba FALLIDO.

Y eso contradice lo que el sistema promete en todas las demás capas: sin índice
FAISS el agente degrada, sin LLM degrada, sin Redis degrada. Sin base tenía que
degradar también — a un informe con la evidencia que sí pudo reunir, o a uno que
dice explícitamente que no reunió ninguna. Un fallo parcial es información, no
un motivo para tirar el trabajo hecho.
"""

from __future__ import annotations

import time
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

        inicio = time.perf_counter()
        try:
            definicion.ejecutar(entrada, estado, indice)
        except Exception as e:
            # Se deja constancia con el TIPO de excepción además del mensaje.
            # Un `pyodbc.OperationalError` y un `ValueError` significan cosas
            # muy distintas para quien lee el informe después: uno dice "la
            # infraestructura no estaba", el otro "los datos no daban".
            duracion = int((time.perf_counter() - inicio) * 1000)
            estado.registrar_paso(
                f"{paso.tool}_fallida", duracion, tool=str(paso.tool)
            )
            if str(paso.tool) not in estado.tools_fallidas:
                estado.tools_fallidas.append(str(paso.tool))
            estado._advertir(
                f"La herramienta '{paso.tool}' falló y su resultado no entra en "
                f"el informe: {type(e).__name__}: {e}"
            )
            continue

    return estado
