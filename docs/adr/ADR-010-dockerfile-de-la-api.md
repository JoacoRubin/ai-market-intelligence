# ADR-010 — Dockerfile de la API, y qué NO se optimiza acá

- **Estado:** Aceptado
- **Fecha:** 2026-08-26

## Contexto

`docker-compose.yml` solo levantaba SQL Server. El agente entero —FastAPI,
LangGraph, el índice FAISS, el motor de forecast— corría directo en la
máquina, contra el `.venv` fuera de OneDrive. Fase 6 del roadmap pide
containerizar la app; esto es esa pieza, sola, sin Redis ni CI todavía.

## Decisión

Build multi-etapa: `builder` resuelve dependencias con `uv sync --frozen`,
`runtime` es la imagen que sirve tráfico. Ollama **no entra al compose** —
sigue siendo un requisito de la máquina host (README), y el container de la
API le habla por `host.docker.internal`.

## Justificación: por qué NO es la misma optimización que en rag-chatbot

El hermano de portfolio (`Proyecto DATA + IA`) usa el multi-stage para sacar
torch de la imagen final: exportó el encoder a ONNX, así que en runtime solo
necesita `onnxruntime`. Acá **no se hizo eso**, y no es un descuido: el
grupo `rag` de este proyecto usa `sentence-transformers` directo, y cada
consulta del usuario se embebe en caliente contra el índice FAISS — no solo
la construcción del índice. Torch **tiene que estar** en la imagen final
para que el RAG funcione en runtime. Migrar a ONNX sería una mejora real,
pero es un cambio de arquitectura del motor de embeddings, no de
containerización, y no es lo que se pidió acá.

Entonces, ¿para qué sirve el multi-stage si no saca torch? Para separar la
capa de dependencias (pesada, cambia poco: solo cuando cambia `uv.lock`) de
la capa de código (liviana, cambia en cada commit). Un rebuild que solo toca
`agent/nodes/router.py` no reinstala un solo paquete.

## Decisiones puntuales

**Ollama fuera del compose.** Meterlo adentro (imagen `ollama/ollama`, ~mismo
patrón que el otro servicio) sería más prolijo en apariencia, pero
`llama3.2:3b` en CPU ya está justificado en ADR-003 como decisión de
hardware — el modelo necesita la RAM/CPU de la máquina host directamente, no
la capa extra de virtualización de un container corriendo inferencia.
Queda como requisito documentado, igual que Docker Desktop o SQL Server.

**El índice FAISS y el cache de HuggingFace van por volumen, no por imagen.**
Son datos generados (`.\tasks.ps1 rag-build`, `.\tasks.ps1 rag-descargar`),
no código. Bakearlos en la imagen acoplaría el build de Docker a tener SQL
Server arriba en ese momento (el corpus sale de `seeds.generate`, no de la
base, así que técnicamente no hace falta — pero igual son artefactos que
cambian con el dataset y no con el código, y mezclar ambos ciclos de vida en
la misma imagen es la clase de acoplamiento que el proyecto ya evita en
otros lados). Sin el volumen montado, `cargar_indice()` devuelve `None` y el
agente degrada solo a análisis sin evidencia documental — la degradación ya
estaba implementada, este ADR no la agrega, solo la deja visible en Docker.

**ODBC Driver 18 instalado en la imagen final, no en el builder.** `pyodbc`
lo necesita en runtime, no en build. Instalarlo y purgar `curl`/`gnupg` en el
mismo `RUN` porque las capas de Docker son aditivas: limpiarlo en una capa
posterior no reduce el tamaño ya escrito.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| Imagen `python:3.13-alpine` | Alpine usa musl libc; `pyodbc`, `pandas` y `scikit-learn` tienen wheels manylinux (glibc) y en Alpine compilan desde source — minutos de más en cada build por unos MB menos de imagen. |
| Meter Ollama en el compose | Ver "Ollama fuera del compose" arriba. Además, el propio README ya lista Ollama como requisito de la máquina, igual que Docker Desktop — es coherente mantenerlo ahí y no mover el límite. |
| Generar el índice FAISS en build-time (dentro del Dockerfile) | Acoplaría el ciclo de vida de un dato (el índice) al de una imagen (el código). Un rebuild de código no debería regenerar embeddings, y un dataset nuevo no debería exigir un rebuild de imagen. |

## Consecuencias

**Positivas**
- El agente corre igual en la máquina de cualquiera que clone el repo y
  tenga Docker + Ollama + el `.env` completado — no más "en mi máquina sí".
- Cache de capas real: cambiar código no reinstala dependencias.
- `HEALTHCHECK` reusa `/health`, que ya reporta el estado de las
  dependencias reales (SQL Server, Ollama) — no es un ping falso al proceso.

**Negativas**
- La imagen sigue siendo pesada (torch + sentence-transformers en runtime).
  Si el tamaño se vuelve un problema real, la solución es la migración a
  ONNX que rag-chatbot ya probó — no otro truco de Dockerfile.
- Un tercer requisito de infraestructura (Docker Desktop) sobre los dos que
  ya existían (SQL Server en Docker, Ollama en el host). El README necesita
  reflejarlo.
