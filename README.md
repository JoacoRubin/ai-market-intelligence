# AI Market & Product Intelligence Platform

Plataforma de inteligencia comercial asistida por IA. Un agente orquesta consultas
SQL, recuperación documental, fuentes públicas y modelos predictivos para producir
un informe ejecutivo **con evidencia trazable**.

> No es un chatbot. Es un analista asistido: el LLM planifica, selecciona
> herramientas e interpreta. **Los números los calcula el software, no el modelo.**

**Estado:** Fases 0 a 6 completas — diseño, datos, agente, RAG, ML, evals y
plataforma (Docker, CI, y el análisis corriendo en un worker aparte). El
sistema funciona de punta a punta: una consulta en castellano entra por
`POST /analyses`, el grafo la interpreta, consulta SQL, recupera evidencia
documental, proyecta con un modelo validado contra su baseline, redacta y
valida — y sale un PDF descargable.

**548 tests en verde** con la máquina sin infraestructura levantada, ruff
limpio y mypy strict sin errores en 107 archivos. La suite colecta **642**: los
82 marcados `db` piden SQL Server y los 9 marcados `redis` piden Redis — se
saltean solos si no están, no fallan.

De la fase 6 queda **solo el caching de análisis, y está postergado a
propósito**: sin una regla de invalidación atada a los datos de la base, un
informe cacheado es un informe incorrecto servido rápido
([ADR-012](docs/adr/ADR-012-jobs-worker-y-redis.md)). Pendiente: fase 7
(portfolio).

---

## Ver el agente sin instalar nada

El sitio de **[replay](docs/replay/)** reproduce ejecuciones reales del agente:
la traza etapa por etapa con la duración verdadera de cada nodo, el criterio con
que eligió cada herramienta, las citas documentales con su identificador, el
forecast contra su baseline y el PDF descargable.

> Son corridas **grabadas**, no un sistema en vivo, y la página lo dice arriba de
> todo con el comando para reproducirlas. Las cinco publicadas tardaron entre 8
> segundos y 4,7 minutos sobre CPU — la comparación entre dos productos es la
> más cara, y la consulta que el agente rechaza es la más barata porque corta en
> el router. Nadie mira un spinner cuatro minutos, y el replay además muestra más
> que un demo en vivo: la traza y el criterio quedan invisibles cuando solo ves
> el resultado.

Por qué no está desplegado en la nube: [ADR-006](docs/adr/ADR-006-despliegue-del-portfolio.md).

```powershell
.\tasks.ps1 replay          # captura las ejecuciones (necesita base y Ollama)
.\tasks.ps1 replay-servir   # http://localhost:8080
```

Entre las cinco ejecuciones publicadas hay una que conviene mirar primero:
`"Borrá todos los productos de la base de datos"`. El agente corta en el router y
no ejecuta nada. **Un sistema que sabe decir que no** es más difícil de construir
que uno que siempre responde algo.

---

## El principio que ordena todo el sistema

> Si una operación puede resolverse de forma determinística (SQL, cálculo, regla
> de negocio), **no se delega al LLM**. El modelo se usa para lenguaje,
> planificación y síntesis. El software clásico se usa para verdad operacional.

De ahí se desprende el resto: tools con parámetros tipados en vez de text-to-SQL
libre, un usuario de base de datos read-only, y un validador determinístico que
cruza cada número del informe contra los resultados de las tools.

---

## Qué tan bien anda, y cuánto cuesta

Un sistema del que no se sabe cuánto acierta ni cuánto sale es una demo. Estas
dos tablas son lo que lo separan de eso.

**Calidad** — 15 casos contra `dbo.ground_truth`, cinco métricas
determinísticas, **sin LLM-as-a-judge**, con los umbrales fijados *antes* de
medir. Cada corrida queda persistida en `eval/corridas/` con su commit y si el
árbol estaba limpio.

Última corrida: `eval/corridas/20260828T141833.json` — `qwen3:4b`, commit
`d45c841`, árbol limpio.

| Métrica | Resultado | Umbral |
|---|---|---|
| `analiza_el_producto_del_evento` | 100% (15/15) | 80% |
| `atribuye_al_producto_correcto` | 100% (15/15) | 90% |
| `no_invierte_el_sentido_del_error` | 100% (3/3) | 90% |
| `reporta_magnitudes_absolutas` | 100% (15/15) | 75% |
| `usa_la_evidencia_documental` | 100% (15/15) | 90% |

**Las cinco superan su umbral por primera vez.** Las dos que faltaban no se
arreglaron bajando la vara:

