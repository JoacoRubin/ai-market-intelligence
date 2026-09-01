# ADR-014 — `company_research` se conecta a SEC EDGAR, determinístico

- **Estado:** Aceptado
- **Fecha:** 2026-09-01

## Contexto

`company_research` se reconocía desde el router pero `planner.py` cortaba
sin plan a propósito: "requiere las fuentes públicas de la fase 3"
(`Intencion.COMPANY_RESEARCH`, `agent/state.py:42`). El usuario probó
`"Comparame MELI y AMZN"` contra el agente real en esta misma sesión —
`estado: completado`, `informe: null`, con esa advertencia exacta. Pidió
conectar una fuente pública real.

Decisión tomada con el usuario antes de diseñar nada: **SEC EDGAR**. Datos
financieros oficiales (revenue, ganancia neta, activos) de empresas que
cotizan en EE. UU. — 100% gratis para siempre, sin API key, sin límite
diario (10 req/seg por IP), solo pide un header `User-Agent` con contacto
real. La alternativa típica, Alpha Vantage, se descartó por un dato medido
en el momento: su free tier bajó a 25 requests/día — se agota en la primera
corrida del golden set.

## Decisión

**El nombre de empresa lo extrae el LLM; la resolución a un identificador
concreto es determinística.** Mismo criterio que el router ya usa para
`dias`: "Apple vs Microsoft" no tiene formato fijo, no hay regex posible —
es lenguaje natural variable, tarea del modelo. `ESQUEMA` de
`agent/nodes/router.py` gana un campo `empresas: list[string]`, **required**
(con `format=esquema` de Ollama es gramática forzada; opcional invita a
omitirlo), manejado defensivo igual que el resto (`respuesta.get(...)`).
`AnalysisState` gana `empresas: list[str]`, paralelo a `entidades`
(product_ids) — no una sobrecarga, son conceptos distintos.

**La resolución nombre→CIK vive en `core/edgar.py`**, no en un nodo del
grafo: es I/O de red, misma naturaleza que `core/kpis.py` (que la *tool*
`product_metrics.py` llama). Match por **igualdad tras normalizar**, nunca
por substring: "Apple" por substring matchea tanto "Apple Inc." como "Apple
Hospitality REIT, Inc." — una REIT real, no relacionada. Un empate ambiguo
se trata como "no encontrada", nunca se adivina (mismo principio de lista
blanca de `docs/adr/ADR-004-sin-text-to-sql.md`, aplicado acá a nombres en
vez de a SQL). Se prueba ticker primero (solo para tokens con forma de
ticker: mayúsculas, 1-5 caracteres — "MELI"/"AMZN" no matchean nunca por
nombre, el título de la SEC nunca es un ticker) y nombre normalizado
después.

**`contexto_mercado` se construye 100% determinístico, cero llamada al
modelo — ni siquiera para redactar.** Son cifras oficiales de un tercero,
no texto ambiguo que necesite interpretación: el template en
`core/conclusiones.py::afirmaciones_de_empresas` YA es la redacción. Dejar
que el LLM las "redacte" sería una oportunidad de que las redondee, las
reordene o las mezcle entre empresas, sin ningún beneficio a cambio.
`synthesizer.py::sintetizar()` se bifurca: si hay métricas de producto,
sigue llamando al modelo exactamente igual que antes; si la consulta es
`company_research` pura (sin productos internos), no se llama al modelo en
absoluto — `modelo_llm` queda declarado como
`"ninguno (contexto_mercado determinístico, sin datos internos)"`, verificado
en una corrida real contra `localhost:8000`.

## Por qué determinístico y no otro prompt para el LLM

