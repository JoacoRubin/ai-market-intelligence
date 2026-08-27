"""Tests del almacén de análisis sobre Redis y del selector de backend.

Dos grupos con reglas distintas, a propósito:

  - Los del **selector** (`crear_almacen`) corren siempre: no tocan Redis,
    solo verifican qué implementación se elige según `JOBS_BACKEND`. Ese es
    el punto donde un typo en la variable manda el sistema al backend
    equivocado en silencio, así que no puede depender de tener Redis arriba.

  - Los del **almacén contra Redis** están marcados `redis` y se saltean
    solos si no hay servidor. Mismo criterio que los marcados `db` con SQL
    Server: un test que falla por falta de infraestructura enseña a ignorar
    el rojo, y esa es la peor clase de test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, datetime

import pytest

from apps.api.schemas import Analisis, EstadoAnalisis
from apps.api.store import AlmacenAnalisis, crear_almacen
from apps.api.store_redis import AlmacenRedis, hay_redis_disponible


def _analisis(id_: str, creado: datetime | None = None) -> Analisis:
    return Analisis(
        id=id_,
        estado=EstadoAnalisis.PENDIENTE,
        creado_en=creado or datetime(2026, 8, 27, 12, 0, 0),
        consulta=f"consulta de {id_}",
        product_ids=["P001"],
        desde=date(2026, 1, 1),
        hasta=date(2026, 6, 30),
    )


# --- el selector: qué backend se construye -----------------------------------


def test_sin_variable_devuelve_el_almacen_en_memoria(monkeypatch: pytest.MonkeyPatch) -> None:
    """El default es memoria: sin Redis el sistema tiene que seguir andando."""
    monkeypatch.delenv("JOBS_BACKEND", raising=False)
    assert isinstance(crear_almacen(), AlmacenAnalisis)


def test_memoria_explicito_devuelve_el_almacen_en_memoria(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOBS_BACKEND", "memoria")
    assert isinstance(crear_almacen(), AlmacenAnalisis)


def test_un_backend_que_no_existe_falla_al_construir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falla RUIDOSO y no cae al default.

    Un `JOBS_BACKEND=redis_` con un typo que cayera silenciosamente a memoria
    daría un sistema que parece distribuido y no lo es: los análisis se
    perderían al reiniciar y nadie sabría por qué. Es el mismo criterio que
    ya usa `crear_cliente()` con LLM_BACKEND.
    """
    monkeypatch.setenv("JOBS_BACKEND", "redis_")
    with pytest.raises(ValueError, match="JOBS_BACKEND"):
        crear_almacen()


def test_el_backend_se_lee_sin_distinguir_mayusculas_ni_espacios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOBS_BACKEND", "  MEMORIA  ")
    assert isinstance(crear_almacen(), AlmacenAnalisis)


# --- el almacén contra Redis de verdad ---------------------------------------

requiere_redis = pytest.mark.skipif(
    not hay_redis_disponible(),
    reason="no hay Redis escuchando (docker compose up -d redis)",
)


@pytest.fixture
def almacen_redis() -> Iterator[AlmacenRedis]:
    """Un almacén con prefijo propio, limpiado antes y después.

    El prefijo por test evita que dos corridas simultáneas —o una corrida
    contra un Redis con datos de desarrollo— se pisen entre sí.
    """
    almacen = AlmacenRedis(prefijo=f"test:{os.getpid()}:analisis")
    almacen.limpiar()
    yield almacen
    almacen.limpiar()


@pytest.mark.redis
@requiere_redis
def test_guarda_y_recupera_un_analisis(almacen_redis: AlmacenRedis) -> None:
    original = _analisis("req-aaa")
    almacen_redis.guardar(original)

    recuperado = almacen_redis.obtener("req-aaa")
    assert recuperado is not None
    assert recuperado.id == "req-aaa"
    assert recuperado.consulta == "consulta de req-aaa"
    assert recuperado.estado == EstadoAnalisis.PENDIENTE
    # Las fechas tienen que sobrevivir el viaje por JSON: si volvieran como
    # str, el PDF y la comparación de períodos fallarían recién al usarlas.
    assert recuperado.desde == date(2026, 1, 1)
    assert recuperado.creado_en == original.creado_en


@pytest.mark.redis
@requiere_redis
def test_obtener_lo_que_no_existe_devuelve_none(almacen_redis: AlmacenRedis) -> None:
    assert almacen_redis.obtener("req-no-existe") is None


