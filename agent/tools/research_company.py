"""Tool `research_company`: situación financiera de empresas externas, desde
SEC EDGAR (`core/edgar.py`).

Misma arquitectura de tres capas que `product_metrics.py`
(`docs/adr/ADR-004-sin-text-to-sql.md`), adaptada a que acá no hay SQL:

  1. **Este esquema**: lista blanca de qué es un nombre de empresa válido —
     necesariamente más laxa que `^P\\d{1,6}$` (un nombre societario no tiene
     formato fijo), pero acota longitud y cantidad, rechaza control chars.
  2. **Resolución determinística nombre→CIK** (`core.edgar.resolver_empresa`):
     el nombre libre nunca llega a formar parte de una URL — se usa solo
     como clave de búsqueda en memoria contra el listado oficial de la SEC.
  3. **La URL final la arma el código con el CIK ya resuelto**
     (`core.edgar.hechos_clave`), nunca con texto que vino del modelo.

El planner pasa los nombres CRUDOS que extrajo el router (`estado.empresas`)
— la resolución ocurre acá adentro, no antes.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

from agent.tools.registry import ToolName
from core.edgar import (
    EmpresaResuelta,
    HechoFinanciero,
    hay_edgar_disponible,
    hechos_clave,
    resolver_empresa,
)

if TYPE_CHECKING:
    from agent.state import AnalysisState

NOMBRE = ToolName.RESEARCH_COMPANY
# Mismo límite que forecast_sales aplica sobre entidades (planner.py) — un
# techo auditable, no arbitrario.
MAX_EMPRESAS = 2
PATRON_NOMBRE = re.compile(r"^[\w &.,'\-]{2,80}$", re.UNICODE)


class EntradaResearchCompany(BaseModel):
    """Argumentos válidos de la herramienta."""

    nombres: list[str] = Field(
        min_length=1, max_length=MAX_EMPRESAS,
        description="Nombres de empresa o tickers mencionados en la consulta, "
                    "en lenguaje natural (por ejemplo 'Apple' o 'AMZN').",
    )

    @field_validator("nombres")
    @classmethod
    def _formato(cls, valores: list[str]) -> list[str]:
        invalidos = [v for v in valores if not PATRON_NOMBRE.match(v.strip())]
        if invalidos:
            raise ValueError(f"nombres con formato inválido: {invalidos}")
        return list(dict.fromkeys(v.strip() for v in valores))


def esquema_para_llm() -> dict[str, Any]:
    """Esquema en formato de tool calling de Ollama, derivado del modelo
    Pydantic — mismo criterio que `product_metrics.esquema_para_llm()`."""
    esquema = EntradaResearchCompany.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": NOMBRE,
            "description": (
                "Consulta la situación financiera oficial (revenue, ganancia "
                "neta, activos) de una o más empresas que cotizan en EE. UU., "
                "según su 10-K más reciente en SEC EDGAR. Usar cuando la "
                "pregunta involucre empresas externas al catálogo interno."
            ),
            "parameters": {
                "type": "object",
                "properties": esquema["properties"],
                "required": esquema.get("required", []),
            },
        },
    }


def ejecutar_research_company(
    entrada: EntradaResearchCompany, estado: AnalysisState
) -> list[HechoFinanciero]:
    """Resuelve cada nombre contra la SEC y trae sus hechos financieros.

    Un nombre que no resuelve, o una empresa sin datos, no aborta la
    ejecución — se informa y se sigue con las demás, mismo patrón no-fatal
    que `product_metrics.py`.
    """
    if not estado.puede_llamar_tool():
        estado.registrar_llamada_tool()
        return []

    estado.registrar_llamada_tool()
    inicio = time.perf_counter()

    if not hay_edgar_disponible():
        # Se distingue de "no encontrada": acá el problema es de
        # infraestructura, no de que el nombre esté mal — un timeout y un
        # nombre inexistente son dos problemas distintos y se ven igual de
        # afuera si no se separan (mismo argumento que ya usa
        # synthesizer.py sobre el respaldo determinístico).
        estado._advertir("SEC EDGAR no está respondiendo — no se pudo investigar ninguna empresa.")
        estado.registrar_paso("edgar_tool", int((time.perf_counter() - inicio) * 1000), tool=NOMBRE)
        estado.registrar_resultado(NOMBRE, [])
        return []

    hechos: list[HechoFinanciero] = []
    no_encontradas: list[str] = []

    for candidato in entrada.nombres:
        resuelta: EmpresaResuelta | None = None
        try:
            resuelta = resolver_empresa(candidato)
        except Exception:
            resuelta = None
        if resuelta is None:
            no_encontradas.append(candidato)
            continue
        try:
            hechos.extend(hechos_clave(resuelta))
        except Exception as e:
            estado._advertir(f"No se pudo consultar SEC EDGAR para {resuelta.nombre}: {e}")

    duracion = int((time.perf_counter() - inicio) * 1000)
    estado.registrar_paso("edgar_tool", duracion, tool=NOMBRE)

    if no_encontradas:
        estado._advertir(
            f"No se encontraron en SEC EDGAR: {', '.join(no_encontradas)}. "
            "Puede que no coticen en EE. UU. o fileen con otro nombre."
        )

    estado.registrar_resultado(NOMBRE, hechos)
    return hechos
