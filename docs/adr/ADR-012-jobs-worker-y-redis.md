# ADR-012 — El análisis corre en un worker aparte, con Redis

- **Estado:** Aceptado
- **Fecha:** 2026-08-27

## Contexto

Hasta acá `POST /analyses` guardaba el registro en un diccionario en memoria
y despachaba el trabajo con `BackgroundTasks` de FastAPI: el análisis corría
**en el mismo proceso que atiende HTTP**. Con el agente conectado eso son 70
a 95 segundos por corrida (y 4,7 minutos la comparación más cara de las
capturas del replay), en el hardware de referencia CPU-only de ADR-003.

Tres agujeros concretos, ninguno hipotético:

1. **Un reinicio pierde el trabajo Y el registro.** No queda ni rastro de
   que el análisis existió: el cliente tiene un id que devuelve 404.
2. **No se comparte entre procesos.** Con más de un worker de uvicorn, un
   `POST` atendido por uno y el `GET` por otro da 404 sobre un análisis que
   existe. Hoy no pasa porque hay un solo proceso — o sea, el sistema
   depende de una condición que nadie declaró.
3. **Nada acota cuántos análisis corren a la vez.** N peticiones simultáneas
   son N grafos compitiendo por la misma CPU, y en CPU-only eso no degrada:
   multiplica el tiempo de todos.

El propio `apps/api/store.py` ya lo anticipaba en su docstring desde la Fase
1: *"hará falta un backend real con Redis y un worker aparte (Fase 6 del
roadmap). Está detrás de una interfaz mínima justamente para que ese cambio
sea sustituir esta clase y nada más."*

## Decisión

**Cola de RQ sobre Redis, consumida por un proceso worker aparte, y el
almacén de análisis también en Redis.** Se elige con una sola variable:

```
JOBS_BACKEND=memoria  → BackgroundTasks + almacén en memoria  (default)
JOBS_BACKEND=redis    → cola de RQ + AlmacenRedis
```

En `docker-compose.yml` siempre es `redis`; el default del código sigue
siendo `memoria` para que la suite no exija infraestructura y el repo se
levante con pocos comandos.

**Una sola variable para las dos cosas, y no dos.** Con interruptores
separados existiría la combinación rota —cola en Redis, almacén en
memoria— donde el worker escribe el resultado en la memoria de su propio
proceso y la API responde "pendiente" para siempre sobre un análisis que
terminó hace rato. Un estado imposible de diagnosticar desde afuera, que se
evita no permitiéndolo.

**Un valor desconocido falla al construir.** `JOBS_BACKEND=redis_` con un
typo no cae al default: levanta `ValueError`. Caer a memoria en silencio
daría un sistema que parece distribuido y no lo es.

## Por qué RQ y no Celery

El código de este proyecto es síncrono, y `ejecutar_grafo` también. RQ
encola una función y la corre; Celery traería broker abstracto, result
backend, serializers configurables y un vocabulario entero para resolver lo
mismo. Es literalmente el criterio que fija ADR-001: **la arquitectura
justifica el framework, no al revés.**

**`SpawnWorker`, no el `Worker` por defecto.** El worker clásico de RQ hace
`os.fork()` por job, y `fork()` no existe en Windows — la máquina de
desarrollo de este proyecto. `SpawnWorker` usa `multiprocessing.spawn` y
corre en los dos sistemas. Se usa **siempre**, no solo en Windows: un worker
que se comporta distinto según el sistema operativo es uno que se prueba en
un lado y se rompe en el otro. Cada job dura minutos, así que el costo extra
de arrancar el proceso es ruido.

## Cómo quedó repartido el código

`apps/jobs/` tiene tres archivos con una responsabilidad cada uno:

| Archivo | Responsabilidad | Importa `rq`/`redis` |
|---|---|---|
| `tareas.py` | **QUÉ** se ejecuta | No |
| `cola.py` | **DÓNDE** se ejecuta (el despacho) | Sí, diferido |
| `worker.py` | El proceso que consume la cola | Sí |

Que `tareas.py` no importe la infraestructura es el punto: la lógica del
análisis se testea con un doble del modelo, sin Redis y sin haber elegido
todavía dónde va a correr.

`procesar_analisis` y `estado_inicial` **se mudaron desde
`apps/api/main.py`**. No es cosmética: mientras vivían ahí, un worker que
quisiera invocarlas tenía que importar FastAPI, los handlers y el router
entero para correr algo que no atiende una sola petición HTTP.

**El job recibe solo el `analysis_id`.** RQ serializa los argumentos con
pickle para mandarlos por Redis, así que pasarle el cliente del modelo o el
almacén sería serializar conexiones abiertas. Cada proceso construye los
suyos — y eso tiene una consecuencia buena: el worker respeta el
`LLM_BACKEND` de SU entorno, así que puede correr contra otro proveedor que
la API sin que la API se entere. Es justo lo que promete el puerto de
ADR-007.

**El almacén se inyecta en `procesar_analisis`, no se importa global.** El
worker corre en otro proceso y tiene que escribir en el almacén compartido;
recibirlo por parámetro hace que esa diferencia sea imposible de olvidar.

## La forma de los datos en Redis

Dos claves por almacén: `<prefijo>:<id>` con el análisis en JSON, y
`<prefijo>:index`, un **sorted set** con los ids puntuados por su timestamp
de creación.

El índice existe porque `listar()` devuelve el más nuevo primero y pagina.
Resolverlo con `KEYS <prefijo>:*` sería más corto y sería un error: `KEYS`
recorre el keyspace entero y bloquea a Redis, así que el costo crecería con
todo lo que haya en la instancia y no con lo que este almacén guardó.

