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

```bash
uv sync                      # núcleo: API + agente
uv sync --extra seed         # + generación del dataset sintético
uv sync --extra ml           # + scikit-learn y MLflow
uv sync --extra rag          # + FAISS y embeddings (pesado: arrastra torch)

ollama pull llama3.2:3b
```

Las dependencias están separadas en grupos a propósito: cada fase del roadmap
instala solo lo que necesita, y el repo se puede levantar por partes.

---

## Decisiones de arquitectura (ADRs)

| ADR | Decisión |
|---|---|
| [ADR-001](docs/adr/ADR-001-langgraph.md) | Por qué LangGraph y cuándo no usarlo |
| [ADR-002](docs/adr/ADR-002-datos.md) | SQL Server relacional + FAISS para vectores |
| [ADR-003](docs/adr/ADR-003-llm-local.md) | LLM local: elección de modelo con datos medidos |

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
