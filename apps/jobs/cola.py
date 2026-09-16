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

# Techo de análisis aceptados que todavía no terminaron. Por encima de este
# número `POST /analyses` devuelve 429 en vez de encolar.
#
# Existe porque un análisis es CARO y ASIMÉTRICO: emitir el pedido cuesta un
# request, atenderlo cuesta entre 70 y 95 segundos de CPU en el hardware de
# referencia (ADR-003). Sin techo, un cliente —o un script con un bug— llena
# la cola en segundos y deja al worker ocupado durante horas.
#
# El default de 10 no es arbitrario: con una corrida de ~90 s, diez análisis
# son unos 15 minutos de trabajo pendiente. Más que eso ya no es una cola, es
# una espera que nadie va a mirar.
MAX_ANALISIS_EN_VUELO = int(os.getenv("MAX_ANALISIS_EN_VUELO", "10"))

# Cuántos análisis recientes se miran para contar los que siguen en vuelo,
# cuando el backend es `memoria`. Ver `analisis_en_vuelo`.
VENTANA_CONTEO_EN_MEMORIA = 200


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


def analisis_en_vuelo(almacen: Any) -> int:
    """Cuántos análisis fueron aceptados y todavía no terminaron.

    Se cuenta distinto en cada backend porque cada uno tiene una primitiva
    barata distinta, y forzar una sola implementación empeoraría las dos:

    - **redis**: RQ ya sabe la profundidad de la cola y cuáles están corriendo.
      Las dos son O(1) contra Redis, y son literalmente "lo encolado".
    - **memoria**: no hay cola — el análisis corre en el proceso de la API con
      `BackgroundTasks`. Lo que se cuenta es el estado persistido, sobre una
      ventana acotada de los más recientes.

    La ventana es deliberada: `listar` devuelve del más nuevo al más viejo, y
    un análisis en vuelo siempre es reciente, así que mirar los primeros
    doscientos los encuentra a todos en cualquier escenario realista. Si aun
    así se quedara corto, el error es contar de MENOS y dejar pasar un pedido
    —nunca rechazar uno legítimo—, que es la dirección correcta para
    equivocarse en un control de admisión.
    """
    if usa_redis():
        from rq.registry import StartedJobRegistry

        cola = obtener_cola()
        return len(cola) + len(StartedJobRegistry(queue=cola))

    from application.models import EstadoAnalisis

    _, recientes = almacen.listar(limite=VENTANA_CONTEO_EN_MEMORIA, offset=0)
    return sum(
        1 for a in recientes
        if a.estado in (EstadoAnalisis.PENDIENTE, EstadoAnalisis.PROCESANDO)
    )


def hay_capacidad(almacen: Any, maximo: int = MAX_ANALISIS_EN_VUELO) -> bool:
    """Indica si se puede aceptar un análisis más.

    Un `maximo` de 0 o negativo desactiva el control, para que un entorno que
    necesite ejecutar una tanda grande —el golden set, por ejemplo— no tenga
    que tocar código. Se configura con `MAX_ANALISIS_EN_VUELO`.
    """
    if maximo <= 0:
        return True
    try:
        return analisis_en_vuelo(almacen) < maximo
    except Exception:
        # Un fallo contando NO puede volverse un fallo sirviendo. Si Redis no
        # responde, el despacho de abajo va a fallar igual y con un error que
        # describe el problema real; rechazar acá con un 429 diría "estoy
        # ocupado" sobre un sistema que en realidad está caído.
        return True


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
