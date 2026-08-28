"""Puertos consumidos por los casos de uso de análisis."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from application.models import Analisis, EstadoAnalisis


@runtime_checkable
class Almacen(Protocol):
    """Persistencia con transición compare-and-set del ciclo de vida."""

    def guardar(self, analisis: Analisis) -> None: ...
    def obtener(self, id_: str) -> Analisis | None: ...
    def listar(
        self, limite: int = 50, offset: int = 0
    ) -> tuple[int, list[Analisis]]: ...
    def transicionar(
        self,
        id_: str,
        *,
        desde: EstadoAnalisis,
        hacia: EstadoAnalisis,
        version_esperada: int | None = None,
        cambios: Mapping[str, Any] | None = None,
    ) -> Analisis | None: ...
    def eliminar(self, id_: str) -> bool: ...
    def limpiar(self) -> None: ...

