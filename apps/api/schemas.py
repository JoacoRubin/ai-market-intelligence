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


class SolicitudAnalisis(BaseModel):
    """Cuerpo del POST /analyses. Admite dos formas excluyentes.

    **Estructurada** (`product_ids` + `desde` + `hasta`): ya se sabe qué se
    quiere, así que el router del agente se saltea. En esta máquina eso son ~77
    segundos de inferencia que no aportan nada.

    **Lenguaje natural** (`consulta`): interviene el agente completo, empezando
    por interpretar qué se está pidiendo.

    Mandar las dos deja ambiguo cuál manda. Se rechaza en vez de elegir una en
    silencio: que el usuario descubra después cuál se usó es peor que un 422.
    """

    consulta: str | None = Field(
        default=None, min_length=3, max_length=500,
        description="Consulta en lenguaje natural. Excluyente con product_ids.",
    )
    product_ids: list[str] | None = Field(
        default=None, max_length=MAX_PRODUCTOS_POR_ANALISIS,
        description="Identificadores de producto. Requiere desde y hasta.",
    )
    desde: date | None = None
    hasta: date | None = None

    @model_validator(mode="after")
    def _forma_coherente(self) -> SolicitudAnalisis:
        tiene_consulta = bool(self.consulta and self.consulta.strip())
        tiene_ids = bool(self.product_ids)

        if tiene_consulta and tiene_ids:
            raise ValueError(
                "enviá 'consulta' O 'product_ids', no ambos: con las dos formas "
                "presentes queda ambiguo cuál define el análisis"
            )
        if not tiene_consulta and not tiene_ids:
            raise ValueError(
                "hace falta 'consulta' (lenguaje natural) o 'product_ids' con "
                "'desde' y 'hasta'"
            )

        if tiene_ids:
            if self.desde is None or self.hasta is None:
                raise ValueError("'product_ids' requiere 'desde' y 'hasta'")
            if self.desde > self.hasta:
                raise ValueError(
                    f"el rango está invertido: 'desde' ({self.desde}) es "
                    f"posterior a 'hasta' ({self.hasta})"
                )
            if len(set(self.product_ids)) != len(self.product_ids):
                raise ValueError("hay productos repetidos en la solicitud")

        return self

    @property
    def es_estructurada(self) -> bool:
        return bool(self.product_ids)


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
    consulta: str
    product_ids: list[str] = Field(default_factory=list)
    desde: date | None = None
    hasta: date | None = None


class Analisis(AnalisisResumen):
    """Vista completa. `informe` sólo aparece cuando el estado es completado.

    `intencion` y `advertencias` exponen el trabajo del agente aunque no haya
    informe: una consulta fuera de alcance no produce análisis, pero el usuario
    merece saber por qué.
    """

    intencion: str | None = None
    informe: Report | None = None
    error: str | None = None
    etapas: list[str] = Field(default_factory=list)
    advertencias: list[str] = Field(default_factory=list)


class ListaAnalisis(BaseModel):
    total: int
    items: list[AnalisisResumen]


class Salud(BaseModel):
    estado: str
    base_de_datos: str
    version: str