@pytest.mark.redis
@requiere_redis
def test_guardar_dos_veces_actualiza_en_vez_de_duplicar(almacen_redis: AlmacenRedis) -> None:
    registro = _analisis("req-bbb")
    almacen_redis.guardar(registro)

    registro.estado = EstadoAnalisis.COMPLETADO
    almacen_redis.guardar(registro)

    total, items = almacen_redis.listar()
    assert total == 1
    assert items[0].estado == EstadoAnalisis.COMPLETADO


@pytest.mark.redis
@requiere_redis
def test_listar_devuelve_el_mas_nuevo_primero(almacen_redis: AlmacenRedis) -> None:
    """Mismo orden que el almacén en memoria.

    Los dos implementan el mismo puerto, así que si el orden difiere, cambiar
    de backend le cambia la respuesta a `GET /analyses` sin que nadie toque
    un handler.
    """
    for i in range(3):
        almacen_redis.guardar(
            _analisis(f"req-{i}", datetime(2026, 8, 27, 12, i, 0))
        )

    total, items = almacen_redis.listar()
    assert total == 3
    assert [a.id for a in items] == ["req-2", "req-1", "req-0"]


@pytest.mark.redis
@requiere_redis
def test_listar_pagina_igual_que_el_almacen_en_memoria(almacen_redis: AlmacenRedis) -> None:
    for i in range(5):
        almacen_redis.guardar(
            _analisis(f"req-{i}", datetime(2026, 8, 27, 12, i, 0))
        )

    total, items = almacen_redis.listar(limite=2, offset=1)
    assert total == 5
    assert [a.id for a in items] == ["req-3", "req-2"]


@pytest.mark.redis
@requiere_redis
def test_eliminar_saca_el_analisis_y_lo_dice(almacen_redis: AlmacenRedis) -> None:
    almacen_redis.guardar(_analisis("req-ccc"))

    assert almacen_redis.eliminar("req-ccc") is True
    assert almacen_redis.obtener("req-ccc") is None
    # Segunda vez devuelve False: el endpoint DELETE distingue 204 de 404 con
    # este booleano, así que no puede mentir.
    assert almacen_redis.eliminar("req-ccc") is False


# --- la trampa de decode_responses ------------------------------------------


@pytest.mark.redis
@requiere_redis
def test_el_almacen_usa_un_cliente_que_decodifica() -> None:
    """El almacén necesita `str`, no `bytes`.

    Los ids que salen del índice se usan para armar claves. Con `bytes`,
    `f"{prefijo}:{id_}"` produce `ami:analisis:b'req-abc'` — una clave que
    no coincide con ninguna de las escritas, así que `obtener()` devolvería
    None sin que nada falle.
    """
    from apps.api.store_redis import _cliente

    assert _cliente().get_connection_kwargs()["decode_responses"] is True


@pytest.mark.redis
@requiere_redis
def test_la_cola_de_rq_usa_un_cliente_que_NO_decodifica() -> None:
    """RQ es incompatible con `decode_responses=True`.

    Decodifica él mismo lo que lee, así que con un cliente que ya decodificó
    revienta con `AttributeError: 'str' object has no attribute 'decode'`.

    Este test existe porque el bug **no aparece al conectar**: el worker
    arranca, loguea "Listening on analisis" y se cae recién al consumir el
    primer job — en un loop de reinicio que desde afuera parece un problema
    de red. Costó levantar el stack entero para verlo; que no haga falta de
    nuevo.
    """
    from apps.jobs.cola import obtener_cola

    cola = obtener_cola()
    assert cola.connection.get_connection_kwargs()["decode_responses"] is False


@pytest.mark.redis
@requiere_redis
def test_eliminar_tambien_lo_saca_del_listado(almacen_redis: AlmacenRedis) -> None:
    """El índice y los datos se borran juntos.

    Si `eliminar` solo borrase la clave del análisis, el índice quedaría con
    un id fantasma y `listar` intentaría leer algo que ya no está.
    """
    almacen_redis.guardar(_analisis("req-ddd"))
    almacen_redis.guardar(_analisis("req-eee", datetime(2026, 8, 27, 13, 0, 0)))

    almacen_redis.eliminar("req-ddd")

    total, items = almacen_redis.listar()
    assert total == 1
    assert [a.id for a in items] == ["req-eee"]