El puntaje es el instante de **creación** y no "ahora": si se actualizara en
cada `guardar`, la lista se reordenaría sola cada vez que el worker pasa un
análisis a PROCESANDO o COMPLETADO.

Las dos escrituras van en un `pipeline` para que viajen juntas. Si el dato
se escribiera y el índice no, el análisis existiría pero no aparecería en
`listar()` — invisible sin estar ausente, la peor clase de inconsistencia.

## La trampa que costó levantar el stack entero

**RQ es incompatible con `decode_responses=True`, y no falla al conectar.**

El almacén NECESITA ese flag: los ids que salen del índice se usan para
armar claves, y un `b'req-abc'` mezclado con strings produce claves que no
coinciden con las escritas — un bug que no rompe, solo devuelve vacío.

RQ necesita exactamente lo contrario: decodifica él mismo lo que lee, así
que con un cliente que ya decodificó revienta con
`AttributeError: 'str' object has no attribute 'decode'`.

Lo caro no fue el bug, fue **cuándo se manifiesta**: el worker arrancaba,
se conectaba, logueaba `*** Listening on analisis...` —todo verde— y se
caía recién al entrar el primer job, en un loop de reinicio que desde
afuera parece un problema de red. Ni los tests unitarios, ni ruff, ni mypy
strict lo habrían visto: cada pieza estaba bien por separado.

Por eso hay **dos clientes** (`_cliente(decodificar=...)`) y no uno
compartido. Parece duplicación y es lo contrario: un solo cliente sirve a
uno de los dos consumidores y rompe al otro. Y por eso hay un test que
afirma explícitamente el flag de cada uno — para que la próxima vez no haga
falta levantar cuatro contenedores para descubrirlo.

**Del mismo viaje salió otro:** el worker heredaba el `HEALTHCHECK` de la
imagen, que hace un GET a `localhost:8000/health`. El worker no sirve HTTP,
así que habría quedado `unhealthy` para siempre. El compose lo reemplaza por
la pregunta que sí corresponde: ¿está este worker registrado y vivo para RQ?

## Alcance: qué NO hace

- **No hay caching.** El README de la Fase 6 dice "jobs y caching"; esto es
  solo lo primero, y es deliberado. Cachear análisis idénticos suena obvio
  y abre tres preguntas que no tienen respuesta corta: qué cuenta como
  "idéntica" (¿misma consulta en lenguaje natural? ¿mismos productos y
  período?), cuánto vive la entrada, y **cómo se invalida cuando cambian
  los datos de la base** — que es la que decide el resultado. Un caché que
  devuelve un informe calculado sobre datos viejos no es una optimización:
  es un informe incorrecto servido rápido. Se hace cuando haya una regla
  de invalidación, no antes.
- **No acota la concurrencia todavía.** Un solo worker en el compose
  procesa de a un job, lo que de hecho serializa las corridas — pero eso es
  consecuencia de la topología, no un límite declarado. Subir `worker` a
  varias réplicas sin pensar en la CPU disponible reintroduce el agujero 3.
- **No hay reintentos automáticos.** Un fallo del grafo se guarda en el
  recurso (`estado=FALLIDO`, `error=...`) igual que antes. RQ sabe
  reintentar, pero un análisis que falló por datos faltantes va a volver a
  fallar, y reintentar gasta minutos de CPU para llegar al mismo lugar.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| Celery | Ver arriba: resuelve lo mismo con mucho más vocabulario, para un código que es síncrono de punta a punta. |
| Solo mover el almacén a Redis, dejando `BackgroundTasks` | Tapa el agujero 1 (persistencia) y deja los otros dos: el análisis seguiría comiéndose el proceso que atiende HTTP. |
| `arq` (cola async) | Encajaría si el código fuera `async`. No lo es — `ejecutar_grafo` es síncrono — así que habría que envolver todo en un executor para ganar nada. |
| Redis obligatorio, sin el modo memoria | Rompería "el repo se levanta con pocos comandos" (el objetivo declarado de la Fase 6) y obligaría a la suite a exigir infraestructura. El patrón de backend elegible ya está en el repo dos veces: `LLM_BACKEND` y `RATE_LIMIT_BACKEND` en el proyecto hermano. |

## Consecuencias

**Positivas**
- Un reinicio de la API ya no pierde análisis: el registro vive en Redis y
  el job encolado también (`appendonly yes` en el servicio).
- La API deja de gastar sus procesos en corridas de minutos.
- El worker puede escalarse o moverse de máquina sin tocar la API.

**Negativas**
- **Un servicio más que puede estar caído.** Con `JOBS_BACKEND=redis` y
  Redis abajo, `POST /analyses` falla — antes no había nada que fallar. El
  worker al menos lo dice al arrancar en vez de quedarse escuchando una
  cola inexistente.
- **Dos caminos que mantener.** `memoria` y `redis` tienen que comportarse
  igual, y los tests del almacén verifican explícitamente que el ORDEN de
  `listar()` coincide — si divergiera, cambiar de backend le cambiaría la
  respuesta a `GET /analyses` sin que nadie toque un handler.
- **Un test más que depende de infraestructura.** Los marcados `redis` sí
  corren en CI —se agregó Redis como `services:` del job, que levanta en
  segundos y no necesita esquema ni seed— pero en local se saltean si no
  está levantado. `.\tasks.ps1 redis-up` alcanza.
