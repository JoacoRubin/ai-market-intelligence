"""El proceso que consume la cola de análisis.

    python -m apps.jobs.worker

Corre aparte de la API, y ese es todo el punto: mientras el análisis vivía
en un `BackgroundTask`, los 70-95 segundos de una corrida (ADR-003) se
gastaban dentro del proceso que atiende HTTP, y un reinicio en el medio
perdía el trabajo sin dejar rastro.

## Por qué SimpleWorker y no SpawnWorker

Se probó `SpawnWorker` primero — la elección documentada originalmente en
este archivo y en ADR-012 — con el razonamiento de que `os.fork()` no existe
en Windows y `SpawnWorker` sí corre en los dos sistemas. Resultó ser falso:
verificado en vivo, `SpawnWorker` de `rq==2.11.0` falla en Windows por DOS
vías independientes, ninguna cosmética.

1. El proceso padre espera al hijo con `os.wait4()` (heredado de la clase
   `Worker` base, no reimplementado): `AttributeError: module 'os' has no
   attribute 'wait4'` — esa función no existe en Windows.
2. El hijo se lanza con `os.spawnv()` pasándole el script como un string
   multilínea inline. En Windows, `os.spawnv()` arma la línea de comandos
   concatenando argumentos, y el escapado rompe el script: `import os`
   llegaba partido en dos (`SyntaxError: Expected one or more names after
   'import'`), así que el hijo moría antes de ejecutar una sola línea del
   análisis.

Ninguno de los dos aparece en Linux (Docker, CI): `os.fork()`, `os.wait4()`
y el spawn de `multiprocessing` sí existen ahí. Pero este worker está
documentado para correr a mano contra el `.venv` local en la máquina de
desarrollo, que es Windows — y ahí un `POST /analyses` con `JOBS_BACKEND=redis`
quedaba en `pendiente` para siempre, sin error, sin ninguna señal de que el
worker había muerto en el primer job.

`SimpleWorker` corre el job en el mismo proceso del worker, sin fork ni
spawn: ningún llamado POSIX-only en el camino. Sostiene el mismo principio
que ya motivaba `SpawnWorker` — el mismo comportamiento en Windows y en
Linux, no un worker que se prueba en un sistema y se rompe en otro — solo
que ahora ese comportamiento común es "un proceso, sin aislamiento por job"
en los dos lados, no "un proceso por job" en los dos lados. Se pierde el
aislamiento (un job que reviente el intérprete se lleva puesto al worker
entero), que en este proyecto es un riesgo bajo: `ejecutar_analisis` es
Python síncrono que llama a SQL, FAISS y HTTP a Ollama, no código que
debería segfaultear al intérprete. El límite de tiempo por job lo sigue
imponiendo `death_penalty_class` de RQ (ya con `TimerDeathPenalty` en
Windows, sin depender de señales), independiente de qué clase de worker
ejecute el trabajo.
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

    from rq import SimpleWorker

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
    SimpleWorker(
        [NOMBRE_COLA], connection=_cliente(decodificar=False)
    # Los retries con intervalo quedan en ScheduledJobRegistry. Sin scheduler
    # el primer fallo se persiste, pero NUNCA vuelve a la cola.
    ).work(with_scheduler=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
