"""Estado del agente: lo que viaja entre los nodos del grafo.

Es un modelo tipado y no un diccionario suelto por una razón concreta: cada nodo
recibe el estado, lo modifica y lo devuelve. Si fuera un dict, un nodo que
escribe `resultado_tools` en vez de `resultados_tools` no fallaría — crearía una
clave nueva, y el error aparecería tres nodos después, cuando el sintetizador
no encuentre nada. Con un modelo, revienta en el momento y en el lugar.

**Los límites viven acá, no en los nodos.** `max_llamadas_tools` y
`max_reintentos` son la defensa contra el problema clásico de los agentes: el
loop infinito. Si cada nodo tuviera que acordarse de chequear un contador,
alcanzaría con que uno se olvide. Acá el presupuesto es del estado, y el estado
lo ve todo el grafo.

En esta máquina, con inferencia CPU-only, cada iteración cuesta segundos reales.
Un agente sin techo no es un bug molesto: es el producto inutilizable.
"""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from agent.tools.registry import ToolName
from core.report import PasoTrace, Report


class Intencion(StrEnum):
    """Tipo de análisis solicitado.

    `FUERA_DE_ALCANCE` es una intención de primera clase, no un caso de error.
    Que el agente pueda decir "esto no me corresponde" es una capacidad: un
    agente que siempre intenta responder, siempre responde algo — aunque no
    tenga con qué.
    """

    PRODUCT_PERFORMANCE = "product_performance"
    COMPANY_RESEARCH = "company_research"
    HYBRID = "hybrid"
    FUERA_DE_ALCANCE = "fuera_de_alcance"


class Periodo(BaseModel):
    desde: date
    hasta: date

    @model_validator(mode="after")
    def _orden_coherente(self) -> Periodo:
        if self.desde > self.hasta:
            raise ValueError(
                f"período invertido: desde={self.desde} es posterior a hasta={self.hasta}"
            )
        return self


class PasoPlan(BaseModel):
    """Una acción planificada, con su justificación.

    `razon` no es documentación: es lo que se muestra en "Cómo se obtuvo" y lo
    que permite auditar si el agente eligió bien la herramienta.
    """

    # ToolName sale del catálogo ejecutable. Cualquier otro nombre es una
    # alucinación del plan y Pydantic la rechaza en este borde.
    tool: ToolName
    argumentos: dict[str, Any] = Field(default_factory=dict)
    razon: str = ""


class AnalysisState(BaseModel):
    """Estado compartido por todos los nodos del grafo."""

    # --- identidad ---
    request_id: str
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    consulta: str = Field(min_length=1)

    # --- comprensión ---
    intencion: Intencion | None = None
    entidades: list[str] = Field(default_factory=list)
    periodo: Periodo | None = None

    # --- ejecución ---
    plan: list[PasoPlan] = Field(default_factory=list)
    # Lo pone el ejecutor. Es lo que le permite al planificador saber si
    # está armando el primer plan o corrigiendo uno que no encontró datos.
    ya_ejecutado: bool = False
    resultados_tools: dict[str, Any] = Field(default_factory=dict)
    evidencia: list[dict[str, Any]] = Field(default_factory=list)

    # --- presupuesto ---
    reintentos: int = 0
    max_reintentos: int = 2
    llamadas_tools: int = 0
    max_llamadas_tools: int = 8

    # --- salida ---
    informe: Report | None = None
    advertencias: list[str] = Field(default_factory=list)
    trace: list[PasoTrace] = Field(default_factory=list)
    error: str | None = None

    # --- presupuesto de herramientas ------------------------------------

    def puede_llamar_tool(self) -> bool:
        return self.llamadas_tools < self.max_llamadas_tools

    def registrar_llamada_tool(self) -> None:
        """Consume presupuesto. Al agotarlo deja constancia en el informe.

        El límite no se alcanza en silencio: si el agente se quedó sin
        herramientas, el resultado puede estar incompleto y el lector merece
        enterarse.
        """
        if not self.puede_llamar_tool():
            self._advertir(
                f"Se alcanzó el límite de {self.max_llamadas_tools} llamadas a "
                "herramientas. El análisis puede estar incompleto."
            )
            return
        self.llamadas_tools += 1

    # --- presupuesto de replanificación ---------------------------------

    def puede_reintentar(self) -> bool:
        return self.reintentos < self.max_reintentos

    def registrar_reintento(self) -> None:
        if not self.puede_reintentar():
            self._advertir(
                f"Se agotaron los {self.max_reintentos} intentos de replanificación "
                "sin reunir evidencia suficiente."
            )
            return
        self.reintentos += 1

    # --- resultados -----------------------------------------------------

    def registrar_resultado(self, tool: str, resultado: Any) -> None:
        self.resultados_tools[tool] = resultado

    def hay_evidencia_suficiente(self) -> bool:
        """Decide si el grafo puede sintetizar o tiene que replanificar.

        Un resultado vacío NO cuenta. Dejar que el modelo redacte sin datos es
        exactamente cómo se producen los informes inventados: el LLM siempre
        encuentra algo para decir.
        """
        return any(bool(r) for r in self.resultados_tools.values())

    # --- trazabilidad ---------------------------------------------------

    def registrar_paso(self, nodo: str, duracion_ms: int,
                       tool: str | None = None) -> None:
        self.trace.append(PasoTrace(nodo=nodo, duracion_ms=duracion_ms, tool=tool))

    @property
    def duracion_total_ms(self) -> int:
        return sum(p.duracion_ms for p in self.trace)

    # --- interno --------------------------------------------------------

    def _advertir(self, mensaje: str) -> None:
        if mensaje not in self.advertencias:
            self.advertencias.append(mensaje)
