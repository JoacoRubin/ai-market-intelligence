"""Semántica distinta entre BackgroundTasks y el job durable de RQ."""

from __future__ import annotations

import sys
from datetime import datetime
from types import ModuleType
from typing import Any, cast

import pytest

from agent.llm import ClienteLLM
from application.models import Analisis, EstadoAnalisis
from apps.api.store import AlmacenAnalisis
from apps.jobs import tareas


def _pendiente(id_: str) -> Analisis:
    return Analisis(
        id=id_,
        estado=EstadoAnalisis.PENDIENTE,
        creado_en=datetime(2026, 8, 28, 12, 0, 0),
        consulta="analizar P001",
        product_ids=["P001"],
    )


def _grafo_que_falla(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("fallo transitorio")


def test_background_persiste_el_fallo_sin_propagarlo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    almacen = AlmacenAnalisis()
    almacen.guardar(_pendiente("req-background"))
    monkeypatch.setattr("application.analisis.ejecutar_grafo", _grafo_que_falla)
    monkeypatch.setattr("application.analisis.cargar_indice", lambda: None)

    tareas.procesar_analisis("req-background", cast(ClienteLLM, object()), almacen)

    resultado = almacen.obtener("req-background")
    assert resultado is not None
    assert resultado.estado == EstadoAnalisis.FALLIDO
    assert resultado.error == "RuntimeError: fallo transitorio"


def test_rq_persiste_el_fallo_y_lo_propaga_para_que_rq_lo_marque_fallido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    almacen = AlmacenAnalisis()
    almacen.guardar(_pendiente("req-rq"))
    monkeypatch.setattr("application.analisis.ejecutar_grafo", _grafo_que_falla)
    monkeypatch.setattr("application.analisis.cargar_indice", lambda: None)
    monkeypatch.setattr(tareas, "crear_cliente", lambda: object())
    monkeypatch.setattr(tareas, "crear_almacen", lambda: almacen)

    with pytest.raises(RuntimeError, match="fallo transitorio"):
        tareas.ejecutar_analisis("req-rq")

    resultado = almacen.obtener("req-rq")
    assert resultado is not None
    assert resultado.estado == EstadoAnalisis.FALLIDO
    assert resultado.error == "RuntimeError: fallo transitorio"


def test_el_despacho_rq_configura_reintentos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.jobs import cola

    class ColaFalsa:
        kwargs: dict[str, Any]

        def enqueue(self, *_args: Any, **kwargs: Any) -> None:
            self.kwargs = kwargs

    cola_falsa = ColaFalsa()
    monkeypatch.setattr(cola, "usa_redis", lambda: True)
    monkeypatch.setattr(cola, "obtener_cola", lambda: cola_falsa)
    monkeypatch.setattr(
        cola,
        "_crear_retry",
        lambda: type("RetryFalso", (), {"max": cola.REINTENTOS_JOB})(),
    )

    assert cola.despachar("req-rq", object(), object(), object()) == "redis"
    assert cola_falsa.kwargs["retry"].max == cola.REINTENTOS_JOB


def test_el_worker_activa_scheduler_para_los_reintentos_diferidos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api import store_redis
    from apps.jobs import worker

    llamadas: list[bool] = []

    class SpawnWorkerFalso:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def work(self, *, with_scheduler: bool) -> None:
            llamadas.append(with_scheduler)

    rq_falso = ModuleType("rq")
    rq_falso.SpawnWorker = SpawnWorkerFalso  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "rq", rq_falso)
    monkeypatch.setattr(worker, "usa_redis", lambda: True)
    monkeypatch.setattr(store_redis, "hay_redis_disponible", lambda: True)
    monkeypatch.setattr(store_redis, "_cliente", lambda **_kwargs: object())

    assert worker.main() == 0
    assert llamadas == [True]
