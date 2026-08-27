"""El proceso que consume la cola de análisis.

    python -m apps.jobs.worker

Corre aparte de la API, y ese es todo el punto: mientras el análisis vivía
en un `BackgroundTask`, los 70-95 segundos de una corrida (ADR-003) se
gastaban dentro del proceso que atiende HTTP, y un reinicio en el medio
perdía el trabajo sin dejar rastro.

## Por qué SpawnWorker y no el Worker por defecto

El `Worker` clásico de RQ hace `os.fork()` por cada job — y `fork()` **no
existe en Windows**, que es la máquina de desarrollo de este proyecto. RQ
trae `SpawnWorker`, que usa `multiprocessing.spawn` y funciona en los dos
sistemas.

Se elige SIEMPRE, no solo en Windows, y es deliberado: un worker que se
comporta distinto según el sistema operativo es un worker que se prueba en
uno y se rompe en el otro. Que el contenedor corra lo mismo que la máquina
de desarrollo vale más que la eficiencia de `fork`, sobre todo cuando cada
job dura minutos y el costo de arrancar el proceso es ruido al lado.
"""

from __future__ import annotations

import logging
import sys

from apps.jobs.cola import NOMBRE_COLA, usa_redis


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("worker")

    if not usa_redis():
        # Falla ruidoso en vez de quedarse escuchando una cola que nadie
        # llena. Un worker "arriba" que no puede recibir trabajo es el peor
        # de los estados: parece sano en `docker ps` y no procesa nada.
        log.error(
            "JOBS_BACKEND no es 'redis': este worker no tendría de dónde "
            "tomar trabajo. Levantalo con JOBS_BACKEND=redis."
        )
        return 1

    from rq import SpawnWorker

    from apps.api.store_redis import REDIS_URL, _cliente, hay_redis_disponible

    if not hay_redis_disponible():
        # Mismo criterio: sin Redis, decirlo ahora y con la URL a la vista.
        # RQ fallaría igual, pero con una traza de conexión que no dice cuál
        # era la URL ni que el problema es de configuración.
        log.error("no hay Redis respondiendo en %s", REDIS_URL)
        return 1

    log.info("worker escuchando la cola %r en %s", NOMBRE_COLA, REDIS_URL)
    # decodificar=False: RQ no es compatible con decode_responses=True. Ver
    # `apps/api/store_redis.py::_cliente` — con el cliente equivocado el
    # worker arranca, dice "Listening on ..." y recién se cae cuando entra
    # el primer job.
    SpawnWorker(
        [NOMBRE_COLA], connection=_cliente(decodificar=False)
    ).work(with_scheduler=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
