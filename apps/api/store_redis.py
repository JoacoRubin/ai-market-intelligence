"""Almacén de análisis sobre Redis.

Implementa el mismo puerto que `AlmacenAnalisis` (memoria) — `guardar`,
`obtener`, `listar`, `eliminar`, `limpiar` — y existe por lo que el almacén
en memoria NO puede hacer y su propio docstring ya anticipaba: sobrevivir a
un reinicio y compartirse entre procesos.

Eso segundo es lo que lo vuelve obligatorio en la Fase 6, no un lujo: desde
que el análisis corre en un **worker aparte**, el proceso que escribe el
resultado no es el que atiende el `GET`. Con el almacén en memoria, la API
respondería 404 sobre un análisis que el worker terminó hace rato.

## La forma de los datos

Dos claves por almacén, no una:

  - `<prefijo>:<id>`  → el análisis serializado a JSON.
  - `<prefijo>:index` → un **sorted set** con los ids, puntuados por el
    timestamp de creación.

El índice va aparte porque `listar()` tiene que devolver el más nuevo
primero y paginar. Resolverlo con `KEYS <prefijo>:*` sería más corto y
sería un error: `KEYS` recorre el keyspace entero y bloquea a Redis
mientras lo hace, así que el costo crece con TODO lo que haya en la
instancia, no con lo que este almacén guardó. El sorted set ordena y pagina
en el servidor, que es exactamente para lo que existe.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from application.lifecycle import TransicionInvalida, aplicar_transicion
from application.models import Analisis, EstadoAnalisis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
PREFIJO_POR_DEFECTO = "ami:analisis"

# Los análisis no viven para siempre. 7 días es holgado para una pieza de
# portfolio —cubre una demo, una entrevista y volver a mirarla— y evita que
# una instancia de Redis de larga vida acumule informes para siempre. El
# almacén en memoria resuelve lo mismo desalojando el más viejo; acá lo hace
# el TTL, que es la herramienta que Redis ya trae.
TTL_SEGUNDOS = 7 * 24 * 60 * 60


def _cliente(url: str = REDIS_URL, decodificar: bool = True) -> Any:
    """Construye el cliente de Redis.

    **`decodificar` NO es una preferencia de estilo: los dos valores son
    obligatorios, cada uno para su consumidor.**

    - `True` (este almacén): lo que vuelve es `str` y no `bytes`. Los ids
      que salen del índice se usan para armar claves, y un `b'req-abc'`
      mezclado con strings produce claves que no coinciden con las que se
      escribieron — un bug que no falla, solo devuelve vacío.

    - `False` (RQ, ver `apps/jobs/cola.py`): **RQ es incompatible con
      `decode_responses=True`.** Decodifica él mismo lo que lee, así que con
      un cliente que ya decodificó revienta con
      `AttributeError: 'str' object has no attribute 'decode'` — y no al
      conectar, sino al consumir el primer job. Verificado contra rq 2.11.0
      levantando el stack: el worker arrancaba, decía "Listening on
      analisis" y se caía en loop recién cuando entraba trabajo.

    Por eso hay dos clientes y no uno compartido. Es la clase de detalle que
    parece duplicación al leerlo y es lo contrario: un solo cliente sirve a
    uno de los dos y rompe al otro.
    """
    import redis

    return redis.Redis.from_url(url, decode_responses=decodificar)


def hay_redis_disponible(url: str = REDIS_URL) -> bool:
    """Indica si hay un Redis respondiendo.

    Vive suelta y no en la clase por el mismo motivo que `ollama_responde` y
    `hay_base_disponible`: la pregunta no depende de CÓMO se lo use. La usan
    los tests para saltarse solos cuando no hay servidor, en vez de fallar y
    hacer creer que se rompió el almacén.
    """
    try:
        _cliente(url).ping()
        return True
    except Exception:
        return False


class AlmacenRedis:
    """Almacén de análisis persistido en Redis."""

    def __init__(
        self,
        url: str = REDIS_URL,
        prefijo: str = PREFIJO_POR_DEFECTO,
        ttl_segundos: int = TTL_SEGUNDOS,
    ) -> None:
        self._r = _cliente(url)
        self._prefijo = prefijo
        self._ttl = ttl_segundos

    # --- claves --------------------------------------------------------------

    def _clave(self, id_: str) -> str:
        return f"{self._prefijo}:{id_}"

    @property
    def _indice(self) -> str:
        return f"{self._prefijo}:index"

    # --- el puerto -----------------------------------------------------------

    def guardar(self, analisis: Analisis) -> None:
        """Persiste el análisis y lo indexa.

        Las dos escrituras van en un `pipeline` para que viajen juntas: si el
        dato se escribiera y el índice no, el análisis existiría pero no
        aparecería en `listar()` — invisible sin estar ausente, que es la
        clase de inconsistencia más difícil de diagnosticar.
        """
        from redis.exceptions import WatchError

        clave = self._clave(analisis.id)
        while True:
            try:
                with self._r.pipeline() as pipe:
                    pipe.watch(clave)
                    crudo = pipe.get(clave)
                    copia = analisis.model_copy(deep=True)
                    if crudo is not None:
                        actual = Analisis.model_validate_json(crudo)
                        if actual.estado == EstadoAnalisis.CANCELADO:
                            raise RuntimeError(
                                f"el análisis {analisis.id} fue cancelado"
                            )
                        if copia.version != actual.version:
                            raise RuntimeError(
                                f"versión obsoleta para {analisis.id}: "
                                f"{copia.version} != {actual.version}"
                            )
                        copia.version = actual.version + 1

                    pipe.multi()
                    pipe.set(clave, copia.model_dump_json(), ex=self._ttl)
                    # El puntaje es el instante de creación: actualizar estado
                    # nunca reordena el listado.
                    pipe.zadd(
                        self._indice,
                        {copia.id: copia.creado_en.timestamp()},
                    )
                    pipe.expire(self._indice, self._ttl)
                    pipe.execute()
                    return
            except WatchError:
                # Otro proceso ganó la carrera: releer y volver a comparar.
                continue

    def obtener(self, id_: str) -> Analisis | None:
        crudo = self._r.get(self._clave(id_))
        if crudo is None:
            return None
        analisis = Analisis.model_validate_json(crudo)
        if analisis.estado == EstadoAnalisis.CANCELADO:
            return None
        return analisis

    def listar(self, limite: int = 50, offset: int = 0) -> tuple[int, list[Analisis]]:
        """Devuelve (total, página), del más nuevo al más viejo.

        `zrevrange` ordena y pagina en el servidor. El total sale de `zcard`,
        no de contar lo que se trajo: son números distintos en cuanto hay
        paginación, y el contrato de `ListaAnalisis` promete el total real.
        """
        total = int(self._r.zcard(self._indice))
        ids = self._r.zrevrange(self._indice, offset, offset + limite - 1)

        items: list[Analisis] = []
        for id_ in ids:
            analisis = self.obtener(id_)
            # Puede ser None si al análisis se le venció el TTL y al índice
            # todavía no: se saltea en vez de romper el listado entero. El
            # índice se limpia solo en el próximo `expire`.
            if analisis is not None:
                items.append(analisis)
        return total, items

    def transicionar(
        self,
        id_: str,
        *,
        desde: EstadoAnalisis,
        hacia: EstadoAnalisis,
        version_esperada: int | None = None,
        cambios: Mapping[str, Any] | None = None,
    ) -> Analisis | None:
        """CAS distribuido mediante WATCH/MULTI/EXEC."""
        from redis.exceptions import WatchError

        clave = self._clave(id_)
        while True:
            try:
                with self._r.pipeline() as pipe:
                    pipe.watch(clave)
                    crudo = pipe.get(clave)
                    if crudo is None:
                        return None
                    actual = Analisis.model_validate_json(crudo)
                    if actual.estado != desde:
                        return None
                    if (
                        version_esperada is not None
                        and actual.version != version_esperada
                    ):
                        return None
                    try:
                        siguiente = aplicar_transicion(actual, hacia, cambios)
                    except TransicionInvalida:
                        return None

                    pipe.multi()
                    pipe.set(
                        clave, siguiente.model_dump_json(), ex=self._ttl
                    )
                    if hacia == EstadoAnalisis.CANCELADO:
                        pipe.zrem(self._indice, id_)
                    else:
                        pipe.zadd(
                            self._indice,
                            {id_: siguiente.creado_en.timestamp()},
                        )
                        pipe.expire(self._indice, self._ttl)
                    pipe.execute()
                    return siguiente.model_copy(deep=True)
            except WatchError:
                continue

    def eliminar(self, id_: str) -> bool:
        """Deja un tombstone y lo quita del índice de recursos visibles."""
        from redis.exceptions import WatchError

        clave = self._clave(id_)
        while True:
            try:
                with self._r.pipeline() as pipe:
                    pipe.watch(clave)
                    crudo = pipe.get(clave)
                    if crudo is None:
                        return False
                    actual = Analisis.model_validate_json(crudo)
                    if actual.estado == EstadoAnalisis.CANCELADO:
                        return False
                    cancelado = aplicar_transicion(
                        actual, EstadoAnalisis.CANCELADO
                    )
                    pipe.multi()
                    pipe.set(
                        clave, cancelado.model_dump_json(), ex=self._ttl
                    )
                    pipe.zrem(self._indice, id_)
                    pipe.execute()
                    return True
            except WatchError:
                continue

    def limpiar(self) -> None:
        """Borra todo lo de ESTE prefijo. No toca el resto del keyspace.

        Se usa en los tests. Recorre el índice en vez de hacer `FLUSHDB`
        justamente porque un almacén no tiene por qué poder vaciar una base
        que comparte con otros — y en desarrollo esa base suele ser la misma
        que usa la cola de trabajos.
        """
        # Los cancelados son tombstones fuera del índice visible; SCAN permite
        # limpiarlos también sin bloquear Redis como haría KEYS.
        claves = list(self._r.scan_iter(match=f"{self._prefijo}:*"))
        pipe = self._r.pipeline()
        for clave in claves:
            pipe.delete(clave)
        pipe.execute()