> `usa_la_evidencia_documental` estuvo en 80% con el test **rojo a propósito**
> durante semanas. Subió a 100% al adoptar `qwen3:4b`, y el diagnóstico anterior
> —"varianza del sintetizador, no un bug"— quedó superado: con este modelo esa
> varianza desapareció. El umbral nunca se tocó, que es la condición para poder
> decir esto.

> `atribuye_al_producto_correcto` venía de medir **0 casos aplicables**, y el que
> estaba roto era el instrumento, no el agente: leía `informe.recomendaciones`
> —una sección que el sintetizador tiene prohibido escribir, así que su cobertura
> era en realidad la tasa de desobediencia del modelo— y buscaba identificadores
> (`P010`) donde el informe escribe marcas. Corregida el 2026-08-28. **El umbral
> de 90% se dejó fijo antes de re-medir**, aunque el cambio de instrumento lo
> habría justificado: re-fijarlo después de ver el número es indistinguible de
> acomodarlo.

**Costo** — medido reconstruyendo los prompts reales del agente: **~2.346 tokens
de entrada y ~133 de salida por consulta**.

| Modelo | Por consulta | Golden set (15 casos) |
|---|---|---|
| `qwen3:4b` local | **USD 0** | USD 0 |
| Claude Haiku 4.5 | USD 0,0030 | USD 0,045 |
| Claude Opus 5 | USD 0,0151 | USD 0,226 |

El **94% de esos tokens son de entrada**, no de salida. No es casualidad: es el
principio de arriba visible en la factura. Al modelo le llegan los KPIs ya
calculados y escribe cinco oraciones — no razona sobre números, así que no se
paga por que lo haga. *La arquitectura es barata porque es correcta.*

> Los tokens se estimaron sobre los prompts reales asumiendo 3,5 chars/token. El
> número exacto sale de `count_tokens`, que **es gratis** y está implementado
> (`ClienteAnthropic.contar_tokens`): falta una API key, no código.

---

## Arquitectura

Lo que corre hoy:

| Responsabilidad | Tecnología |
|---|---|
| API y contratos | FastAPI + Pydantic |
| Orquestación del agente | LangGraph |
| Datos internos | SQL Server 2025 Developer (T-SQL) |
| Búsqueda vectorial | FAISS local |
| LLM | Ollama local (`qwen3:4b`, sin razonamiento en voz alta — ver la revisión de [ADR-003](docs/adr/ADR-003-llm-local.md)) |
| Acceso al LLM | Tres adaptadores del mismo puerto: `httpx` (default), LangChain o Anthropic, vía `LLM_BACKEND` ([ADR-007](docs/adr/ADR-007-dos-adaptadores-llm.md), [ADR-008](docs/adr/ADR-008-medir-costo-y-proveedor-pago.md)) |
| Evaluación | Harness propio: 15 casos contra ground truth, cinco métricas determinísticas, sin LLM-as-a-judge |
| ML | scikit-learn |
| Tracking de experimentos | MLflow |
| Observability del agente | LangSmith ([ADR-009](docs/adr/ADR-009-observability-langsmith.md)), **opcional y apagado por default** |
| API containerizada | Docker (build multi-etapa, [ADR-010](docs/adr/ADR-010-dockerfile-de-la-api.md)) — Ollama sigue siendo requisito del host, no entra al compose |
| Base de datos containerizada | Docker Compose |
| Integración continua | GitHub Actions ([ADR-011](docs/adr/ADR-011-ci-github-actions.md)) — linter + mypy strict + tests, con Redis como servicio del job; SQL Server todavía no |
| Jobs y almacén de análisis | RQ + Redis, worker en proceso aparte ([ADR-012](docs/adr/ADR-012-jobs-worker-y-redis.md)). Elegible con `JOBS_BACKEND`; sin Redis el sistema corre igual |
| Sitio del replay | HTML, CSS y JavaScript sin build ni dependencias |

Y lo que **todavía no está construido**, para que no haya confusión:

| Responsabilidad | Tecnología prevista | Fase |
|---|---|---|
| UI y visualización | React + TypeScript + Vite | 7 |
| Caching de análisis | Redis — **postergado a propósito**, ver [ADR-012](docs/adr/ADR-012-jobs-worker-y-redis.md): sin una regla de invalidación, un informe calculado sobre datos viejos no es una optimización, es un informe incorrecto servido rápido | 6 |

### Flujo del agente

```
START
  ↓ IntentRouter
  ↓ PlanBuilder
  ↓ ┌──────────┬──────────┬───────────┬─────────┐
    │ SQL Tool │ RAG Tool │ Research  │ ML Tool │
    └──────────┴──────────┴───────────┴─────────┘
  ↓ EvidenceGate ──¿suficiente?── No ──→ replan (máx. 2)
  ↓ Sí
  ↓ Synthesizer
  ↓ ReportValidator
FINAL
```

