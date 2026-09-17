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

    class SimpleWorkerFalso:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def work(self, *, with_scheduler: bool) -> None:
            llamadas.append(with_scheduler)

    rq_falso = ModuleType("rq")
    rq_falso.SimpleWorker = SimpleWorkerFalso  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "rq", rq_falso)
    monkeypatch.setattr(worker, "usa_redis", lambda: True)
    monkeypatch.setattr(store_redis, "hay_redis_disponible", lambda: True)
    monkeypatch.setattr(store_redis, "_cliente", lambda **_kwargs: object())

    assert worker.main() == 0
    assert llamadas == [True]


def test_el_worker_usa_simpleworker_y_no_spawnworker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresión: SpawnWorker de rq==2.11.0 no funciona en Windows.

    Verificado en vivo, no leído de la documentación de RQ: falla por dos
    vías independientes. El padre espera al hijo con `os.wait4()`, que no
    existe en Windows (`AttributeError`). El hijo se lanza con `os.spawnv()`
    pasándole el script inline, y el escapado de línea de comandos de
    Windows lo rompe antes de que llegue a ejecutarse (`SyntaxError` en
    `import os`) — el hijo muere sin correr una sola línea del análisis.

    El síntoma en la API es silencioso: `POST /analyses` sigue devolviendo
    202, y el análisis queda en `pendiente` para siempre, sin error visible.
    Ese silencio es la razón para tener este test — nada más en la suite lo
    detectaría, porque el worker real no corre en CI (`ubuntu-latest`, donde
    `os.fork()` sí existe y el bug no aparece).

    `SimpleWorker` corre el job en el mismo proceso: sin fork ni spawn, así
    que ningún llamado POSIX-only queda en el camino en ninguna plataforma.
    """
    from apps.api import store_redis
    from apps.jobs import worker

    construido: list[type] = []

    class SimpleWorkerFalso:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            construido.append(type(self))

        def work(self, *, with_scheduler: bool) -> None:
            pass

    rq_falso = ModuleType("rq")
    rq_falso.SimpleWorker = SimpleWorkerFalso  # type: ignore[attr-defined]

    def spawn_worker_prohibido(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError(
            "el worker no debe instanciar SpawnWorker: falla en Windows "
            "(ver docstring de apps/jobs/worker.py)"
        )

    rq_falso.SpawnWorker = spawn_worker_prohibido  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "rq", rq_falso)
    monkeypatch.setattr(worker, "usa_redis", lambda: True)
    monkeypatch.setattr(store_redis, "hay_redis_disponible", lambda: True)
    monkeypatch.setattr(store_redis, "_cliente", lambda **_kwargs: object())

    assert worker.main() == 0
    assert construido == [SimpleWorkerFalso]
