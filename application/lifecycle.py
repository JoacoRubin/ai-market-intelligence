"""Reglas puras de transición del recurso análisis."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from application.models import Analisis, EstadoAnalisis


class TransicionInvalida(ValueError):
    """La transición pedida no pertenece a la máquina de estados."""


TRANSICIONES_VALIDAS: dict[EstadoAnalisis, frozenset[EstadoAnalisis]] = {
    EstadoAnalisis.PENDIENTE: frozenset(
        {EstadoAnalisis.PROCESANDO, EstadoAnalisis.CANCELADO}
    ),
    EstadoAnalisis.PROCESANDO: frozenset(
        {
            EstadoAnalisis.COMPLETADO,
            EstadoAnalisis.FALLIDO,
            EstadoAnalisis.CANCELADO,
        }
    ),
    # RQ vuelve a reclamar un fallo transitorio cuando ejecuta un retry.
    EstadoAnalisis.FALLIDO: frozenset(
        {EstadoAnalisis.PROCESANDO, EstadoAnalisis.CANCELADO}
    ),
    EstadoAnalisis.COMPLETADO: frozenset({EstadoAnalisis.CANCELADO}),
    EstadoAnalisis.CANCELADO: frozenset(),
}

_CAMPOS_INMUTABLES = frozenset({"id", "creado_en", "estado", "version"})


def aplicar_transicion(
    actual: Analisis,
    hacia: EstadoAnalisis,
    cambios: Mapping[str, Any] | None = None,
) -> Analisis:
    """Devuelve una copia versionada o falla si la transición es inválida."""
    if hacia not in TRANSICIONES_VALIDAS[actual.estado]:
        raise TransicionInvalida(f"{actual.estado.value} -> {hacia.value}")

    cambios = cambios or {}
    prohibidos = _CAMPOS_INMUTABLES.intersection(cambios)
    if prohibidos:
        raise TransicionInvalida(
            f"una transición no puede cambiar {sorted(prohibidos)}"
        )

    siguiente = actual.model_copy(deep=True)
    for nombre, valor in cambios.items():
        if nombre not in type(siguiente).model_fields:
            raise TransicionInvalida(f"campo de análisis desconocido: {nombre}")
        setattr(siguiente, nombre, copy.deepcopy(valor))
    siguiente.estado = hacia
    siguiente.version = actual.version + 1
    return siguiente

