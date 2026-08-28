"""Modelo de aplicación del recurso análisis.

Estos tipos antes vivían junto a los DTO HTTP. Son compartidos por HTTP, el
worker y los almacenes, por lo que su dueño real es la capa de aplicación.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from core.report import Report


class EstadoAnalisis(StrEnum):
    """Estados persistidos de un análisis y su trabajo asociado."""

    PENDIENTE = "pendiente"
    PROCESANDO = "procesando"
    COMPLETADO = "completado"
    FALLIDO = "fallido"
    CANCELADO = "cancelado"


class AnalisisResumen(BaseModel):
    """Vista liviana del recurso."""

    id: str
    estado: EstadoAnalisis
    creado_en: datetime
    consulta: str
    product_ids: list[str] = Field(default_factory=list)
    desde: date | None = None
    hasta: date | None = None
    # Versión optimista. Cambia en cada transición atómica y evita que un
    # worker atrasado sobrescriba una cancelación o un resultado más nuevo.
    version: int = Field(default=0, ge=0)


class Analisis(AnalisisResumen):
    """Recurso completo producido por el caso de uso de análisis."""

    intencion: str | None = None
    informe: Report | None = None
    error: str | None = None
    etapas: list[str] = Field(default_factory=list)
    advertencias: list[str] = Field(default_factory=list)