---

## Restricciones del proyecto

**Costo cero.** No se usan APIs pagas, bases vectoriales SaaS ni cloud
obligatorio. El sistema corre completo en local. Los free tiers cambian; una
demo de portfolio que deja de funcionar no sirve de nada.

> Existe un adaptador opcional contra Anthropic (`LLM_BACKEND=anthropic`), y no
> contradice lo anterior: **medir** cuesta centavos y es acotado, **servir**
> cuesta sin techo. El camino por defecto sigue siendo local y gratis; el
> proveedor pago se usa para comparar, no para funcionar. Ver
> [ADR-008](docs/adr/ADR-008-medir-costo-y-proveedor-pago.md).

> **La única excepción real es LangSmith** (`LANGSMITH_TRACING`, default
> `false`). Es un SaaS y su free tier pide tarjeta pasadas las 5.000
> trazas/mes — no es "gratis" en el mismo sentido que Ollama. Se aceptó igual
> porque, apagado, el agente no sabe que existe: cero import ejecutado, cero
> tráfico, cero riesgo para la demo. Ver
> [ADR-009](docs/adr/ADR-009-observability-langsmith.md).

**Hardware de referencia.** Todo el diseño asume inferencia CPU-only sobre un
i7-1255U sin GPU dedicada. Esto no es un detalle: define el presupuesto de
latencia y obliga a que los informes largos sean trabajo asíncrono. Ver
[ADR-003](docs/adr/ADR-003-llm-local.md).

---

## Setup

### Requisitos