El principio rector del proyecto es "el modelo no calcula números — SQL o
software calcula, el modelo redacta" (README, ADR-004). Acá el argumento es
más fuerte todavía que con SQL propio: los hechos de SEC EDGAR no son datos
internos que el sistema controla, son cifras oficiales de terceros. Pedirle
al modelo que las "explique" agrega una capa de riesgo (redondeo,
confusión entre empresas, invención de contexto) sin agregar ningún valor —
el template ya dice todo lo que hay que decir. Es la misma lógica que ya
usa el modo degradado de toda la API cuando el LLM no está disponible,
llevada un paso más allá: acá ni siquiera hace falta que el LLM esté
disponible para que este camino funcione.

## Cómo quedó repartido el código

| Archivo | Responsabilidad |
|---|---|
| `core/edgar.py` | Cliente HTTP, resolución nombre→CIK, hechos XBRL, `hay_edgar_disponible()` |
| `agent/tools/research_company.py` | Contrato de tool: Pydantic, guardrails de presupuesto, manejo no-fatal de errores |
| `agent/nodes/router.py` | Extrae `empresas` (LLM) — nunca resuelve, nunca arma URL |
| `agent/nodes/planner.py` | Decide SI se llama a la tool (empresas presentes), nunca CÓMO |
| `core/conclusiones.py` | `afirmaciones_de_empresas()` — el template determinístico |
| `agent/nodes/synthesizer.py` | Bifurca entre LLM (hay métricas) y determinístico puro (no las hay) |
| `agent/nodes/validator.py` | Reconoce `HechoFinanciero` para no borrar `contexto_mercado` (ver la trampa, abajo) |

El planner nunca resuelve nada: pasa los nombres CRUDOS que extrajo el
router como argumento de la tool, mismo patrón que `"consulta":
estado.consulta` ya usa con texto libre en `search_documents`. La
resolución y el armado de la URL final ocurren enteramente DENTRO de la
tool, con el CIK ya numérico — el LLM nunca toca ni el ticker ni la URL.

## Las dos trampas que solo la verificación en vivo mostró

**Dos hosts distintos, no uno.** `data.sec.gov` sirve
`/api/xbrl/companyfacts/...`; el listado de tickers
(`company_tickers.json`) vive en **`www.sec.gov`**, un host distinto. El
código original usaba una sola constante `BASE = "https://data.sec.gov"`
para los dos endpoints. El síntoma no fue un error de conexión: fue un
**404 con cuerpo de error de S3** (`NoSuchKey`, con `RequestId`/`HostId` de
AWS) — `data.sec.gov` está detrás de un bucket S3, así que pedirle un
archivo que no tiene devuelve el 404 genérico de S3, no un 404 de la
aplicación. `hay_edgar_disponible()` interpretó ese 404 como "EDGAR no
responde", y una corrida real contra `localhost:8000` con
`"Comparame MELI y AMZN"` terminó con
`"SEC EDGAR no está respondiendo — no se pudo investigar ninguna empresa"`
— con SEC EDGAR perfectamente sano y alcanzable.

Se encontró recién en la verificación de punta a punta, no en los tests
unitarios: los tests de `core/edgar.py` mockean `httpx.get` sin URL real
de por medio, así que no podían atrapar un host equivocado — solo pegarle a
la red de verdad lo mostró. Ahí quedó la lección escrita en el propio
código (`core/edgar.py`, comentario junto a `BASE_WWW`/`BASE_DATOS`): un
mock que no valida la URL contra la que se llama es un mock que no prueba
la mitad del contrato.

**Segunda trampa, encontrada pidiéndole al usuario que probara con "Apple y
Tesla" después de arreglar la primera.** El fallback de conceptos de
revenue (`CONCEPTOS_REVENUE`) se quedaba con el **primer tag que tuviera
ALGÚN dato**, no con el dato más reciente. Apple dejó de taguear `Revenues`
en 2018 al adoptar ASC 606 y migró a
`RevenueFromContractWithCustomerExcludingAssessedTax` — pero el tag viejo
sigue existiendo en su XBRL, con años históricos hasta 2018. El resultado
real: un informe que decía "Apple reportó un revenue de USD 265.595
millones en el año fiscal **2018**" al lado de "tuvo una ganancia neta...
en el año fiscal **2025**" — dos años fiscales distintos, mismo informe,
sin que nada lo marcara como inconsistente (cada `Afirmacion` es correcta
por separado; la que faltaba era comparar entre tags, no dentro de uno
solo). El fix: `hechos_clave` compara el candidato de CADA concepto de la
lista de fallback y se queda con el que tenga el `end` más reciente de
todos, no con el primero que aparece. De nuevo, ni el test original
(`test_hechos_clave_usa_el_primer_concepto...`) ni el eval del golden set
lo hubieran atrapado — ninguno de los dos ejercita una empresa real con
tags migrados en su historial completo. Solo una corrida real, con una
empresa real, lo mostró.

