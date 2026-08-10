"""Contratos de entrada y salida de la API.

Separados del modelo de dominio (`core.report`) a propósito: el informe es lo
que el sistema produce, y estos esquemas son cómo se pide y cómo se entrega.
Mezclarlos ata el contrato público a la estructura interna, y después cualquier
refactor rompe a los consumidores.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from core.report import Report

MAX_PRODUCTOS_POR_ANALISIS = 10


class RangoFechas(BaseModel):
    """Rango temporal cerrado. Base de todo lo que se consulta por período."""

    desde: date
    hasta: date

    @model_validator(mode="after")
    def _orden_coherente(self) -> RangoFechas:
        if self.desde > self.hasta:
            raise ValueError(
                f"el rango está invertido: 'desde' ({self.desde}) es posterior "
                f"a 'hasta' ({self.hasta})"
            )
        return self


class Producto(BaseModel):
    id: str
    brand: str
    category: str
    price: float
    cost: float
    launch_date: date


class ListaProductos(BaseModel):
    total: int
    items: list[Producto]


class SolicitudAnalisis(RangoFechas):
    """Cuerpo del POST /analyses."""

    product_ids: list[str] = Field(min_length=1, max_length=MAX_PRODUCTOS_POR_ANALISIS)

    @model_validator(mode="after")
    def _sin_repetidos(self) -> SolicitudAnalisis:
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("hay productos repetidos en la solicitud")
        return self


class EstadoAnalisis(StrEnum):
    """Estados del recurso análisis.

    El recurso existe desde que se crea; lo que cambia es su estado. Por eso el
    POST responde 202 y no 201: fue aceptado, todavía no terminó.
    """

    PENDIENTE = "pendiente"
    PROCESANDO = "procesando"
    COMPLETADO = "completado"
    FALLIDO = "fallido"


class AnalisisResumen(BaseModel):
    """Vista liviana, para el listado."""

    id: str
    estado: EstadoAnalisis
    creado_en: datetime
    product_ids: list[str]
    desde: date
    hasta: date


class Analisis(AnalisisResumen):
    """Vista completa. `informe` sólo aparece cuando el estado es completado."""

    informe: Report | None = None
    error: str | None = None
    etapas: list[str] = Field(default_factory=list)


class ListaAnalisis(BaseModel):
    total: int
    items: list[AnalisisResumen]


class Salud(BaseModel):
    estado: str
    base_de_datos: str
    version: str
