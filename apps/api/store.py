"""Almacenamiento de análisis.

Hay dos implementaciones del mismo puerto y `crear_almacen()` elige entre
ellas con `JOBS_BACKEND` — la misma forma que ya usa `crear_cliente()` para
el modelo (ADR-007):

    JOBS_BACKEND=memoria  → AlmacenAnalisis  (default, este archivo)
    JOBS_BACKEND=redis    → AlmacenRedis     (apps/api/store_redis.py)

`AlmacenAnalisis` guarda en memoria del proceso: los análisis se pierden al
reiniciar y no se comparten entre procesos. Eso alcanzaba mientras el
análisis corría dentro de la misma API, y **deja de alcanzar en cuanto hay un
worker aparte** (ADR-012): el proceso que escribe el resultado ya no es el que
atiende el GET.

Sigue siendo el default igual, y no es por inercia: sin Redis el sistema tiene
que seguir levantándose con pocos comandos, y la suite no puede exigir
infraestructura para correr.

La nota original de este archivo pedía que el reemplazo fuera "sustituir esta
clase y nada más". Se cumplió: `AlmacenRedis` implementa los mismos cinco
métodos y ningún handler cambió.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from typing import Protocol, runtime_checkable

from apps.api.schemas import Analisis

MAX_ANALISIS_EN_MEMORIA = 200


@runtime_checkable
class Almacen(Protocol):
    """Lo que la API necesita de un almacén de análisis.

    Existe para que `main.py` no dependa de CUÁL de las dos
    implementaciones está corriendo. Es el mismo criterio que `ClienteLLM`:
    un puerto chico, definido por lo que el consumidor usa.
    """

    def guardar(self, analisis: Analisis) -> None: ...
    def obtener(self, id_: str) -> Analisis | None: ...
    def listar(
        self, limite: int = 50, offset: int = 0
    ) -> tuple[int, list[Analisis]]: ...
    def eliminar(self, id_: str) -> bool: ...
    def limpiar(self) -> None: ...


class AlmacenAnalisis:
    """Almacén en memoria con desalojo del más viejo.

    El límite existe para que un proceso de larga vida no crezca sin techo: sin
    él, cada análisis quedaría retenido para siempre y la memoria sería una
    fuga lenta que aparece recién en producción.
    """

    def __init__(self, capacidad: int = MAX_ANALISIS_EN_MEMORIA) -> None:
        self._datos: OrderedDict[str, Analisis] = OrderedDict()
        self._lock = threading.Lock()
        self._capacidad = capacidad

    def guardar(self, analisis: Analisis) -> None:
        with self._lock:
            self._datos[analisis.id] = analisis
            self._datos.move_to_end(analisis.id)
            while len(self._datos) > self._capacidad:
                self._datos.popitem(last=False)

    def obtener(self, id_: str) -> Analisis | None:
        with self._lock:
            return self._datos.get(id_)

    def listar(self, limite: int = 50, offset: int = 0) -> tuple[int, list[Analisis]]:
        with self._lock:
            todos = list(reversed(self._datos.values()))
        return len(todos), todos[offset:offset + limite]

    def eliminar(self, id_: str) -> bool:
        with self._lock:
            return self._datos.pop(id_, None) is not None

    def limpiar(self) -> None:
        with self._lock:
            self._datos.clear()


def crear_almacen() -> Almacen:
    """Construye el almacén según `JOBS_BACKEND`. Único lugar que elige.

    Que la decisión viva acá es lo que permite que los handlers no sepan
    cuál está corriendo — el mismo motivo por el que existe
    `crear_cliente()` para el modelo.

    Un valor desconocido **falla al construir** en vez de caer al default: un
    `JOBS_BACKEND=redis_` con un typo que cayera silenciosamente a memoria
    daría un sistema que parece distribuido y no lo es, y los análisis se
    perderían al reiniciar sin que nadie entienda por qué.
    """
    backend = os.getenv("JOBS_BACKEND", "memoria").strip().lower()
    if backend == "memoria":
        return AlmacenAnalisis()
    if backend == "redis":
        # Import diferido: `redis` es una dependencia opcional (grupo `jobs`)
        # y el camino por default no tiene por qué exigir que esté instalada.
        from apps.api.store_redis import AlmacenRedis

        return AlmacenRedis()
    raise ValueError(
        f"JOBS_BACKEND={backend!r} no existe. Valores válidos: "
        f"'memoria', 'redis'."
    )


almacen: Almacen = crear_almacen()