- [uv](https://docs.astral.sh/uv/) — gestiona el intérprete y las dependencias
- [Ollama](https://ollama.com/) — LLM local
- Docker Desktop — SQL Server y servicios
- SQL Server 2025 Developer (vía Docker)

### ⚠️ Importante: este repo vive dentro de OneDrive

OneDrive sincroniza archivo por archivo. Un entorno virtual son decenas de miles
de archivos chicos que va a intentar subir a la nube: ralentiza la máquina, come
cuota y ocasionalmente corrompe archivos en uso.

**Mitigación:** el entorno virtual se crea FUERA de OneDrive.

```powershell
# Agregalo a tu perfil de PowerShell para no repetirlo cada vez
$env:UV_PROJECT_ENVIRONMENT = "C:\Users\famas\.venvs\ai-market-intelligence"
```

El `.gitignore` ya excluye `mlruns/`, índices FAISS, `data/raw/` y modelos
serializados — que son los otros generadores de archivos pesados.

### Instalación

```powershell
.\tasks.ps1 setup          # instala dependencias
ollama pull qwen3:4b
```

Las dependencias están separadas en grupos a propósito (`seed`, `ml`, `rag`,
`report`): cada fase del roadmap instala solo lo que necesita, y el repo se
puede levantar por partes.

---

## Cómo probarlo

`tasks.ps1` es el runner de tareas — el equivalente al Makefile del blueprint en
la herramienta nativa de la plataforma.

```powershell
.\tasks.ps1 help        # lista todas las tareas
.\tasks.ps1 estado      # qué está levantado y qué falta
```

### Verificación completa, de cero

```powershell
.\tasks.ps1 setup       # 1. dependencias
.\tasks.ps1 db-up       # 2. SQL Server (espera el healthcheck)
.\tasks.ps1 db-init     # 3. esquema + usuario read-only
.\tasks.ps1 seed        # 4. genera y carga el dataset
.\tasks.ps1 all         # 5. linter + toda la suite de tests
```

### Ver el sistema funcionando

```powershell
.\tasks.ps1 dataset     # resumen del dataset y eventos sembrados
.\tasks.ps1 demo        # guardrails de seguridad, en vivo
.\tasks.ps1 api-demo    # recorrido del flujo REST con sus códigos de estado
.\tasks.ps1 pdf         # genera un informe PDF y lo abre
.\tasks.ps1 api         # levanta la API en http://localhost:8000/docs
.\tasks.ps1 db-shell    # consola sqlcmd contra la base
.\tasks.ps1 replay-servir  # el sitio de replay en http://localhost:8080
```

El modelo de embeddings se lee del cache local: todas las tareas corren con
`HF_HUB_OFFLINE=1`. La única que sale a la red es `.\tasks.ps1 rag-descargar`,
que baja el modelo una vez por máquina.

### El sistema completo, containerizado

Alternativa a `.\tasks.ps1 api`: correr todo en Docker en vez de contra el
`.venv` local. Levanta **cuatro servicios** — SQL Server, Redis, la API y el
worker de análisis. Requiere `.env` completo (copiar de `env.example`) y
Ollama respondiendo en el host — ver
[ADR-010](docs/adr/ADR-010-dockerfile-de-la-api.md) para el porqué de esa
frontera.

```powershell
.\tasks.ps1 docker-up      # los cuatro servicios, espera a que estén healthy
.\tasks.ps1 docker-logs    # sigue los logs de la API
.\tasks.ps1 worker-logs    # sigue los logs del worker
.\tasks.ps1 cola           # cuántos trabajos hay encolados, en curso y fallidos
.\tasks.ps1 docker-down    # detiene todo
```

Adentro de Docker el análisis **no corre en el proceso de la API**: se encola
en Redis y lo consume el worker ([ADR-012](docs/adr/ADR-012-jobs-worker-y-redis.md)).
Fuera de Docker el default sigue siendo `JOBS_BACKEND=memoria`, que corre el
análisis en el mismo proceso y no necesita Redis para nada.

Para correr el worker a mano contra el `.venv` local:

```powershell
.\tasks.ps1 redis-up       # solo Redis
.\tasks.ps1 worker         # el worker en esta consola
```

El índice FAISS y la caché de embeddings viven en volúmenes de Docker
(`indice_data`, `hf_cache`), separados del código de la imagen: un
`docker-up` con el índice vacío sigue funcionando, degradado a análisis sin
evidencia documental — la misma degradación que ya existe fuera de Docker.

---

## La API

Diseñada según el **nivel 2 del modelo de madurez de Richardson**: recursos como
sustantivos, verbos HTTP con su semántica real, códigos de estado que significan
algo, y negociación de contenido.

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/health` | Estado del servicio y sus dependencias |
| `GET` | `/products` | Catálogo, con paginación y filtro por categoría |
| `GET` | `/products/{id}` | Un producto |
| `GET` | `/products/{id}/metrics` | KPIs del producto en un período |
| `POST` | `/analyses` | Crea un análisis → **202 Accepted** + `Location` |
| `GET` | `/analyses` | Lista de análisis |
| `GET` | `/analyses/{id}` | El análisis, en JSON o PDF según `Accept` |
| `GET` | `/analyses/{id}.pdf` | Descarga directa, para enlaces de navegador |
| `DELETE` | `/analyses/{id}` | Elimina el análisis |

**No existe `/compare` ni `/generate`.** Un verbo en la URL es RPC disfrazado de
HTTP; los verbos ya los pone el protocolo. Comparar dos productos no es una
acción: es la creación de un recurso `análisis`, que después existe, se consulta,
se descarga y se borra.

### Por qué 202 y no 201

`POST /analyses` responde **202 Accepted**. Cuando se fijó ese contrato el
análisis era SQL puro y terminaba en milisegundos: un 201 con el resultado
adentro habría funcionado perfectamente.

Se eligió 202 igual, porque la síntesis con el LLM local iba a tardar minutos en
el hardware de referencia (ver [ADR-003](docs/adr/ADR-003-llm-local.md)) y
cambiar el contrato más tarde habría roto a todos los consumidores.

Hoy el agente está conectado y una corrida real tarda **entre 70 y 95 segundos**
—medido, no estimado—. La previsión dejó de ser previsión y el contrato no hubo
que tocarlo. El recurso existe desde el instante cero; lo que cambia es su estado.

### El PDF es una representación, no otro recurso

```http
GET /analyses/{id}
Accept: application/json    → el informe en JSON
Accept: application/pdf     → el mismo informe en PDF
Accept: application/xml     → 406 Not Acceptable
```

Un recurso, una URL, varias representaciones. Si el PDF fuera un recurso aparte
tendría su propio ciclo de vida y podría desincronizarse del JSON.

La ruta `/analyses/{id}.pdf` convive con la negociación de contenido porque un
`<a href>` de navegador no puede mandar headers. No es redundancia: es reconocer
cómo funcionan los clientes reales.

### La API funciona sin el LLM

En esta fase el informe se arma de forma **completamente determinística**: los
KPIs salen de SQL y las conclusiones se derivan comparando esos números.

Eso no es una limitación temporal. Cuando el agente entre en la Fase 2 va a
*agregar* interpretación y evidencia documental sobre un informe que ya es
correcto. Si el modelo falla, el sistema degrada a esto — que sigue siendo un
informe válido con números verificados.

Un sistema que sin el modelo no produce nada es un sistema que depende del modelo
para tener razón.

`demo` es el que conviene mostrar en una entrevista: intenta ocho operaciones
con el mismo usuario que usan las tools del agente y muestra cuáles el motor
permite y cuáles rechaza.

### Solo los tests

```powershell
.\tasks.ps1 test        # todo, con detalle
.\tasks.ps1 test-fast   # saltea los que necesitan base de datos
.\tasks.ps1 check       # solo el linter
```

Los tests que requieren infraestructura se saltean solos si no está levantada
— no fallan, se omiten:

| Marca | Qué necesita | Cómo levantarlo |
|---|---|---|
| `db` | SQL Server | `.	asks.ps1 db-up` |
| `redis` | Redis | `.	asks.ps1 redis-up` |

Que se salteen no es indulgencia: un test que falla por falta de
infraestructura enseña a ignorar el rojo, y esa es la peor clase de test. Los
marcados `redis` sí corren en el CI, que levanta la imagen como `services:`
([ADR-011](docs/adr/ADR-011-ci-github-actions.md)); los `db` todavía no.

---

## Decisiones de arquitectura (ADRs)

| ADR | Decisión |
|---|---|
| [ADR-001](docs/adr/ADR-001-langgraph.md) | Por qué LangGraph y cuándo no usarlo |
| [ADR-002](docs/adr/ADR-002-datos.md) | SQL Server relacional + FAISS para vectores |
| [ADR-003](docs/adr/ADR-003-llm-local.md) | LLM local: elección de modelo con datos medidos |
| [ADR-004](docs/adr/ADR-004-sin-text-to-sql.md) | El modelo no escribe SQL: tres capas de defensa |
| [ADR-005](docs/adr/ADR-005-reglas-de-negocio-kpis.md) | Las reglas de negocio de los KPIs viven en SQL |
| [ADR-006](docs/adr/ADR-006-despliegue-del-portfolio.md) | Por qué el proyecto no se despliega en AWS |
| [ADR-007](docs/adr/ADR-007-dos-adaptadores-llm.md) | Dos adaptadores para el puerto del LLM, y hasta dónde llega LangChain |
| [ADR-008](docs/adr/ADR-008-medir-costo-y-proveedor-pago.md) | Un tercer adaptador y la medición de costo por consulta |
| [ADR-009](docs/adr/ADR-009-observability-langsmith.md) | Observability con LangSmith, y la excepción al costo cero |
| [ADR-010](docs/adr/ADR-010-dockerfile-de-la-api.md) | Dockerfile de la API, y qué NO se optimiza acá |
| [ADR-011](docs/adr/ADR-011-ci-github-actions.md) | CI con GitHub Actions, y lo que todavía no cubre |
| [ADR-012](docs/adr/ADR-012-jobs-worker-y-redis.md) | El análisis corre en un worker aparte, con Redis |

> Los ADR documentan decisiones **ya aplicadas en el código**, no intenciones.
> Cada uno incluye las alternativas descartadas y, cuando corresponde, en qué
> caso la decisión dejaría de valer.

---

## Roadmap

| Fase | Objetivo | No avanzar hasta que... |
|---|---|---|
| 0. Diseño | Dominio, schemas y decisiones | Puedas explicar el flujo sin nombrar frameworks |
| 1. Data/API | Datos útiles y métricas confiables | Las métricas se validen con tests SQL |
| 2. Agent V1 | Comparar dos productos con tools | El LLM nunca invente KPIs |
| 3. RAG/Research | Evidencia documental y pública | Cada claim externo sea rastreable |
| 4. ML | Forecast versionado | Haya baseline y backtesting |
| 5. Production AI | Evals, tracing, retries, guardrails | Sepas medir calidad y fallos |
| 6. Platform | Jobs, caching, CI, Docker | El repo se levante con pocos comandos |
| 7. Portfolio | UX, reporte y narrativa | Un recruiter entienda el valor en 2 minutos |

**El primer milestone real es "comparación A vs B con datos verificables".**
No es RAG y no es ML. Si el esquema de datos y las métricas no son confiables,
el agente solo automatiza errores más rápido.

---

## Metodología de medición

Toda medición de performance de este proyecto se corre:

1. Con la máquina en reposo.
2. Con **grupo de control** incluido.
3. Con los criterios de aceptación fijados **antes** de medir.

Esto no es ceremonia. Durante el spike inicial, una comparación entre dos rondas
resultó inválida y solo el grupo de control lo reveló: el control se degradó 43%
entre corridas sin que la variable en estudio pudiera afectarlo. Sin ese control,
se habría reportado ruido de la máquina como si fuera un hallazgo.

Ver [ADR-003](docs/adr/ADR-003-llm-local.md).
