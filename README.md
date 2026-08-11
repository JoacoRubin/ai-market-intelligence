# AI Market & Product Intelligence Platform

Plataforma de inteligencia comercial asistida por IA. Un agente orquesta consultas
SQL, recuperación documental, fuentes públicas y modelos predictivos para producir
un informe ejecutivo **con evidencia trazable**.

> No es un chatbot. Es un analista asistido: el LLM planifica, selecciona
> herramientas e interpreta. **Los números los calcula el software, no el modelo.**

**Estado:** Fase 0 — Diseño. Spike de viabilidad completado.

---

## El principio que ordena todo el sistema

> Si una operación puede resolverse de forma determinística (SQL, cálculo, regla
> de negocio), **no se delega al LLM**. El modelo se usa para lenguaje,
> planificación y síntesis. El software clásico se usa para verdad operacional.

De ahí se desprende el resto: tools con parámetros tipados en vez de text-to-SQL
libre, un usuario de base de datos read-only, y un validador determinístico que
cruza cada número del informe contra los resultados de las tools.

---

## Arquitectura

| Responsabilidad | Tecnología |
|---|---|
| UI y visualización | React + TypeScript + Vite |
| API y contratos | FastAPI + Pydantic |
| Orquestación del agente | LangGraph |
| Datos internos | SQL Server 2025 Developer (T-SQL) |
| Búsqueda vectorial | FAISS local |
| LLM | Ollama local (`llama3.2:3b`) |
| ML | scikit-learn |
| Tracking y tracing | MLflow |
| Packaging | Docker Compose |
| CI | GitHub Actions |

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
ollama pull llama3.2:3b
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
```

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

`POST /analyses` responde **202 Accepted**. Hoy el análisis es SQL puro y termina
en milisegundos, así que un 201 con el resultado adentro funcionaría.

Pero la síntesis con el LLM local tarda cerca de **dos minutos** en el hardware de
referencia (ver [ADR-003](docs/adr/ADR-003-llm-local.md)). Dejar al cliente
colgado ese tiempo es inaceptable, y cambiar el contrato después rompería a todos
los consumidores. El recurso existe desde el instante cero; lo que cambia es su
estado.

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

Los tests que requieren SQL Server están marcados con `@pytest.mark.db` y se
saltean solos si la base no está levantada — no fallan, se omiten.

---

## Decisiones de arquitectura (ADRs)

| ADR | Decisión |
|---|---|
| [ADR-001](docs/adr/ADR-001-langgraph.md) | Por qué LangGraph y cuándo no usarlo |
| [ADR-002](docs/adr/ADR-002-datos.md) | SQL Server relacional + FAISS para vectores |
| [ADR-003](docs/adr/ADR-003-llm-local.md) | LLM local: elección de modelo con datos medidos |
| [ADR-005](docs/adr/ADR-005-reglas-de-negocio-kpis.md) | Las reglas de negocio de los KPIs viven en SQL |
| [ADR-006](docs/adr/ADR-006-despliegue-del-portfolio.md) | Por qué el proyecto no se despliega en AWS |

> **ADR-004 falta.** Está citado desde el ADR-002 y el ADR-005 como la decisión
> de no delegar la generación de SQL al modelo, pero nunca se escribió. La
> decisión está tomada y aplicada en el código; lo que falta es el documento.

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