## Alcance: qué NO hace

- **Un solo filing (10-K más reciente) por concepto, no serie histórica.**
  Serie histórica implicaría que el software calcule crecimiento
  interanual — factible después, diseño aparte.
- **2 empresas por consulta máximo**, mismo límite que `forecast_sales`
  aplica sobre entidades — auditable, no arbitrario. Cubre el golden set
  completo (`emp-01` pide 2, `emp-02`/`hold-04` piden 1).
- **3 conceptos XBRL fijos**: revenue (con fallback de tag —
  `Revenues`/`RevenueFromContractWithCustomerExcludingAssessedTax`/
  `SalesRevenueNet`, porque desde ASC 606 muchos filers, Apple incluida, no
  taguean `Revenues`), ganancia neta, activos totales. No es una lista
  abierta: cada concepto nuevo es una plantilla nueva que hay que validar
  contra el validador.
- **Sin retry ni backoff**, misma disciplina que `core/db.py`/`agent/llm.py`
  — el proyecto no tiene `tenacity` como dependencia directa y un límite de
  10 req/seg sin techo diario no lo justifica para 2 empresas por consulta.
- **Sin serie de precio de acción ni noticias** — solo lo que el 10-K
  declara. Una empresa que no cotiza en EE. UU. (no filea en la SEC) no
  tiene datos acá, y el agente lo dice en vez de inventarlos.
- **Sin chequeo cruzado `empresas` vs `intencion`** estilo
  `corregir_intencion_por_entidades` — el golden set no lo mide, se agrega
  si hace falta después.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| Alpha Vantage | Free tier degradado a 25 requests/día — se agota en una sola corrida del golden set. |
| Redactar `contexto_mercado` con el LLM (mismo patrón que `resumen_ejecutivo`) | Agrega riesgo de redondeo/confusión entre empresas sin ningún beneficio: el template ya es la redacción completa. |
| Resolver nombre→empresa por substring | "Apple" matchea por substring tanto la empresa correcta como una REIT no relacionada — colisión real, no hipotética. |
| Extraer nombres de empresa con regex/gazetteer determinístico | No hay formato fijo para un nombre societario en lenguaje natural — el mismo argumento que ya usa el router para no extraer `dias` con regex. |

## Consecuencias

**Positivas**
- `company_research` deja de estar cortado — el golden set ya tenía 3 casos
  reales (`emp-01`, `emp-02`, `hold-04`) esperando esta conexión.
- Cero riesgo de cifras inventadas: el modelo nunca toca un número
  financiero externo, ni para calcularlo ni para redactarlo.
- `Fuente.tipo="api_publica"` y `Fuente.url` se usan por primera vez en la
  práctica — existían en `core/report.py` desde el día uno.

**Negativas**
- Cobertura limitada a empresas que filean en la SEC (EE. UU.) — declarado
  en `limitaciones` del informe, no escondido.
- Un servicio externo más que puede estar caído — a diferencia de SQL
  Server (infraestructura propia), acá no hay control sobre la
  disponibilidad de `sec.gov`. Se degrada con una advertencia clara, nunca
  con una excepción que tumbe el grafo.
- El listado de tickers se cachea en memoria de proceso (`lru_cache`, ~1MB):
  un reinicio de la API lo vuelve a pedir. Aceptable — la lista cambia con
  baja frecuencia.
