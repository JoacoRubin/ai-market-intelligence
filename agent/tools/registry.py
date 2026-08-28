"""Catálogo único de herramientas ejecutables por el agente.

Los nombres válidos, sus esquemas de entrada, handlers y reglas de
disponibilidad viven acá. Estado, planner y ejecutor consumen este contrato en
vez de mantener listas y dispatches paralelos que puedan desincronizarse.

El catálogo se construye de manera diferida para que los módulos de cada tool
puedan importar :class:`ToolName` sin crear un ciclo de imports.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

if TYPE_CHECKING:
    from agent.state import AnalysisState


class ToolName(StrEnum):
    """Nombres de las capacidades realmente implementadas."""

    PRODUCT_METRICS = "product_metrics"
    SEARCH_DOCUMENTS = "search_documents"
    FORECAST_SALES = "forecast_sales"


ToolHandler = Callable[[BaseModel, "AnalysisState", Any], Any]
ToolAvailability = Callable[[Any], bool]


def _siempre_disponible(_contexto: Any) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class DefinicionTool:
    """Contrato completo de una herramienta disponible para el agente."""

    name: ToolName
    input_model: type[BaseModel]
    handler: ToolHandler
    availability: ToolAvailability = _siempre_disponible

    def esta_disponible(self, contexto: Any = None) -> bool:
        return self.availability(contexto)

    def validar_argumentos(self, argumentos: Mapping[str, Any]) -> BaseModel:
        return self.input_model.model_validate(dict(argumentos))

    def ejecutar(
        self,
        entrada: BaseModel,
        estado: AnalysisState,
        contexto: Any = None,
    ) -> Any:
        return self.handler(entrada, estado, contexto)


def _requiere_indice(indice: Any) -> bool:
    return indice is not None


@lru_cache(maxsize=1)
def catalogo_tools() -> Mapping[ToolName, DefinicionTool]:
    """Devuelve el catálogo inmutable de capacidades activas.

    Los imports locales evitan cargar SQL, RAG y ML cuando solo se valida el
    estado o se construye un plan.
    """
    from agent.tools.forecast_sales import (
        EntradaForecastSales,
        ejecutar_forecast_sales,
    )
    from agent.tools.product_metrics import (
        EntradaProductMetrics,
        ejecutar_product_metrics,
    )
    from agent.tools.search_documents import (
        EntradaSearchDocuments,
        ejecutar_search_documents,
    )

    def ejecutar_metricas(
        entrada: BaseModel,
        estado: AnalysisState,
        _contexto: Any,
    ) -> Any:
        return ejecutar_product_metrics(cast(EntradaProductMetrics, entrada), estado)

    def ejecutar_documentos(
        entrada: BaseModel,
        estado: AnalysisState,
        indice: Any,
    ) -> Any:
        return ejecutar_search_documents(cast(EntradaSearchDocuments, entrada), estado, indice)

    def ejecutar_forecast(
        entrada: BaseModel,
        estado: AnalysisState,
        _contexto: Any,
    ) -> Any:
        return ejecutar_forecast_sales(cast(EntradaForecastSales, entrada), estado)

    definiciones = {
        ToolName.PRODUCT_METRICS: DefinicionTool(
            name=ToolName.PRODUCT_METRICS,
            input_model=EntradaProductMetrics,
            handler=ejecutar_metricas,
        ),
        ToolName.SEARCH_DOCUMENTS: DefinicionTool(
            name=ToolName.SEARCH_DOCUMENTS,
            input_model=EntradaSearchDocuments,
            handler=ejecutar_documentos,
            availability=_requiere_indice,
        ),
        ToolName.FORECAST_SALES: DefinicionTool(
            name=ToolName.FORECAST_SALES,
            input_model=EntradaForecastSales,
            handler=ejecutar_forecast,
        ),
    }

    # Falla cerca del cambio si se agrega un nombre sin implementación (o al
    # revés), en lugar de descubrir el drift al ejecutar un plan en producción.
    if set(definiciones) != set(ToolName):
        raise RuntimeError("El catálogo de tools no coincide con los nombres permitidos")

    return MappingProxyType(definiciones)


def buscar_tool(nombre: ToolName | str) -> DefinicionTool | None:
    """Busca una definición sin asumir que el dato de entrada es confiable."""
    try:
        nombre_tipado = nombre if isinstance(nombre, ToolName) else ToolName(nombre)
    except ValueError:
        return None
    return catalogo_tools().get(nombre_tipado)

