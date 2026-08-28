"""DÓNDE se ejecuta un análisis: en este proceso o en un worker aparte.

Es el único lugar que decide, igual que `crear_cliente()` para el modelo y
`crear_almacen()` para el almacén. El handler de `POST /analyses` llama a
`despachar()` y no sabe cuál de los dos caminos tomó — ese es el punto.

    JOBS_BACKEND=memoria  → BackgroundTasks, en el proceso de la API (default)
    JOBS_BACKEND=redis    → cola de RQ, consumida por apps/jobs/worker.py
"""

from __future__ import annotations

import os
from typing import Any

NOMBRE_COLA = os.getenv("JOBS_COLA", "analisis")

# Techo de una corrida antes de que RQ la dé por muerta. Generoso a
# propósito: en el hardware de referencia (CPU-only, ADR-003) una corrida
# real tarda entre 70 y 95 segundos, y una comparación de dos productos
# llegó a 4,7 minutos en las capturas del replay. Un timeout de 180s —el
# default de RQ— mataría análisis legítimos y los reportaría como fallidos,
# que es peor que tardar: enseña a desconfiar del sistema cuando funciona.
TIMEOUT_JOB_SEGUNDOS = int(os.getenv("JOBS_TIMEOUT", "900"))

# Cuánto sobrevive el resultado del job en Redis. Corto y a propósito: el
# resultado REAL del análisis vive en el almacén (`AlmacenRedis`, 7 días),
# no acá. Lo que RQ guarda es metadata de la ejecución, útil para depurar un
# rato y no para consultar después.
TTL_RESULTADO_SEGUNDOS = 3600

# RQ solo reintenta excepciones que escapan del job. El entrypoint durable
# propaga después de persistir FALLIDO; BackgroundTasks usa otra semántica.
REINTENTOS_JOB = int(os.getenv("JOBS_RETRIES", "3"))
INTERVALOS_REINTENTO_SEGUNDOS = [10, 30, 60]


def usa_redis() -> bool:
    """Indica si el despacho va a la cola de Redis.

    Se lee de la MISMA variable que el almacén (`JOBS_BACKEND`) y no de una
    propia. Tener dos interruptores permitiría la combinación rota —cola en
    Redis con almacén en memoria— donde el worker escribe el resultado en su
    propio proceso y la API responde para siempre "pendiente" sobre un
    análisis que terminó hace rato.
    """
    return os.getenv("JOBS_BACKEND", "memoria").strip().lower() == "redis"


def obtener_cola() -> Any:
    """Construye la cola de RQ contra el Redis configurado.

    `decodificar=False` es OBLIGATORIO, no una preferencia: RQ decodifica
    él mismo lo que lee de Redis, así que con `decode_responses=True`
    revienta con `AttributeError: 'str' object has no attribute 'decode'`
    — y no al conectar, sino al consumir el primer job. Ver la explicación
    larga en `apps/api/store_redis.py::_cliente`.
    """
    # Imports diferidos: `rq` y `redis` son dependencias opcionales (grupo
    # `jobs`), y el camino por default no tiene por qué exigirlas instaladas.
    from rq import Queue

    from apps.api.store_redis import _cliente

    return Queue(NOMBRE_COLA, connection=_cliente(decodificar=False))


def _crear_retry() -> Any:
    """Construye la política solo cuando está instalado el extra de RQ."""
    from rq import Retry

    return Retry(
        max=REINTENTOS_JOB,
        interval=INTERVALOS_REINTENTO_SEGUNDOS,
    )


def despachar(analysis_id: str, tareas: Any, cliente: Any, almacen: Any) -> str:
    """Manda el análisis a ejecutarse. Devuelve dónde quedó.

    El valor de retorno —"redis" o "proceso"— no lo usa el handler para
    decidir nada; existe para que los tests puedan afirmar QUÉ camino se
    tomó. Sin eso, un test que verifique el despacho tendría que espiar
    atributos internos de FastAPI o de RQ.
    """
    if usa_redis():
        # Se encola el NOMBRE de la función, no la función: RQ la importa en
        # el worker por ese path. Pasar el objeto obligaría a que ambos
        # procesos tengan exactamente el mismo módulo cargado en memoria.
        obtener_cola().enqueue(
            "apps.jobs.tareas.ejecutar_analisis",
            analysis_id,
            job_id=analysis_id,
            job_timeout=TIMEOUT_JOB_SEGUNDOS,
            result_ttl=TTL_RESULTADO_SEGUNDOS,
            retry=_crear_retry(),
        )
        return "redis"

    from apps.jobs.tareas import procesar_analisis

    tareas.add_task(procesar_analisis, analysis_id, cliente, almacen)
    return "proceso"
