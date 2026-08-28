"""Regresiones del ciclo de vida concurrente de los análisis."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent.llm import ClienteLLM
from application.analisis import procesar_analisis
from application.models import Analisis, EstadoAnalisis
from apps.api.schemas import Analisis as AnalisisAPI
from apps.api.store import AlmacenAnalisis


def _analisis(id_: str = "req-lifecycle") -> Analisis:
    return Analisis(
        id=id_,
        estado=EstadoAnalisis.PENDIENTE,
        creado_en=datetime(2026, 8, 28, 12, 0, 0),
        consulta="Comparar P001",
        product_ids=["P001"],
        desde=date(2026, 1, 1),
        hasta=date(2026, 3, 31),
    )


def test_los_esquemas_http_reexportan_el_modelo_de_aplicacion() -> None:
    assert AnalisisAPI is Analisis


def test_el_almacen_no_expone_mutabilidad_compartida() -> None:
    almacen = AlmacenAnalisis()
    original = _analisis()
    almacen.guardar(original)

    original.product_ids.append("P999")
    recuperado = almacen.obtener(original.id)
    assert recuperado is not None
    recuperado.estado = EstadoAnalisis.FALLIDO
    recuperado.product_ids.append("P998")

    _, listado = almacen.listar()
    listado[0].advertencias.append("mutación externa")

    persistido = almacen.obtener(original.id)
    assert persistido is not None
    assert persistido.estado == EstadoAnalisis.PENDIENTE
    assert persistido.product_ids == ["P001"]
    assert persistido.advertencias == []


def test_solo_admite_transiciones_validas_y_versionadas() -> None:
    almacen = AlmacenAnalisis()
    almacen.guardar(_analisis())

    procesando = almacen.transicionar(
        "req-lifecycle",
        desde=EstadoAnalisis.PENDIENTE,
        hacia=EstadoAnalisis.PROCESANDO,
    )
    assert procesando is not None
    assert procesando.estado == EstadoAnalisis.PROCESANDO
    assert procesando.version == 1

    obsoleto = almacen.transicionar(
        "req-lifecycle",
        desde=EstadoAnalisis.PROCESANDO,
        hacia=EstadoAnalisis.COMPLETADO,
        version_esperada=0,
    )
    assert obsoleto is None

    invalido = almacen.transicionar(
        "req-lifecycle",
        desde=EstadoAnalisis.PENDIENTE,
        hacia=EstadoAnalisis.COMPLETADO,
    )
    assert invalido is None

    completado = almacen.transicionar(
        "req-lifecycle",
        desde=EstadoAnalisis.PROCESANDO,
        hacia=EstadoAnalisis.COMPLETADO,
        version_esperada=procesando.version,
    )
    assert completado is not None
    assert completado.version == 2


def test_delete_durante_el_procesamiento_no_resucita_el_recurso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    almacen = AlmacenAnalisis()
    almacen.guardar(_analisis())

    def borrar_mientras_corre(*_args: Any, **_kwargs: Any) -> Any:
        en_curso = almacen.obtener("req-lifecycle")
        assert en_curso is not None
        assert en_curso.estado == EstadoAnalisis.PROCESANDO
        assert almacen.eliminar("req-lifecycle") is True
        return SimpleNamespace(
            informe=None,
            intencion=None,
            entidades=["P001"],
            periodo=None,
            trace=[],
            advertencias=[],
        )

    monkeypatch.setattr("application.analisis.ejecutar_grafo", borrar_mientras_corre)
    monkeypatch.setattr("application.analisis.cargar_indice", lambda: None)

    procesar_analisis("req-lifecycle", cliente=cast(ClienteLLM, object()), almacen=almacen)

    assert almacen.obtener("req-lifecycle") is None
    assert almacen.listar() == (0, [])
    # La segunda eliminación ve el tombstone y no vuelve a "eliminar" nada.
    assert almacen.eliminar("req-lifecycle") is False

